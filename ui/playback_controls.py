"""首页播放控制与倒计时快捷设置。"""

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QHBoxLayout, QLineEdit, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    SwitchButton,
    ToolTipFilter,
    TransparentToolButton,
    VerticalSeparator,
    isDarkTheme,
    qconfig,
    themeColor,
)

from app.audio.countdown import MAX_COUNTDOWN_SECONDS, MIN_COUNTDOWN_SECONDS
from ui import icons
from ui.playback_buttons import PlayButton, RoundPlaybackButton, StepPlaybackButton
from ui.tooltips import TextToolTipFilter
from ui.widget_state import set_enabled


class CountdownButton(RoundPlaybackButton):
    """复用圆形工具按钮，以图标颜色表示倒计时开关。"""

    checkedChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(FluentIcon.STOP_WATCH)
        self.setIconSize(QSize(16, 16))
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip('开始朗读前显示倒计时，每秒播放提示音')
        self.setAccessibleName('倒计时播放')
        self.toggled.connect(self.checkedChanged)
        self.installEventFilter(ToolTipFilter(self))
        qconfig.themeChanged.connect(self.update)
        qconfig.themeColorChanged.connect(self.update)

    def _drawIcon(self, icon, painter, rect, state=None):
        if self.isChecked() and self.isEnabled():
            accent = themeColor()
            color = accent.lighter(160) if isDarkTheme() else accent
            FluentIcon.STOP_WATCH.render(painter, rect, fill=color.name())
        else:
            super()._drawIcon(icon, painter, rect)


