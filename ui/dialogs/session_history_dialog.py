"""历史记录列表弹窗"""

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    PrimaryPushButton,
    PushButton,
    TableWidget,
)


class SessionHistoryDialog(QDialog):
    """显示历史会话记录，并允许选择加载目标"""

    def __init__(self, session_root_path: Path, parent=None):
        super().__init__(parent)
        self.session_root_path = Path(session_root_path)
        self.selected_record_path: Path | None = None

        self.setWindowTitle('历史记录列表')
        self.setMinimumSize(800, 550)
        self.setWindowIcon(QIcon(':/image/image/update.svg'))

        # 使用 qfluentwidgets 的 TableWidget
        self.table = TableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            '创建时间', '来源文件', '条目数', '引擎', '分隔符', '记录 ID', '文件名'
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        
        # 设置列宽自适应：不同列使用不同模式
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        
        # 列配置：(列索引, ResizeMode)
        # 短内容列：ResizeToContents
        # 长内容列：Stretch
        resize_modes = [
            (0, QHeaderView.ResizeMode.ResizeToContents),  # 创建时间
            (1, QHeaderView.ResizeMode.Stretch),  # 来源文件
            (2, QHeaderView.ResizeMode.ResizeToContents),  # 条目数
            (3, QHeaderView.ResizeMode.ResizeToContents),  # 引擎
            (4, QHeaderView.ResizeMode.ResizeToContents),  # 分隔符
            (5, QHeaderView.ResizeMode.ResizeToContents),  # 记录 ID
            (6, QHeaderView.ResizeMode.ResizeToContents),  # 文件名
        ]
        
        for col, mode in resize_modes:
            header.setSectionResizeMode(col, mode)
        
        self.table.itemDoubleClicked.connect(self.accept_selected)

        # 使用 qfluentwidgets 的按钮
        self.refresh_button = PushButton('刷新', self)
        self.refresh_button.setIcon(QIcon(':/image/image/backward.svg'))
        self.open_button = PrimaryPushButton('加载选中记录', self)
        self.cancel_button = PushButton('取消', self)

        self.refresh_button.clicked.connect(self.reload_records)
        self.open_button.clicked.connect(self.accept_selected)
        self.cancel_button.clicked.connect(self.reject)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.setSpacing(10)
        button_layout.setContentsMargins(0, 10, 0, 0)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.table)
        main_layout.addLayout(button_layout)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        self.reload_records()

    def get_selected_record_path(self) -> Path | None:
        """返回已选中的记录路径"""
        return self.selected_record_path

    def reload_records(self):
        """重载会话记录列表"""
        self.table.setRowCount(0)
        self.selected_record_path = None

        if not self.session_root_path.exists():
            return

        record_files = sorted(
            self.session_root_path.glob('*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for record_file in record_files:
            item_data = self._read_one_record(record_file)
            self._append_row(item_data)

        if self.table.rowCount() > 0:
            self.table.selectRow(0)

    def accept_selected(self):
        """确认加载当前选中行"""
        row = self.table.currentRow()
        if row < 0:
            return

        # 从任意列获取存储的记录路径数据
        for col in range(self.table.columnCount()):
            path_item = self.table.item(row, col)
            if path_item is not None:
                record_path = path_item.data(Qt.ItemDataRole.UserRole)
                if record_path:
                    self.selected_record_path = Path(str(record_path))
                    self.accept()
                    return

    def _append_row(self, row_data: dict):
        """添加一行展示数据"""
        row = self.table.rowCount()
        self.table.insertRow(row)

        values = [
            row_data['created_at'],
            row_data['source_name'],
            str(row_data['items_count']),
            row_data['engine_mode'],
            row_data['mark'],
            row_data['session_id'],
            row_data['file_name'],
        ]

        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            # 在所有列存储路径数据，方便后续获取
            cell.setData(Qt.ItemDataRole.UserRole, str(row_data['file_path']))
            # 设置单元格水平和竖直居中
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, cell)

    def _read_one_record(self, record_path: Path) -> dict:
        """读取单条记录，解析展示信息，异常时回退默认值"""
        fallback_time = datetime.fromtimestamp(record_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        fallback = {
            'file_path': record_path,
            'file_name': record_path.name,
            'created_at': fallback_time,
            'source_name': '-',
            'items_count': 0,
            'engine_mode': '-',
            'mark': '-',
            'session_id': record_path.stem,
        }

        try:
            with record_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return fallback

        created_at_raw = str(data.get('created_at', '')).strip()
        if created_at_raw:
            created_at = created_at_raw.replace('T', ' ')
        else:
            created_at = fallback_time

        profile = data.get('generation_profile', {})
        mode = '-'
        if isinstance(profile, dict):
            mode = str(profile.get('mode', '-'))

        items = data.get('items', [])
        items_count = len(items) if isinstance(items, list) else 0

        # 优先使用source_file提取文件名（包含后缀），回退到source_name
        source_name = '-'
        source_file = data.get('source_file', '')
        if source_file:
            try:
                source_name = Path(source_file).name
            except Exception:
                source_name = str(source_file)
        if not source_name or source_name == '-':
            source_name = str(data.get('source_name', '-'))

        return {
            'file_path': record_path,
            'file_name': record_path.name,
            'created_at': created_at,
            'source_name': source_name,
            'items_count': items_count,
            'engine_mode': mode,
            'mark': str(data.get('mark', '-')),
            'session_id': str(data.get('session_id', record_path.stem)),
        }
