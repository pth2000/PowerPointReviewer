"""浏览历史会话，并管理其音频缓存。"""

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
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)

from app import icons, audio_cache
from ui.dialogs.delete_record_dialog import DeleteRecordMessageBox


class SessionHistoryDialog(QDialog):
    """列出历史会话，并集中处理加载、批量删除和缓存清理。"""

    def __init__(self, session_root_path: Path, parent=None):
        super().__init__(parent)
        self.session_root_path = Path(session_root_path)
        self.selected_record_path: Path | None = None

        self.setWindowTitle('历史记录列表')
        self.setMinimumSize(800, 550)
        self.setWindowIcon(QIcon(':/image/image/update.svg'))

        # 使用 Fluent 表格保持与应用主题一致。
        self.table = TableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            '创建时间', '来源文件', '条目数', '引擎', '发音人', '分隔符', '记录 ID'
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 扩展选择支持 Ctrl、Shift 和“全选”批量删除。
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        
        # 短字段按内容适配，来源和发音人等长字段占用剩余宽度。
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        
        # 每列单独声明尺寸策略，避免短字段浪费空间。
        resize_modes = [
            (0, QHeaderView.ResizeMode.ResizeToContents),  # 创建时间
            (1, QHeaderView.ResizeMode.Stretch),  # 来源文件
            (2, QHeaderView.ResizeMode.ResizeToContents),  # 条目数
            (3, QHeaderView.ResizeMode.ResizeToContents),  # 引擎
            (4, QHeaderView.ResizeMode.Stretch),  # 发音人
            (5, QHeaderView.ResizeMode.ResizeToContents),  # 分隔符
            (6, QHeaderView.ResizeMode.ResizeToContents),  # 记录 ID
        ]
        
        for col, mode in resize_modes:
            header.setSectionResizeMode(col, mode)
        
        self.table.itemDoubleClicked.connect(self.accept_selected)
        self.table.itemSelectionChanged.connect(self._update_action_buttons_state)

        # 历史记录只保存缓存索引，因此记录管理与缓存维护放在同一界面。
        self.cache_label = CaptionLabel(self)
        self.clean_button = PushButton('清理未引用音频', self)
        self.clean_button.clicked.connect(self.clean_orphan_audio)

        cache_layout = QHBoxLayout()
        cache_layout.addWidget(self.cache_label)
        cache_layout.addStretch(1)
        cache_layout.addWidget(self.clean_button)
        cache_layout.setSpacing(10)

        self.refresh_button = PushButton('刷新', self)
        icons.apply(self.refresh_button, ':/image/image/backward.svg')
        self.select_all_button = PushButton('全选', self)
        self.open_button = PrimaryPushButton('加载选中记录', self)
        self.delete_button = PushButton('删除选中记录', self)
        self.cancel_button = PushButton('关闭', self)

        self.refresh_button.clicked.connect(self.reload_records)
        self.select_all_button.clicked.connect(self.select_all_records)
        self.open_button.clicked.connect(self.accept_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.cancel_button.clicked.connect(self.reject)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.select_all_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.delete_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.setSpacing(10)
        button_layout.setContentsMargins(0, 10, 0, 0)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.table)
        main_layout.addLayout(cache_layout)
        main_layout.addLayout(button_layout)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        self.reload_records()
        self._update_action_buttons_state()
        self.refresh_cache_label()

    def refresh_cache_label(self):
        """刷新缓存文件数量和磁盘占用摘要。"""
        try:
            self.cache_label.setText(f'音频缓存：{audio_cache.summary()}')
        except Exception as e:
            print(f'[历史记录] 缓存统计失败：{e}')
            self.cache_label.setText('音频缓存：统计失败')

    def clean_orphan_audio(self):
        """确认后删除未被任何历史记录引用的音频。"""
        try:
            removed, freed = audio_cache.clear_orphans()
        except Exception as e:
            self._show_error('清理失败', f'详情：{e}')
            return

        self.refresh_cache_label()
        if removed:
            self._show_success(
                '清理完成', f'已删除 {removed} 条未引用音频，释放 {audio_cache.format_size(freed)}')
        else:
            self._show_success('无需清理', '当前没有未被引用的音频缓存')

    def get_selected_record_path(self) -> Path | None:
        """返回单条已选记录路径，多选或未选时返回 ``None``。"""
        return self.selected_record_path

    def reload_records(self):
        """重新扫描会话目录，并按时间填充记录表格。"""
        self.table.setRowCount(0)
        self.selected_record_path = None

        if not self.session_root_path.exists():
            self._update_action_buttons_state()
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

        self._update_action_buttons_state()

    def select_all_records(self):
        """选中表格中的全部历史记录。"""
        if self.table.rowCount():
            self.table.selectAll()

    def selected_record_paths(self) -> list:
        """按表格顺序返回已选行对应的去重记录路径。"""
        paths_list = []
        for index in sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row()):
            item = self.table.item(index.row(), 0)
            if item is None:
                continue
            raw_path = item.data(Qt.ItemDataRole.UserRole)
            if raw_path:
                paths_list.append(Path(str(raw_path)))
        return paths_list

    def _update_action_buttons_state(self):
        """根据记录数量和选择数量更新操作按钮。"""
        has_rows = self.table.rowCount() > 0
        selected_count = len(self.table.selectionModel().selectedRows()) if has_rows else 0
        self.select_all_button.setEnabled(has_rows)
        # 加载只接受单条记录，删除则支持批量选择。
        self.open_button.setEnabled(selected_count == 1)
        self.delete_button.setEnabled(selected_count > 0)
        self.delete_button.setText(
            f'删除选中记录（{selected_count}）' if selected_count > 1 else '删除选中记录')

    def accept_selected(self):
        """验证单选记录存在后确认关闭弹窗。"""
        row = self.table.currentRow()
        if row < 0:
            return

        # 每个单元格都携带相同路径，任意选中列都可定位记录。
        for col in range(self.table.columnCount()):
            path_item = self.table.item(row, col)
            if path_item is not None:
                record_path = path_item.data(Qt.ItemDataRole.UserRole)
                if record_path:
                    self.selected_record_path = Path(str(record_path))
                    self.accept()
                    return

    def delete_selected(self):
        """删除选中记录，并按用户选择清理其独占音频。"""
        record_paths = [path for path in self.selected_record_paths() if path.exists()]
        if not record_paths:
            self.reload_records()
            self._show_warning('未选择记录', '请先在列表中选中要删除的历史记录')
            return

        # 仅统计目标记录独占缓存，避免破坏仍保留记录的音频引用。
        exclusive = audio_cache.exclusive_keys(record_paths)
        audio_count, audio_bytes = audio_cache.measure_keys(exclusive)

        target_text = (f'"{record_paths[0].name}"' if len(record_paths) == 1
                       else f'选中的 {len(record_paths)} 条历史记录')
        confirm = DeleteRecordMessageBox(
            target_text, audio_count, audio_cache.format_size(audio_bytes), self)
        if not confirm.exec():
            return

        delete_audio = confirm.should_delete_audio()

        deleted = []
        failed = []
        for record_path in record_paths:
            try:
                record_path.unlink()
                deleted.append(record_path)
            except Exception as e:
                print(f'[历史记录] 删除失败 {record_path.name}：{e}')
                failed.append(record_path.name)

        removed_audio, freed = (0, 0)
        if delete_audio and exclusive and deleted:
            try:
                removed_audio, freed = audio_cache.remove_keys(exclusive)
            except Exception as e:
                print(f'[历史记录] 删除音频失败：{e}')

        if self.selected_record_path in deleted:
            self.selected_record_path = None

        self.reload_records()
        self.refresh_cache_label()

        if failed:
            self._show_error('部分删除失败', f'成功 {len(deleted)} 条，失败 {len(failed)} 条：{"、".join(failed[:5])}')
            return

        detail = (f'已删除：{deleted[0].name}' if len(deleted) == 1
                  else f'已删除 {len(deleted)} 条历史记录')
        if removed_audio:
            detail += f'，并清理 {removed_audio} 条音频（{audio_cache.format_size(freed)}）'
        self._show_success('删除成功', detail)

    def _append_row(self, row_data: dict):
        """将一条规范化记录信息追加到表格。"""
        row = self.table.rowCount()
        self.table.insertRow(row)

        values = [
            row_data['created_at'],
            row_data['source_name'],
            str(row_data['items_count']),
            row_data['engine_mode'],
            row_data['speaker'],
            row_data['mark'],
            row_data['session_id'],
        ]

        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            # 路径附加到每个单元格，用户选中任意列都能解析记录。
            cell.setData(Qt.ItemDataRole.UserRole, str(row_data['file_path']))
            # 统一居中短字段，保持表格视觉节奏。
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, cell)

    def _read_one_record(self, record_path: Path) -> dict:
        """读取一条会话记录，并在字段缺失时生成可展示的默认值。"""
        fallback_time = datetime.fromtimestamp(record_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        fallback = {
            'file_path': record_path,
            'file_name': record_path.name,
            'created_at': fallback_time,
            'source_name': '-',
            'items_count': 0,
            'engine_mode': '-',
            'speaker': '-',
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

        speaker = str(data.get('speaker', '')).strip() or '-'

        items = data.get('items', [])
        items_count = len(items) if isinstance(items, list) else 0

        # source_file 可保留扩展名；旧记录缺失时再回退到 source_name。
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
            'speaker': speaker,
            'mark': str(data.get('mark', '-')),
            'session_id': str(data.get('session_id', record_path.stem)),
        }

    def _show_success(self, title: str, content: str):
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2200,
            parent=self,
        )

    def _show_warning(self, title: str, content: str):
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _show_error(self, title: str, content: str):
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self,
        )
