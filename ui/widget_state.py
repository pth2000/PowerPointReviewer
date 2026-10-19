"""管理控件启用状态，并识别主窗口内的模态弹窗。"""

from PySide6.QtWidgets import QApplication, QDialog, QWidget


def active_modal_dialog(window):
    """同时识别独立模态窗口和 Fluent 嵌在主窗口内的遮罩弹窗。"""
    dialog = QApplication.activeModalWidget()
    if dialog is not None:
        return dialog
    if isinstance(window, QWidget):
        for dialog in reversed(window.findChildren(QDialog)):
            if dialog.isVisible() and dialog.isModal():
                return dialog
    return None


def set_enabled(widget: QWidget, enabled: bool):
    """禁用前清除控件及其子控件的焦点，保留页面滚动位置。"""
    if not enabled:
        focus = QApplication.focusWidget()
        if focus is not None and (focus is widget or widget.isAncestorOf(focus)):
            focus.clearFocus()
    widget.setEnabled(enabled)
