"""更新控件状态，避免禁用时的焦点转移触发页面滚动。"""

from PySide6.QtWidgets import QApplication, QWidget


def set_enabled(widget: QWidget, enabled: bool):
    """禁用前清除控件及其子控件的焦点，保留页面滚动位置。"""
    if not enabled:
        focus = QApplication.focusWidget()
        if focus is not None and (focus is widget or widget.isAncestorOf(focus)):
            focus.clearFocus()
    widget.setEnabled(enabled)
