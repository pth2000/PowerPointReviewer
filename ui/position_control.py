"""讲稿进度的原位输入控件，点击组外或按 Tab 确认，Esc 取消。"""

import math

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QLineEdit, QWidget
from qfluentwidgets import ToolTipFilter, isDarkTheme, qconfig, themeColor


class PositionControl(QAbstractButton):
    position_requested = Signal(int)
    invalid_input = Signal(str)

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setText(label)
        self.value = self.total = 0
        self._editing = False
        self._initial_value = 0
        self._hovered = False
        self._show_focus = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(f'点击跳转到指定{label}')
        self.installEventFilter(ToolTipFilter(self))
        self.setAccessibleDescription('点击输入位置，点击组外或按回车确认，Esc 取消。')
        self.editor = QLineEdit(self)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editor.setAccessibleName(f'目标{label}')
        self.editor.setValidator(QIntValidator(1, 1, self.editor))
        self.editor.setCursor(Qt.CursorShape.IBeamCursor)
        self.editor.installEventFilter(self)
        self.editor.hide()
        self.editor.textChanged.connect(self._layout_editor)
        self.clicked.connect(self.begin_edit)
        qconfig.themeChanged.connect(self._update_editor_style)
        qconfig.themeColorChanged.connect(self._update_editor_style)
        self._update_editor_style()
        self.setEnabled(False)

    def set_position(self, value, total):
        if self._editing and total != self.total:
            self.cancel_edit()
        self.value, self.total = value, total
        self.editor.validator().setRange(1, max(1, total))
        self.setAccessibleName(f'{self.text()} {value}/{total}' if total else self.text())
        self.update()

    def content_width(self):
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Bold)
        label_width = QFontMetricsF(font).horizontalAdvance(self.text())
        value = f'{self.total}/{self.total}' if self.total else '—'
        return max(label_width, QFontMetricsF(self.font()).horizontalAdvance(value))

    def begin_edit(self):
        if self._editing:
            self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
            return
        if not self.isEnabled() or not self.total:
            return
        self._editing = True
        self._initial_value = self.value
        QApplication.instance().installEventFilter(self)
        self.editor.setText(str(self.value))
        self._layout_editor()
        self.editor.show()
        self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.editor.selectAll()
        self.update()

    def cancel_edit(self):
        self._finish_edit(confirm=False)

    def _finish_edit(self, *, confirm):
        if not self._editing:
            return
        text = self.editor.text().strip()
        self._editing = False
        QApplication.instance().removeEventFilter(self)
        self.editor.hide()
        self.update()
        if not confirm:
            return
        value = int(text) if text.isdecimal() else 0
        if not 1 <= value <= self.total:
            self.invalid_input.emit(f'{self.text()}应为 1 到 {self.total} 之间的整数。')
        elif value != self._initial_value:
            self.position_requested.emit(value)

    def eventFilter(self, watched, event):
        if self._editing:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                if watched is not self and not self.isAncestorOf(watched):
                    self._finish_edit(confirm=True)
            elif watched is self.editor:
                if event.type() == QEvent.Type.KeyPress:
                    if event.key() == Qt.Key.Key_Escape:
                        self.cancel_edit()
                        self.setFocus(Qt.FocusReason.OtherFocusReason)
                        return True
                    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        self._finish_edit(confirm=True)
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
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.cancel_edit()
        super().hideEvent(event)

    def focusInEvent(self, event):
        self._show_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._show_focus = False
        self.update()
        super().focusOutEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            if not self.isEnabled():
                self.cancel_edit()
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
            self.update()
        elif event.type() == QEvent.Type.FontChange:
            self.editor.setFont(self.font())
            self._layout_editor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_editor()

    def _update_editor_style(self, *_):
        foreground = '#eeeeee' if isDarkTheme() else '#000000'
        accent = themeColor().name()
        self.editor.setStyleSheet(
            f'QLineEdit {{ color: {foreground}; background: transparent; border: none; '
            f'border-bottom: 2px solid {accent}; border-radius: 0; padding: 0 3px; '
            f'selection-background-color: {accent}; selection-color: white; }}')
        self.update()

    def _value_geometry(self):
        metrics = QFontMetricsF(self.font())
        line_height = metrics.lineSpacing() + 2
        top = (self.height() - line_height * 2 - 4) / 2
        return metrics, line_height, top

    def _layout_editor(self, *_):
        if not self._editing:
            return
        metrics, line_height, top = self._value_geometry()
        available = max(1, self.width() - 8)
        minimum = metrics.horizontalAdvance('0') + 8
        suffix_width = min(metrics.horizontalAdvance(f'/{self.total}'), max(0, available - minimum))
        editor_width = min(metrics.horizontalAdvance(self.editor.text() or '0') + 8, available - suffix_width)
        x = (self.width() - editor_width - suffix_width) / 2
        self.editor.setGeometry(round(x), round(top + line_height), math.ceil(editor_width),
                                math.ceil(line_height + 8))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        if self.isEnabled() and (self._hovered or self._editing):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#393939' if dark else '#f0f0f0'))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 0, -1, 0), 4, 4)
        if self.hasFocus() and self._show_focus and not self._editing:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(themeColor(), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        metrics, line_height, top = self._value_geometry()
        color = QColor('#eeeeee' if dark else '#000000')
        if not self.isEnabled():
            # 与 Fluent 按钮的禁用文字透明度一致。
            color = QColor(255, 255, 255, 93) if dark else QColor(0, 0, 0, 92)
        painter.setPen(color)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(0, top, self.width(), line_height), Qt.AlignmentFlag.AlignCenter, self.text())
        painter.setFont(self.font())
        y = top + line_height + 4
        if self._editing:
            x = self.editor.geometry().right() + 1
            width = max(0, self.width() - 4 - x)
            suffix = metrics.elidedText(f'/{self.total}', Qt.TextElideMode.ElideRight, width)
            painter.drawText(QRectF(x, y, width, line_height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, suffix)
        else:
            value = f'{self.value}/{self.total}' if self.total else '—'
            value = metrics.elidedText(value, Qt.TextElideMode.ElideRight, max(1, self.width() - 8))
            painter.drawText(QRectF(0, y, self.width(), line_height), Qt.AlignmentFlag.AlignCenter, value)
