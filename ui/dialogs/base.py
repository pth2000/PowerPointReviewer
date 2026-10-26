"""独立对话框的公共基类。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDialog, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import qconfig

from ui import theme


class ThemedDialog(QDialog):
    """背景与系统标题栏跟随应用明暗主题的对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        qconfig.themeChanged.connect(self._on_theme_changed)

    def create_form_layout(self, width, margins, spacing, *, fixed_height=True):
        """小型表单按内容确定尺寸，禁用最大化和手动缩放。"""
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint, True)
        self.setSizeGripEnabled(False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.form_column = QWidget(self)
        self.form_column.setFixedWidth(width)
        outer.addWidget(self.form_column)
        if fixed_height:
            outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        layout = QVBoxLayout(self.form_column)
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        return layout

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