class CountdownDuration(QAbstractButton):
    """点击时长原位输入，点击组件外或按 Tab 确认，Esc 取消。"""

    valueChanged = Signal(int)
    invalid_input = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._remaining = None
        self._editing = False
        self._show_focus = False
        self.setFixedHeight(36)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip('点击修改倒计时时长')
        self.setAccessibleDescription(f'输入 {self.minimum()} 到 {self.maximum()} 秒，点击外部确认，Esc 取消。')
        self.installEventFilter(ToolTipFilter(self))
        self.editor = QLineEdit(self)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.editor.setValidator(QIntValidator(self.minimum(), self.maximum(), self.editor))
        self.editor.setAccessibleName('倒计时时长（秒）')
        self.editor.installEventFilter(self)
        self.editor.hide()
        self.clicked.connect(self.begin_edit)
        qconfig.themeChanged.connect(self._update_editor_style)
        qconfig.themeColorChanged.connect(self._update_editor_style)
        self._update_editor_style()
        self.setValue(0)

    def sizeHint(self):
        metrics = self.fontMetrics()
        width = self._unit_x() + metrics.horizontalAdvance('秒') + 6
        return QSize(width, 36)

    def minimum(self):
        return MIN_COUNTDOWN_SECONDS

    def maximum(self):
        return MAX_COUNTDOWN_SECONDS

    def value(self):
        return self._value

    def setValue(self, value):
        self._finish_edit(confirm=False)
        value = min(self.maximum(), max(self.minimum(), int(value)))
        changed = value != self._value
        self._value = value
        self._update_accessible_name()
        self.update()
        if changed:
            self.valueChanged.emit(value)

    def set_remaining(self, seconds):
        if seconds == self._remaining:
            return
        if seconds is not None:
            self._finish_edit(confirm=False)
            self._show_focus = False
        self._remaining = seconds
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if seconds is None else Qt.FocusPolicy.NoFocus)
        self.setToolTip('点击修改倒计时时长' if seconds is None else '')
        self._update_accessible_name()
        self.update()

    def begin_edit(self):
        if self._editing or self._remaining is not None or not self.isEnabled():
            return
        self._editing = True
        self.editor.setText(str(self._value))
        self._layout_editor()
        self.editor.show()
        self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.editor.selectAll()
        QApplication.instance().installEventFilter(self)
        self.update()

    def _finish_edit(self, *, confirm):
        if not self._editing:
            return
        text = self.editor.text().strip()
        self._editing = False
        QApplication.instance().removeEventFilter(self)
        self.editor.hide()
        self.update()
        if confirm:
            if text.isdecimal() and self.minimum() <= int(text) <= self.maximum():
                self.setValue(int(text))
            else:
                self.invalid_input.emit(f'倒计时时长应为 {self.minimum()} 到 {self.maximum()} 之间的整数。')

    def eventFilter(self, watched, event):
        if self._editing:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                if watched is not self and not self.isAncestorOf(watched):
                    self._finish_edit(confirm=True)
            elif watched is self.editor:
                if event.type() == QEvent.Type.KeyPress:
                    if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        self._finish_edit(confirm=event.key() != Qt.Key.Key_Escape)
                        self.setFocus(Qt.FocusReason.OtherFocusReason)
                        return True
                elif (event.type() == QEvent.Type.FocusOut
                      and event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)):
                    self._finish_edit(confirm=True)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.begin_edit()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event):
        self._show_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self._show_focus = False
        super().focusOutEvent(event)
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            if not self.isEnabled():
                self._finish_edit(confirm=False)
            self._update_accessible_name()
        elif event.type() == QEvent.Type.FontChange and hasattr(self, 'editor'):
            self.editor.setFont(self.font())
            self._layout_editor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_editor()

    def _layout_editor(self, *_):
        if self._editing:
            self.editor.setGeometry(2, 4, self._number_width(), 28)
            self.update()

    def _number_width(self):
        # 为两位数字、样式内边距和 QLineEdit 自身的文本边距预留空间。
        return self.fontMetrics().horizontalAdvance(str(self.maximum())) + 12

    def _unit_x(self):
        return 2 + self._number_width() + 3

    def hideEvent(self, event):
        self._finish_edit(confirm=False)
        super().hideEvent(event)

    def _update_accessible_name(self):
        if self._remaining is None:
            self.setAccessibleName(f'倒计时时长：{self._value} 秒')
        else:
            self.setAccessibleName(f'倒计时剩余：{self._remaining} 秒')

    def _update_editor_style(self, *_):
        color = '#f2f2f2' if isDarkTheme() else '#202020'
        accent = themeColor()
        selection = QColor(accent)
        selection.setAlpha(60 if isDarkTheme() else 35)
        self.editor.setStyleSheet(
            f'QLineEdit {{ color: {color}; background: transparent; border: none; '
            f'border-bottom: 1px solid {accent.name()}; border-radius: 0; padding: 0 3px; '
            f'selection-background-color: {selection.name(QColor.NameFormat.HexArgb)}; '
            f'selection-color: {color}; }}')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        if self.isEnabled() and self._remaining is None and (self.underMouse() or self._editing):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#393939' if dark else '#f0f0f0'))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 4, 0, -4), 4, 4)
        if self.hasFocus() and self._show_focus and not self._editing:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(themeColor(), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        secondary = QColor('#b4b4b4' if dark else '#747474')
        alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if not self.isEnabled() and self._remaining is None:
            painter.setOpacity(0.36)
        if not self._editing:
            number = str(self._value if self._remaining is None else self._remaining)
            painter.setPen(QColor('#f2f2f2' if dark else '#202020'))
            painter.drawText(QRectF(2, 0, self._number_width() - 5, 36),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number)
        painter.setPen(secondary)
        unit_x = self._unit_x()
        painter.drawText(QRectF(unit_x, 0, self.width() - unit_x, 36), alignment, '秒')


class PlaybackControls(CardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._playback_active = False
        self._countdown_remaining = None
        self.setFixedHeight(80)
        self.setMinimumWidth(640)
        self.countdown_button = CountdownButton(self)
        self.countdown_button.setObjectName('countdownButton')
        self.countdown_label = BodyLabel('倒计时', self)
        self.countdown_label.setFixedHeight(36)
        self.countdown_label.setAccessibleDescription('点击启停倒计时')
        self.countdown_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.countdown_label.setToolTip(self.countdown_button.toolTip())
        self.countdown_label.installEventFilter(ToolTipFilter(self.countdown_label))
        self.countdown_label.installEventFilter(self)
        self.countdown_button.installEventFilter(self)
        self.previous_button = StepPlaybackButton(True, self)
        self.next_button = StepPlaybackButton(False, self)
        self.play_button = PlayButton(self)
        self.reset_button = TransparentToolButton(icons.SystemIcon.RESET, self)
        self.reset_button.setFixedSize(32, 36)
        self.reset_button.setIconSize(QSize(15, 15))
        self.divider = VerticalSeparator(self)
        self.divider.setFixedHeight(18)
        self.return_button = TransparentToolButton(icons.SystemIcon.UNDO, self)
        self.return_button.setFixedSize(32, 36)
        self.return_button.setIconSize(QSize(15, 15))
        self.return_button.setEnabled(False)
        self.left_divider = VerticalSeparator(self)
        self.left_divider.setFixedHeight(18)

        self.sync_group = QWidget(self)
        row = QHBoxLayout(self.sync_group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.sync_label = BodyLabel('同步翻页', self.sync_group)
        self._update_label_state(self.countdown_label, True)
        self._update_label_state(self.sync_label, True)
        self.sync_switch = self._switch(self.sync_group)
        self.sync_switch.setAccessibleName('同步翻页')
        self.sync_switch.indicator.setAccessibleName('同步翻页')
        for widget in (self.sync_label, self.sync_switch.indicator):
            widget.installEventFilter(TextToolTipFilter(widget))
        self.sync_switch.installEventFilter(self)
        row.addWidget(self.sync_label)
        row.addWidget(self.sync_switch)

        self.countdown_switch = self.countdown_button
        self.countdown_seconds = CountdownDuration(self)
        self.countdown_seconds.setFont(self.sync_label.font())
        self.countdown_switch.checkedChanged.connect(self.refresh_countdown)
        self.countdown_seconds.valueChanged.connect(self.refresh_countdown)
        self.refresh_countdown()
        for button, name in ((self.return_button, '返回刚才的位置'),
                             (self.previous_button, '上一页'), (self.next_button, '下一页'),
                             (self.reset_button, '回到讲稿开头'), (self.play_button, '播放')):
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.installEventFilter(ToolTipFilter(button))
        for first, second in ((self.countdown_button, self.countdown_seconds),
                              (self.countdown_seconds, self.return_button),
                              (self.return_button, self.previous_button),
                              (self.previous_button, self.play_button),
                              (self.play_button, self.next_button),
                              (self.next_button, self.reset_button),
                              (self.reset_button, self.sync_switch.indicator)):
            QWidget.setTabOrder(first, second)

    @staticmethod
    def _switch(parent):
        control = SwitchButton(parent)
        control.setOnText('')
        control.setOffText('')
        return control

    def refresh_countdown(self, *_):
        enabled = self.countdown_switch.isChecked()
        counting = self._countdown_remaining is not None
        set_enabled(self.countdown_seconds, (enabled or counting) and self.countdown_button.isEnabled())
        self.countdown_seconds.set_remaining(self._countdown_remaining)
        self.countdown_seconds.setVisible(counting or (enabled and not self._playback_active))
        self.countdown_button.update()
        self._layout_controls()

    def set_sync_hint(self, text):
        for widget in (self.sync_group, self.sync_label, self.sync_switch, self.sync_switch.indicator):
            widget.setToolTip(text)

    def set_countdown_progress(self, *, playing, remaining=None):
        if (playing, remaining) == (self._playback_active, self._countdown_remaining):
            return
        self._playback_active = playing
        self._countdown_remaining = remaining
        self.refresh_countdown()

    def set_countdown_available(self, available, *, locked=False):
        set_enabled(self.countdown_button, available and not locked)
        hint = '请先停止播放，再修改此设置。' if locked else '开始朗读前显示倒计时，每秒播放提示音'
        self.countdown_button.setToolTip(hint)
        self.countdown_label.setToolTip(hint)
        self.refresh_countdown()

    def _layout_controls(self):
        if not hasattr(self, 'countdown_seconds'):
            return
        self.countdown_label.adjustSize()
        self.countdown_seconds.resize(self.countdown_seconds.sizeHint())
        countdown_width = (self.countdown_button.width() + 8 + self.countdown_label.width()
                           + 4 + self.countdown_seconds.width())
        self.sync_group.adjustSize()
        self.setMinimumWidth(max(640, 2 * (self.sync_group.width() + 162),
                                 2 * (countdown_width + 158)))
        center = self.width() // 2
        self.countdown_button.move(16, 22)
        self.countdown_label.move(self.countdown_button.geometry().right() + 9,
                                  (80 - self.countdown_label.height()) // 2)
        self.countdown_seconds.move(self.countdown_label.geometry().right() + 5, 22)
        self.play_button.move(center - 22, 18)
        self.previous_button.move(center - 72, 22)
        self.next_button.move(center + 36, 22)
        self.return_button.move(center - 130, 22)
        self.left_divider.move(center - 91, 31)
        self.divider.move(center + 88, 31)
        self.reset_button.move(center + 98, 22)
        self.sync_group.move(self.width() - self.sync_group.width() - 24,
                             (80 - self.sync_group.height()) // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_controls()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._layout_controls()

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'sync_switch', None) and event.type() == QEvent.Type.EnabledChange:
            self._update_label_state(self.sync_label, self.sync_switch.isEnabled())
        elif obj is getattr(self, 'countdown_button', None) and event.type() == QEvent.Type.EnabledChange:
            enabled = self.countdown_button.isEnabled()
            self._update_label_state(self.countdown_label, enabled)
            self.countdown_label.setCursor(
                Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        elif (obj is getattr(self, 'countdown_label', None)
              and event.type() == QEvent.Type.MouseButtonRelease
              and event.button() == Qt.MouseButton.LeftButton
              and self.countdown_label.rect().contains(event.position().toPoint())
              and self.countdown_button.isEnabled()):
            self.countdown_button.setFocus(Qt.FocusReason.MouseFocusReason)
            self.countdown_button.click()
            return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _update_label_state(label, enabled):
        label.setEnabled(enabled)
        label.setTextColor('#000000' if enabled else '#a0a0a0',
                           '#f2f2f2' if enabled else '#808080')

