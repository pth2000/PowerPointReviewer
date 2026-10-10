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
from ui.dialogs.window_preview import WindowPreview


class WindowTargetDialog(ThemedDialog):
    """列出当前打开的窗口，供用户指定翻页按键的接收方。

    列表按窗口面积降序排列，放映中的全屏窗口通常位于最前。
    """

    def __init__(self, current=None, parent=None, recording=False, presentation=None):
        super().__init__(parent)
        self.selected = dict(current) if isinstance(current, dict) else {}
        self.recording = recording
        self.presentation = presentation
        self._valid_window = False
        self._preview_ready = False

        title = '选择录制窗口' if recording else '选择翻页目标'
        self.setWindowTitle(title)
        self.setMinimumSize(700, 480)
        if recording:
            self.setMinimumHeight(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(SubtitleLabel(title, self))

        tip = CaptionLabel(self)
        tip.setText('请选择与当前文稿对应的全屏幻灯片画面。此选择仅改变录制画面，'
                    '翻页和动画仍由当前文稿控制；演讲者控制台、阅读和编辑窗口暂不支持。'
                    if recording else '请在放映开始后选择放映窗口')
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

        self.preview = None
        if recording:
            self.preview = WindowPreview(self)
            self.preview.readyChanged.connect(self._preview_ready_changed)
            layout.addWidget(self.preview)

        self.status_label = CaptionLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        refresh_button = PushButton('刷新', self)
        refresh_button.clicked.connect(self.reload)
        button_row.addWidget(refresh_button)

        clear_button = PushButton('不指定', self)
        clear_button.clicked.connect(self._clear_selection)
        if not recording:
            button_row.addWidget(clear_button)
        else:
            clear_button.hide()
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

        for window in window_target.list_windows(include_untitled=self.recording):
            processes = getattr(self.presentation, 'capture_processes', None)
            if processes is not None and window['process'].casefold() not in processes:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (window['title'] or '（无标题窗口）', window['process'], window['class_name'])
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
        self._valid_window = False
        self._preview_ready = False
        self.confirm_button.setEnabled(window is not None and not self.recording)
        if self.preview is not None:
            self.preview.stop()
        if window is not None:
            if self.recording:
                try:
                    validator = getattr(self.presentation, 'validate_capture_window', None)
                    if validator is not None:
                        validator(window)
                    self._valid_window = True
                except Exception as exc:
                    self.status_label.setText(str(exc))
                    self.preview.stop(str(exc))
                    return
            action = '录制窗口' if self.recording else '翻页将发送到'
            title = window['title'] or window['process'] or '无标题窗口'
            self.status_label.setText(f'{action}：{title}')
            if self.preview is not None:
                self.preview.show_target(window)
        elif self.selected:
            remembered = self.selected.get('title') or self.selected.get('process') or '原目标'
            self.status_label.setText(f'上次选择的窗口不在列表中：{remembered}')
        else:
            self.status_label.setText('未选择窗口')

    def accept(self):
        window = self._current_window()
        if window is None or (self.recording and not (self._valid_window and self._preview_ready)):
            return
        if window is not None:
            self.selected = dict(window) if self.recording else window_target.make_target(window)
        super().accept()

    def _preview_ready_changed(self, ready):
        self._preview_ready = ready
        self.confirm_button.setEnabled(ready and self._valid_window and self._current_window() is not None)

    def done(self, result):
        if self.preview is not None:
            self.preview.stop()
        super().done(result)
