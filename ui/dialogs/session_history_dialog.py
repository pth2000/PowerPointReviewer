"""浏览历史会话，并管理其音频缓存。"""

import json
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
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
    SearchLineEdit,
    TableWidget,
)

from ui import icons
from app.audio import cache as audio_cache
from app.presentation import link as presentation_link
from app.project.history_catalog import read_record_summary
from app.logging_setup import get_logger, log_error
from tasks.history_scan_task import HistoryScanTask
from ui.dialogs.delete_record_dialog import DeleteRecordMessageBox
from ui.dialogs.base import ThemedDialog
from ui.dialogs.presentation_link_dialog import PresentationLinkDialog


class SessionHistoryDialog(ThemedDialog):
    """列出历史会话，并集中处理加载、批量删除和缓存清理。"""

    presentation_link_changed = Signal(object, object)
    records_loaded = Signal()

    def __init__(self, session_root_path: Path, parent=None):
        super().__init__(parent)
        protector = getattr(parent, 'protected_audio_keys', None)
        self.active_cache_keys = (protector() if callable(protector)
                                  else set(getattr(parent, 'note_cache_keys', ()) or ()))
        self.session_root_path = Path(session_root_path)
        self.selected_record_path: Path | None = None
        self._rows = {}
        self._pending_rows = deque()
        self._loader = None
        self._reload_pending = False
        self._closing = False
        self._close_result = self.DialogCode.Rejected
        self._selected_after_load = None
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_batch)

        self.setWindowTitle('历史记录列表')
        self.setMinimumSize(800, 550)
        self.setWindowIcon(icons.SystemIcon.HISTORY.icon())

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
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        
        # 固定短字段的初始宽度，避免逐行计算列宽拖慢大量记录的加载。
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        
        for col in range(7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col == 1
                                        else QHeaderView.ResizeMode.Interactive)
        for col, width in ((2, 72), (3, 80), (4, 136), (5, 64), (6, 88)):
            header.resizeSection(col, width)
        header.resizeSection(0, max(180, self.table.fontMetrics().horizontalAdvance('2026-10-26 12:00:00') + 40))
        
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

        self.refresh_button = PushButton(icons.SystemIcon.UPDATE, '刷新', self)
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
        search_row = QHBoxLayout()
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText('搜索文件名或发音人')
        self.search_edit.textChanged.connect(self._filter_rows)
        search_row.addWidget(self.search_edit, 1)
        self.count_label = CaptionLabel(self)
        search_row.addWidget(self.count_label)
        main_layout.addLayout(search_row)
        main_layout.addWidget(self.table)
        main_layout.addLayout(cache_layout)
        main_layout.addLayout(button_layout)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(12, 12, 12, 12)

        self.reload_records()
        self._update_action_buttons_state()

    def refresh_cache_label(self):
        """刷新缓存文件数量和磁盘占用摘要。"""
        if self._loader is None:
            self._start_scan(records=False)

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
        self._selected_after_load = Path(selected_path) if selected_path else None
        self._pending_rows.clear()
        self._render_timer.stop()
        if self._loader is not None:
            self._reload_pending = True
            self._loader.requestInterruption()
            return
        self._start_scan()

    def _start_scan(self, *, records=True):
        if self._closing:
            return
        if records:
            self.table.setRowCount(0)
            self._rows.clear()
            self.selected_record_path = None
        self.cache_label.setText('正在读取历史记录…' if records else '正在统计音频缓存…')
        self.refresh_button.setEnabled(False)
        self.clean_button.setEnabled(False)
        task = HistoryScanTask(self.session_root_path, self, records=records)
        self._loader = task
        task.batch_ready.connect(lambda rows: self._receive_batch(task, rows))
        task.cache_ready.connect(lambda value: self.cache_label.setText(f'音频缓存：{value}')
                                 if task is self._loader and not self._closing else None)
        task.failed.connect(lambda message: self._show_error('历史记录读取失败', message)
                            if task is self._loader and not self._closing and not self._reload_pending else None)
        task.finished.connect(lambda: self._scan_finished(task))
        if records:
            self._filter_rows()
        task.start()

    def _receive_batch(self, task, rows):
        if task is self._loader and not self._closing and not self._reload_pending:
            self._pending_rows.extend(rows)
            self._render_timer.start(0)

    def _render_batch(self):
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            for _ in range(min(64, len(self._pending_rows))):
                data = self._pending_rows.popleft()
                self._rows[data['file_path']] = data
                self._append_row(data)
                self._filter_row(self.table.rowCount() - 1)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        self._update_count()
        if not self._pending_rows:
            self._render_timer.stop()
            if self._loader is None:
                self._finish_loading()

    def _scan_finished(self, task):
        if task is not self._loader:
            task.deleteLater()
            return
        self._loader = None
        task.deleteLater()
        if self._closing:
            self.done(self._close_result)
        elif self._reload_pending:
            self._reload_pending = False
            self._start_scan()
        elif not self._pending_rows:
            self._finish_loading()

    def _finish_loading(self):
        self.refresh_button.setEnabled(True)
        self.clean_button.setEnabled(True)
        visible = [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]
        if visible and (self._selected_after_load or not self.selected_record_paths()):
            row = next((row for row in visible
                        if Path(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == self._selected_after_load),
                       visible[0])
            self.table.selectRow(row)
        self._selected_after_load = None
        self._update_action_buttons_state()
        self.records_loaded.emit()

    def _filter_row(self, row):
        query = self.search_edit.text().strip().casefold()
        hidden = False
        if query:
            text = ' '.join(self.table.item(row, col).text() for col in (1, 3, 4)).casefold()
            hidden = query not in text
        self.table.setRowHidden(row, hidden)

    def _filter_rows(self, *_):
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            self._filter_row(row)
        self._update_count()
        self._update_action_buttons_state()

    def _update_count(self):
        count = self.table.rowCount()
        if self.search_edit.text().strip():
            visible = sum(not self.table.isRowHidden(row) for row in range(count))
            self.count_label.setText(f'{visible} / {count} 条')
        else:
            self.count_label.setText(f'{count} 条')

    def _finish_dialog(self, result):
        self._closing = True
        self._close_result = result
        self._render_timer.stop()
        self._pending_rows.clear()
        if self._loader is not None:
            self._loader.requestInterruption()
            self.cache_label.setText('正在关闭…')
            return
        self.done(result)

    def reject(self):
        self._finish_dialog(self.DialogCode.Rejected)

    def accept(self):
        self._finish_dialog(self.DialogCode.Accepted)

    def closeEvent(self, event):
        if self._loader is not None:
            event.ignore()
            self.reject()
        else:
            self._closing = True
            self._render_timer.stop()
            super().closeEvent(event)

    def select_all_records(self):
        """选中表格中的全部历史记录。"""
        if self.table.rowCount():
            self.table.selectAll()

    def selected_record_paths(self) -> list:
        """按表格顺序返回已选行对应的去重记录路径。"""
        paths_list = []
        for index in sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row()):
            item = self.table.item(index.row(), 0)
            if item is None or self.table.isRowHidden(index.row()):
                continue
            raw_path = item.data(Qt.ItemDataRole.UserRole)
            if raw_path:
                paths_list.append(Path(str(raw_path)))
        return paths_list

    def _update_action_buttons_state(self):
        """根据记录数量和选择数量更新操作按钮。"""
        has_rows = self.table.rowCount() > 0
        selected = self.selected_record_paths()
        selected_count = len(selected)
        self.select_all_button.setEnabled(has_rows)
        # 加载只接受单条记录，删除则支持批量选择。
        record = self._read_one_record(selected[0]) if selected_count == 1 else None
        linked = record is not None and record['presentation_state'] == '已关联'
        readable = record is not None and not record.get('read_error')
        self.open_button.setEnabled(readable)
        self.link_ppt_button.setEnabled(readable)
        self.link_ppt_button.setText('更换 PPT' if linked else '关联 PPT')
        self.open_button.setToolTip('加载讲稿与配音；未关联 PPT 时进入受限模式' if not linked else '')
        self.delete_button.setEnabled(selected_count > 0)
        self.delete_button.setText(
            f'删除选中记录（{selected_count}）' if selected_count > 1 else '删除选中记录')

    def accept_selected(self):
        selected = self.selected_record_paths()
        if len(selected) != 1:
            return
        path = selected[0]
        if not path.is_file():
            self.reload_records()
            self._show_warning('记录已不可用', '请刷新后选择其他历史记录')
            return
        if self._read_one_record(path).get('read_error'):
            self._show_error('记录无法读取', '请尝试恢复备份或选择其他记录。')
            return
        self.selected_record_path = path
        self.accept()

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
            get_logger().exception('关联历史记录文稿失败：%s', record_path)
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
                get_logger().exception('删除历史记录失败：%s', record_path)
                print(f'[历史记录] 删除失败 {record_path.name}：{e}')
                failed.append(record_path.name)

        removed_audio, freed = (0, 0)
        if delete_audio and exclusive and deleted:
            try:
                safe_keys = exclusive - audio_cache.referenced_keys() - self.active_cache_keys
                removed_audio, freed = audio_cache.remove_keys(safe_keys)
            except Exception:
                get_logger().exception('删除历史记录后清理音频失败')

        if self.selected_record_path in deleted:
            self.selected_record_path = None

        self.reload_records()

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
            cell.setTextAlignment((Qt.AlignmentFlag.AlignLeft if col in (1, 4)
                                   else Qt.AlignmentFlag.AlignHCenter) | Qt.AlignmentFlag.AlignVCenter)
            if col == 6:
                cell.setToolTip(row_data['presentation_tooltip'])
            elif col == 1:
                cell.setToolTip(f'{row_data["source_name"]}\n记录：{row_data["file_name"]}')
            elif col == 4:
                cell.setToolTip(row_data['speaker'])
            elif col == 0:
                cell.setToolTip(row_data['updated_at'])
            self.table.setItem(row, col, cell)

    def _read_one_record(self, record_path: Path) -> dict:
        return self._rows.get(Path(record_path)) or read_record_summary(record_path)

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
        log_error(title, content)
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self,
        )
