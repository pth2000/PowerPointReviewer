"""应用内统一使用 Fluent 悬浮提示，兼容普通控件、模型条目与自绘提示。"""

from dataclasses import dataclass
from weakref import WeakSet

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QMenu, QWidget
from qfluentwidgets import ToolTip, ToolTipFilter, ToolTipPosition
from qfluentwidgets.components.widgets.tool_tip import ItemViewToolTipDelegate
from shiboken6 import isValid


def _configure_text(tooltip, widget):
    tooltip.label.setTextFormat(Qt.TextFormat.PlainText)
    tooltip.label.setWordWrap(True)
    available = widget.screen().availableGeometry().width() - 80
    tooltip.label.setMaximumWidth(min(560, max(120, available)))


class TextToolTipFilter(ToolTipFilter):
    def _createToolTip(self):
        tooltip = super()._createToolTip()
        _configure_text(tooltip, self.parent())
        return tooltip


@dataclass(frozen=True)
class _Target:
    widget: QWidget
    rect: QRect
    text: str


class FluentToolTips(QObject):
    """按需处理原生提示，不替换列表委托，也不扫描或改写控件树。"""

    def __init__(self, app):
        super().__init__(app)
        self._target = None
        self._tooltip = None
        self._anchor = None
        self._views = WeakSet()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._show)
        app.aboutToQuit.connect(self.hide)

    @staticmethod
    def _filters(widget):
        return widget.findChildren(ToolTipFilter, options=Qt.FindChildOption.FindDirectChildrenOnly)

    def _local_handler(self, widget):
        while widget is not None:
            filters = self._filters(widget)
            if any(isinstance(item, ItemViewToolTipDelegate) for item in filters):
                return 'item'
            if widget.toolTip():
                return 'widget' if filters and widget.isEnabled() else None
            if widget.isWindow():
                break
            widget = widget.parentWidget()
        return None

    def _watch_view(self, view):
        if view in self._views:
            return
        self._views.add(view)
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        for bar in (view.horizontalScrollBar(), view.verticalScrollBar()):
            bar.valueChanged.connect(self.hide)
        if view.model() is not None:
            view.model().modelReset.connect(self.hide)
            view.model().layoutChanged.connect(self.hide)

    def _resolve(self, widget, point):
        parent = widget.parentWidget()
        if isinstance(parent, QAbstractItemView) and widget is parent.viewport():
            self._watch_view(parent)
            if isinstance(parent, QHeaderView):
                number = parent.logicalIndexAt(point)
                if number >= 0 and parent.model() is not None:
                    value = parent.model().headerData(number, parent.orientation(), Qt.ItemDataRole.ToolTipRole)
                    if value:
                        start, size = parent.sectionViewportPosition(number), parent.sectionSize(number)
                        rect = (QRect(start, 0, size, widget.height()) if parent.orientation() == Qt.Orientation.Horizontal
                                else QRect(0, start, widget.width(), size))
                        return _Target(widget, rect, str(value))
            else:
                index = parent.indexAt(point)
                value = index.data(Qt.ItemDataRole.ToolTipRole) if index.isValid() else None
                if value:
                    return _Target(widget, parent.visualRect(index), str(value))
        if isinstance(widget, QMenu) and widget.toolTipsVisible():
            action = widget.actionAt(point)
            if action is not None and action.toolTip():
                return _Target(widget, widget.actionGeometry(action), action.toolTip())
        while widget is not None:
            if widget.toolTip():
                return _Target(widget, widget.rect(), widget.toolTip())
            if widget.isWindow():
                break
            widget = widget.parentWidget()
        return None

    def hide(self, *_):
        self.timer.stop()
        self._target = None
        if self._tooltip is not None and isValid(self._tooltip):
            self._tooltip.hide()

    def _set_target(self, target, *, immediate=False):
        if target is None:
            self.hide()
            return
        if target == self._target:
            if immediate:
                self.timer.stop()
                self._show()
            return
        self._target = target
        if self._tooltip is not None and isValid(self._tooltip) and self._tooltip.isVisible():
            self._show()
        elif immediate:
            self._show()
        else:
            self.timer.start(300)

    def _show(self):
        self.timer.stop()
        target = self._target
        if target is None or not isValid(target.widget) or not target.widget.isVisible():
            self.hide()
            return
        window = target.widget.window()
        if (self._tooltip is None or not isValid(self._tooltip)
                or self._tooltip.parentWidget() is not window):
            if self._tooltip is not None and isValid(self._tooltip):
                self._tooltip.hide()
                self._tooltip.deleteLater()
            if self._anchor is not None and isValid(self._anchor):
                self._anchor.deleteLater()
            self._tooltip = ToolTip(parent=window)
            self._tooltip.setDuration(-1)
            self._anchor = QWidget(window)
            self._anchor.hide()
        _configure_text(self._tooltip, target.widget)
        self._tooltip.setText(target.text)
        origin = window.mapFromGlobal(target.widget.mapToGlobal(target.rect.topLeft()))
        self._anchor.setGeometry(QRect(origin, target.rect.size()))
        self._tooltip.adjustPos(self._anchor, ToolTipPosition.TOP)
        if not self._tooltip.isVisible():
            self._tooltip.show()

    def _refresh_local_text(self, widget):
        for handler in self._filters(widget):
            tooltip = handler._tooltip
            if not widget.toolTip():
                handler.hideToolTip()
            elif tooltip is not None and isValid(tooltip) and tooltip.isVisible():
                handler.showToolTip()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Type.ApplicationDeactivate, QEvent.Type.MouseButtonPress,
                    QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self.hide()
        if not isinstance(obj, QWidget) or not isValid(obj) or isinstance(obj.window(), ToolTip):
            return False
        if kind == QEvent.Type.ToolTipChange:
            self._refresh_local_text(obj)
            if self._target is not None and self._target.widget is obj:
                self._set_target(self._resolve(obj, QPoint()))
        elif kind in (QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.Leave):
            target = self._target
            if target is not None and (not isValid(target.widget) or obj is target.widget
                                       or obj.isAncestorOf(target.widget)):
                self.hide()
        elif kind in (QEvent.Type.Enter, QEvent.Type.MouseMove, QEvent.Type.ToolTip):
            handler = self._local_handler(obj)
            if handler is not None:
                self.hide()
                # 条目提示仍交给 Fluent 的 helpEvent，普通控件只拦截原生提示事件。
                return kind == QEvent.Type.ToolTip and handler != 'item'
            if kind == QEvent.Type.MouseMove and event.buttons() != Qt.MouseButton.NoButton:
                self.hide()
                return False
            point = event.pos() if kind == QEvent.Type.ToolTip else event.position().toPoint()
            self._set_target(self._resolve(obj, point), immediate=kind == QEvent.Type.ToolTip)
            return kind == QEvent.Type.ToolTip
        return False


def install_fluent_tooltips(app=None):
    """为现有及后续创建的窗口安装统一提示策略。"""
    app = app or QApplication.instance()
    manager = getattr(app, '_fluent_tooltips', None)
    if manager is None or not isValid(manager):
        manager = FluentToolTips(app)
        app._fluent_tooltips = manager
        app.installEventFilter(manager)
    return manager
