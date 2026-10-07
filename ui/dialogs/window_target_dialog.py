"""选择翻页目标窗口的对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
)

from app import window_target
from ui.dialogs.base import ThemedDialog


class WindowTargetDialog(ThemedDialog):
    """列出当前打开的窗口，供用户指定翻页按键的接收方。

    列表按窗口面积降序排列，放映中的全屏窗口通常位于最前。
    """

    def __init__(self, current=None, parent=None):
        super().__init__(parent)
        self.selected = dict(current) if isinstance(current, dict) else {}

        self.setWindowTitle('选择翻页目标')
        self.setMinimumSize(700, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel('选择翻页目标', self))

        tip = CaptionLabel(self)
        tip.setText('请在放映开始后选择放映窗口')
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.table = TableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['窗口标题', '程序', '窗口类'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        # 标题列最重要，占满剩余宽度；辅助列给固定初始宽度，可拖动
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(1, 150)
        header.resizeSection(2, 200)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.table, stretch=1)

        self.status_label = CaptionLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        refresh_button = PushButton('刷新', self)
        refresh_button.clicked.connect(self.reload)
        button_row.addWidget(refresh_button)

        clear_button = PushButton('不指定', self)
        clear_button.clicked.connect(self._clear_selection)
        button_row.addWidget(clear_button)
        button_row.addStretch(1)

        cancel_button = PushButton('取消', self)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        self.confirm_button = PrimaryPushButton('确定', self)
        self.confirm_button.clicked.connect(self.accept)
        button_row.addWidget(self.confirm_button)
        layout.addLayout(button_row)

        self.reload()

    def reload(self):
        """重新枚举当前打开的窗口。"""
        current_hwnd = int(self.selected.get('hwnd') or 0)
        self.table.setRowCount(0)

        for window in window_target.list_windows():
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (window['title'], window['process'], window['class_name'])
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, window)
                if col:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, cell)
            if window['hwnd'] == current_hwnd:
                self.table.selectRow(row)

        self._sync_buttons()

    def _current_window(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _clear_selection(self):
        """不指定任何窗口并关闭对话框。"""
        self.selected = {}
        super().accept()

    def _sync_buttons(self):
        """根据选中项更新提示与确定按钮状态。"""
        window = self._current_window()
        self.confirm_button.setEnabled(window is not None)
        if window is not None:
            self.status_label.setText(f'翻页将发送到：{window["title"]}')
        elif self.selected:
            remembered = self.selected.get('title') or self.selected.get('process') or '原目标'
            self.status_label.setText(f'上次选择的窗口不在列表中：{remembered}')
        else:
            self.status_label.setText('未选择窗口')

    def accept(self):
        window = self._current_window()
        if window is not None:
            self.selected = window_target.make_target(window)
        super().accept()
