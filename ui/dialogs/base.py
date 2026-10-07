"""独立对话框的公共基类。"""

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDialog
from qfluentwidgets import qconfig

from app import theme


class ThemedDialog(QDialog):
    """背景与系统标题栏跟随应用明暗主题的对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        qconfig.themeChanged.connect(self._on_theme_changed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.window_background())

    def showEvent(self, event):
        self._apply_title_bar_theme()
        super().showEvent(event)

    def _on_theme_changed(self, *_):
        self._apply_title_bar_theme()
        self.update()

    def _apply_title_bar_theme(self):
        """设置系统标题栏的明暗。"""
        theme.apply_title_bar_theme(self)
