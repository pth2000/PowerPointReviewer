"""浏览历史会话，并管理其音频缓存。"""

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
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

from ui import icons
from app.audio import cache as audio_cache
from app.presentation import link as presentation_link
from ui.dialogs.delete_record_dialog import DeleteRecordMessageBox
from ui.dialogs.base import ThemedDialog
from ui.dialogs.presentation_link_dialog import PresentationLinkDialog


class SessionHistoryDialog(ThemedDialog):
    """列出历史会话，并集中处理加载、批量删除和缓存清理。"""

    presentation_link_changed = Signal(object, object)

    def __init__(self, session_root_path: Path, parent=None):
        super().__init__(parent)
        protector = getattr(parent, 'protected_audio_keys', None)
        self.active_cache_keys = (protector() if callable(protector)
                                  else set(getattr(parent, 'note_cache_keys', ()) or ()))
        self.session_root_path = Path(session_root_path)
        self.selected_record_path: Path | None = None

        self.setWindowTitle('历史记录列表')
        self.setMinimumSize(800, 550)
        self.setWindowIcon(QIcon(':/image/image/update.svg'))

        # 使用 Fluent 表格保持与应用主题一致。
        self.table = TableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            '更新时间', '来源文件', '配音段数', '引擎', '发音人', '分隔符', 'PPT'
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
            (0, QHeaderView.ResizeMode.ResizeToContents),  # 更新时间
            (1, QHeaderView.ResizeMode.Stretch),  # 来源文件
            (2, QHeaderView.ResizeMode.ResizeToContents),  # 条目数
            (3, QHeaderView.ResizeMode.ResizeToContents),  # 引擎
            (4, QHeaderView.ResizeMode.Stretch),  # 发音人
            (5, QHeaderView.ResizeMode.ResizeToContents),  # 分隔符
            (6, QHeaderView.ResizeMode.ResizeToContents),  # PPT 关联状态
        ]
        
        for col, mode in resize_modes:
            header.setSectionResizeMode(col, mode)
        
        self.table.itemDoubleClicked.connect(self.accept_selected)
        self.table.itemSelectionChanged.connect(self._update_action_buttons_state)

        # 历史记录只保存缓存索引，因此记录管理与缓存维护放在同一界面。
        self.cache_label = CaptionLabel(self)
        self.clean_button = PushButton('清理闲置音频', self)
        self.clean_button.clicked.connect(self.clean_orphan_audio)

        cache_layout = QHBoxLayout()
        cache_layout.addWidget(self.cache_label)
        cache_layout.addStretch(1)
        cache_layout.addWidget(self.clean_button)
        cache_layout.setSpacing(10)

        self.refresh_button = PushButton('刷新', self)
        icons.apply(self.refresh_button, ':/image/image/backward.svg')
        self.select_all_button = PushButton('全选', self)
        self.link_ppt_button = PushButton('关联 PPT', self)
        self.link_ppt_button.setToolTip('为选中记录选择对应文稿，保留已有讲稿和配音')
        self.open_button = PrimaryPushButton('加载选中记录', self)
        self.delete_button = PushButton('删除选中记录', self)
        self.cancel_button = PushButton('关闭', self)

        self.refresh_button.clicked.connect(self.reload_records)
        self.select_all_button.clicked.connect(self.select_all_records)
        self.link_ppt_button.clicked.connect(self.associate_selected)
        self.open_button.clicked.connect(self.accept_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.cancel_button.clicked.connect(self.reject)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.select_all_button)
        button_layout.addWidget(self.link_ppt_button)
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
            removed, freed = audio_cache.clear_orphans(self.active_cache_keys)
        except Exception as e:
            self._show_error('清理失败', f'详情：{e}')
            return

        self.refresh_cache_label()
        if removed:
            self._show_success(
                '清理完成', f'已删除 {removed} 条闲置音频，释放 {audio_cache.format_size(freed)}')
        else:
            self._show_success('没有闲置音频', '现有音频均保留用于历史记录播放')

    def get_selected_record_path(self) -> Path | None:
        """返回单条已选记录路径，多选或未选时返回 ``None``。"""
        return self.selected_record_path

    def reload_records(self, *, selected_path=None):
        """重新扫描会话目录，并按时间填充记录表格。"""
        self.table.setRowCount(0)
        self.selected_record_path = None

        if not self.session_root_path.exists():
            self._update_action_buttons_state()
            return

        records = []
        for path in self.session_root_path.glob('*.json'):
            try:
                records.append((path.stat().st_mtime, path))
            except OSError:
                continue
        record_files = [path for _stamp, path in sorted(records, key=lambda item: item[0], reverse=True)]

        for record_file in record_files:
            item_data = self._read_one_record(record_file)
            self._append_row(item_data)

        if self.table.rowCount() > 0:
            selected_row = 0
            if selected_path:
                for row in range(self.table.rowCount()):
                    if Path(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == Path(selected_path):
                        selected_row = row
                        break
            self.table.selectRow(selected_row)

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
        self.link_ppt_button.setEnabled(selected_count == 1)
        selected = self.selected_record_paths()
        linked = (selected_count == 1 and self._read_one_record(selected[0])['presentation_state'] == '已关联')
        self.link_ppt_button.setText('更换 PPT' if linked else '关联 PPT')
        self.open_button.setToolTip('加载讲稿与配音；未关联 PPT 时进入受限模式' if not linked else '')
        self.delete_button.setEnabled(selected_count > 0)
        self.delete_button.setText(
            f'删除选中记录（{selected_count}）' if selected_count > 1 else '删除选中记录')

    def accept_selected(self):
        """验证单选记录存在后确认关闭弹窗。"""
        if len(self.selected_record_paths()) != 1:
            return
        row = self.table.currentRow()
        if row < 0:
            return

        # 每个单元格都携带相同路径，任意选中列都可定位记录。
        for col in range(self.table.columnCount()):
            path_item = self.table.item(row, col)
            if path_item is not None:
                record_path = path_item.data(Qt.ItemDataRole.UserRole)
                if record_path:
                    if not Path(str(record_path)).is_file():
                        self.reload_records()
                        self._show_warning('记录已不可用', '请刷新后选择其他历史记录')
                        return
                    self.selected_record_path = Path(str(record_path))
                    self.accept()
                    return

    def associate_selected(self):
        selected = self.selected_record_paths()
        if len(selected) != 1:
            return
        record_path = selected[0]
        dialog = None
        try:
            with record_path.open('r', encoding='utf-8') as stream:
                record = json.load(stream)
            pages = [int(item['page']) for item in record.get('items', [])]
            if not pages:
                raise ValueError('记录中没有可关联的讲稿。')
            dialog = PresentationLinkDialog(presentation_link.from_record(record), pages, self)
            if dialog.exec() and dialog.selected:
                presentation_link.update_record(record_path, dialog.selected, notes_baseline=None)
                self.presentation_link_changed.emit(record_path, dict(dialog.selected))
                self.reload_records(selected_path=record_path)
                self._show_success('关联成功', f'已关联 {dialog.selected["name"]}')
        except Exception as exc:
            self._show_error('关联失败', str(exc))
        finally:
            if dialog is not None:
                dialog.deleteLater()

    def delete_selected(self):
        """删除选中记录，并按用户选择清理其独占音频。"""
        record_paths = [path for path in self.selected_record_paths() if path.exists()]
        if not record_paths:
            self.reload_records()
            self._show_warning('未选择记录', '在列表中选中要删除的记录')
            return

        # 仅统计目标记录独占缓存，避免破坏仍保留记录的音频引用。
        exclusive = audio_cache.exclusive_keys(record_paths) - self.active_cache_keys
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
                safe_keys = exclusive - audio_cache.referenced_keys() - self.active_cache_keys
                removed_audio, freed = audio_cache.remove_keys(safe_keys)
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
            row_data['updated_at'],
            row_data['source_name'],
            str(row_data['items_count']),
            row_data['engine_mode'],
            row_data['speaker'],
            row_data['mark'],
            row_data['presentation_state'],
        ]

        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            # 路径附加到每个单元格，用户选中任意列都能解析记录。
            cell.setData(Qt.ItemDataRole.UserRole, str(row_data['file_path']))
            # 统一居中短字段，保持表格视觉节奏。
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == 6:
                cell.setToolTip(row_data['presentation_tooltip'])
            elif col == 1:
                cell.setToolTip(f'{row_data["source_name"]}\n记录：{row_data["file_name"]}')
            self.table.setItem(row, col, cell)

    def _read_one_record(self, record_path: Path) -> dict:
        """读取一条会话记录，并在字段缺失时生成可展示的默认值。"""
        try:
            fallback_time = datetime.fromtimestamp(record_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        except OSError:
            fallback_time = '-'
        fallback = {
            'file_path': record_path,
            'file_name': record_path.name,
            'created_at': fallback_time,
            'updated_at': fallback_time,
            'source_name': '-',
            'items_count': 0,
            'engine_mode': '-',
            'speaker': '-',
            'mark': '-',
            'session_id': record_path.stem,
            'presentation_state': '未关联',
            'presentation_tooltip': '未关联 PPT，加载后可播放配音；在此关联后可使用同步翻页和视频导出。',
        }

        try:
            with record_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return fallback
        if not isinstance(data, dict):
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
        link = presentation_link.from_record(data)
        associated = presentation_link.resolve(link)
        link_state = '已关联' if associated else ('文件缺失' if link.get('path') else '未关联')
        link_tip = (str(associated) if associated else
                    '关联 PPT 后可使用同步翻页、视频导出和写回 PPT 备注。')

        return {
            'file_path': record_path,
            'file_name': record_path.name,
            'created_at': created_at,
            'updated_at': str(data.get('updated_at') or created_at).replace('T', ' '),
            'source_name': source_name,
            'items_count': items_count,
            'engine_mode': mode,
            'speaker': speaker,
            'mark': str(data.get('mark', '-')),
            'session_id': str(data.get('session_id', record_path.stem)),
            'presentation_state': link_state,
            'presentation_tooltip': link_tip,
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
