"""按页组织片段，试听、对比并采用片段级配音调整。"""

import copy
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt, QEvent, QSize, QItemSelectionModel, QTimer, QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QSizePolicy,
    QSplitter, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from qfluentwidgets import (
    Action, BodyLabel, CaptionLabel, CardWidget, ComboBox, DoubleSpinBox,
    InfoBar, InfoBarPosition, LineEdit, MessageBox,
    PrimaryPushButton, PrimaryToolButton,
    ProgressBar, RoundMenu, ScrollArea, Slider, SpinBox,
    StrongBodyLabel, SubtitleLabel, TableWidget, TransparentPushButton, TransparentToolButton,
    Theme, setCustomStyleSheet,
)

from app import paths
from app.audio import segments as segment_audio
from app.audio.playback import AudioOutputWatcher
from app.logging_setup import get_logger
from app.documents.statistics import format_duration
from tasks.audio_review_task import AudioReviewTask
from ui.icons import SystemIcon
from ui.dialogs.base import ThemedDialog
from ui.dialogs.audio_review_widgets import (
    SEGMENT_ROLE, AudioSourceSwitch, ReviewStatusLabel, SegmentDelegate,
)


class AudioReviewDialog(ThemedDialog):
    def __init__(self, reviewer, parent=None):
        super().__init__(parent)
        self.reviewer = reviewer
        self.project = reviewer.project
        self.ctx = reviewer.ctx
        self.revision = None
        self.task = None
        self.candidates = {}
        self.drafts = {}
        self.undo_stack = []
        self.redo_stack = []
        self._syncing = False
        self._close_after_task = False
        self._queue = []
        self._queue_offset = 0
        self._playing = False
        self._play_serial = 0
        paths.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        self._directory = tempfile.TemporaryDirectory(prefix='audio-review-', dir=paths.PREVIEW_DIR)
        self.setWindowTitle('配音审听')
        self.resize(1180, 760)
        self.setMinimumSize(960, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(14)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(SubtitleLabel('配音审听', self))
        self.total_label = CaptionLabel(self)
        heading.addWidget(self.total_label)
        top.addLayout(heading)
        top.addStretch()
        self.undo_button = TransparentPushButton(SystemIcon.UNDO, '撤销', self)
        self.undo_button.setToolTip('撤销上一次配音更改。')
        self.undo_button.clicked.connect(self.undo)
        top.addWidget(self.undo_button)
        self.redo_button = TransparentPushButton(SystemIcon.REDO, '重做', self)
        self.redo_button.setToolTip('重新应用刚刚撤销的配音更改。')
        self.redo_button.clicked.connect(self.redo)
        top.addWidget(self.redo_button)
        more_button = TransparentToolButton(SystemIcon.MORE, self)
        more_button.setToolTip('更多操作')
        self.more_menu = RoundMenu(parent=self)
        self.generate_button = Action('按当前设置重新配音', triggered=lambda: self.prepare(True))
        self.generate_button.setToolTip('使用设置页当前的引擎和音色生成试听，采用后替换所选片段的配音。')
        self.more_menu.addAction(self.generate_button)
        self.restore_button = Action('恢复选中片段的原始配音', triggered=self.restore_original)
        self.more_menu.addAction(self.restore_button)
        self.more_menu.addSeparator()
        self.stop_action = Action('停止试听', triggered=self.stop)
        self.more_menu.addAction(self.stop_action)
        more_button.clicked.connect(lambda: self.more_menu.exec(
            more_button.mapToGlobal(more_button.rect().bottomLeft())))
        top.addWidget(more_button)
        root.addLayout(top)

        workspace = CardWidget(self)
        workspace.setBorderRadius(10)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal, workspace)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet('QSplitter::handle { background: rgba(128,128,128,0.16); }')
        middle = QWidget(splitter)
        middle.setMinimumWidth(360)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(16, 14, 16, 12)
        middle_layout.setSpacing(10)
        list_toolbar = QHBoxLayout()
        self.pages = ComboBox(middle)
        self.pages.setFixedWidth(160)
        self.pages.setToolTip('按页查看讲稿片段')
        self.pages.currentIndexChanged.connect(self._populate_segments)
        list_toolbar.addWidget(self.pages)
        list_toolbar.addStretch()
        self.search = LineEdit(middle)
        self.search.setPlaceholderText('查找讲稿')
        self.search.setMaximumWidth(210)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._populate_segments)
        list_toolbar.addWidget(self.search)
        middle_layout.addLayout(list_toolbar)
        self.table = TableWidget(middle)
        self.table.setColumnCount(4)
        self.table.setItemDelegate(SegmentDelegate(self.table))
        self.table.horizontalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        for column in (1, 3):
            self.table.setColumnHidden(column, True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table_style = 'TableWidget { background: transparent; border: none; }'
        setCustomStyleSheet(self.table, table_style, table_style)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.play(indices=[self.current()]))
        self.table.viewport().installEventFilter(self)
        self.table.installEventFilter(self)
        middle_layout.addWidget(self.table, 1)
        self.empty_label = CaptionLabel('没有找到匹配的片段', middle)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        middle_layout.addWidget(self.empty_label, 1)
        splitter.addWidget(middle)

        right_pane = QWidget(splitter)
        right_pane.setMinimumWidth(300)
        pane_layout = QVBoxLayout(right_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)
        self.inspector_scroll = ScrollArea(right_pane)
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inspector_scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self.inspector_scroll.viewport().setAutoFillBackground(False)
        right = QWidget()
        right.setObjectName('audioReviewInspector')
        right.setStyleSheet('QWidget#audioReviewInspector { background: transparent; }')
        self.inspector_scroll.setWidget(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 20, 20, 12)
        right_layout.setSpacing(16)
        context = QVBoxLayout()
        context.setSpacing(6)
        self.selection_label = StrongBodyLabel('选择一个片段', right)
        context.addWidget(self.selection_label)
        self.selection_detail = CaptionLabel(right)
        context.addWidget(self.selection_detail)
        self.edit_summary = CaptionLabel(right)
        self.edit_summary.setWordWrap(True)
        self.edit_summary.hide()
        context.addWidget(self.edit_summary)
        right_layout.addLayout(context)
        right_layout.addWidget(self._rule(right))

        self.speed = DoubleSpinBox(right)
        self.speed.setRange(0.5, 2)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setSuffix(' ×')
        self.speed.setFixedWidth(88)
        speed_row = QHBoxLayout()
        speed_row.addWidget(BodyLabel('语速', right))
        speed_row.addStretch()
        speed_row.addWidget(self.speed)
        speed_layout = QVBoxLayout()
        speed_layout.setSpacing(8)
        speed_layout.addLayout(speed_row)
        self.speed_slider = Slider(Qt.Orientation.Horizontal, right)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.valueChanged.connect(lambda value: self.speed.setValue(value / 100))
        self.speed.valueChanged.connect(lambda value: self.speed_slider.setValue(round(value * 100)))
        self.speed_slider.setValue(100)
        speed_layout.addWidget(self.speed_slider)
        right_layout.addLayout(speed_layout)
        self.volume = SpinBox(right)
        self.volume.setRange(0, 200)
        self.volume.setSuffix(' %')
        self.volume.setFixedWidth(88)
        volume_row = QHBoxLayout()
        volume_row.addWidget(BodyLabel('音量', right))
        volume_row.addStretch()
        volume_row.addWidget(self.volume)
        right_layout.addLayout(volume_row)
        self.leading = SpinBox(right)
        self.trailing = SpinBox(right)
        pauses = QHBoxLayout()
        pauses.setSpacing(12)
        for label, control in (('开头留白', self.leading), ('结尾留白', self.trailing)):
            control.setRange(0, 3000)
            control.setSingleStep(50)
            control.setSuffix(' 毫秒')
            control.setToolTip('补足至少这么长的留白，保留原音频已有的停顿。')
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            column = QVBoxLayout()
            column.setSpacing(8)
            column.addWidget(BodyLabel(label, right))
            column.addWidget(control)
            pauses.addLayout(column, 1)
        right_layout.addLayout(pauses)
        for control in (self.speed, self.volume, self.leading, self.trailing):
            control.setSymbolVisible(False)
            control.valueChanged.connect(self._draft_changed)
        right_layout.addWidget(self._rule(right))
        right_layout.addWidget(StrongBodyLabel('讲稿', right))
        self.script_text = BodyLabel(right)
        self.script_text.setWordWrap(True)
        self.script_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.script_text.setTextFormat(Qt.TextFormat.PlainText)
        self.script_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.script_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        right_layout.addWidget(self.script_text)
        self.message = ReviewStatusLabel(right)
        self.message.setWordWrap(True)
        self.message.setText('')
        right_layout.addWidget(self.message)
        self.progress = ProgressBar(right)
        self.progress.hide()
        right_layout.addWidget(self.progress)
        right_layout.addStretch(1)
        pane_layout.addWidget(self.inspector_scroll, 1)
        pane_layout.addWidget(self._rule(right_pane))
        actions = QWidget(right_pane)
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(20, 12, 20, 16)
        actions_layout.setSpacing(10)
        self.comparison_detail = CaptionLabel(actions)
        self.comparison_detail.setWordWrap(True)
        self.comparison_detail.hide()
        actions_layout.addWidget(self.comparison_detail)
        action_row = QHBoxLayout()
        self.discard_button = TransparentPushButton('放弃调整', actions)
        self.discard_button.clicked.connect(self.discard)
        action_row.addWidget(self.discard_button)
        self.render_button = PrimaryPushButton('生成试听', actions)
        self.render_button.clicked.connect(lambda: self.prepare(False))
        self.render_button.setToolTip('生成新配音用于试听，采用后才替换当前配音。')
        action_row.addWidget(self.render_button, 1)
        self.apply_button = PrimaryPushButton('采用新配音', actions)
        self.apply_button.clicked.connect(self.adopt)
        action_row.addWidget(self.apply_button, 1)
        actions_layout.addLayout(action_row)
        pane_layout.addWidget(actions)
        splitter.addWidget(right_pane)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([780, 320])
        workspace_layout.addWidget(splitter)
        root.addWidget(workspace, 1)

        player = QWidget(self)
        player_layout = QHBoxLayout(player)
        player_layout.setContentsMargins(4, 2, 4, 2)
        player_layout.setSpacing(14)
        transport = QHBoxLayout()
        transport.setSpacing(6)
        self.previous_button = TransparentToolButton(SystemIcon.PREVIOUS, player)
        self.previous_button.setIconSize(QSize(16, 16))
        self.previous_button.setToolTip('上一段并播放')
        self.previous_button.clicked.connect(lambda: self.navigate(-1))
        self.play_button = PrimaryToolButton(SystemIcon.PLAY.icon(Theme.DARK), player)
        self.play_button.setFixedSize(42, 42)
        self.play_button.setIconSize(QSize(17, 17))
        self.play_button.setToolTip('播放')
        self.play_button.clicked.connect(self.toggle_play)
        self.next_button = TransparentToolButton(SystemIcon.NEXT, player)
        self.next_button.setIconSize(QSize(16, 16))
        self.next_button.setToolTip('下一段并播放')
        self.next_button.clicked.connect(lambda: self.navigate(1))
        for button in (self.previous_button, self.play_button, self.next_button):
            transport.addWidget(button)
        player_layout.addLayout(transport)
        timeline = QVBoxLayout()
        timeline.setSpacing(4)
        track = QHBoxLayout()
        self.playing_label = CaptionLabel('选择片段开始试听', player)
        track.addWidget(self.playing_label)
        track.addStretch()
        self.time_label = CaptionLabel(player)
        track.addWidget(self.time_label)
        timeline.addLayout(track)
        self.seek = Slider(Qt.Orientation.Horizontal, player)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self._seek)
        timeline.addWidget(self.seek)
        player_layout.addLayout(timeline, 1)
        self.compare_card = QWidget(player)
        compare_layout = QHBoxLayout(self.compare_card)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        self.compare_tabs = AudioSourceSwitch(self.compare_card)
        self.compare_tabs.addItem('current', '当前配音', lambda: self._compare_source('current'))
        self.compare_tabs.addItem('candidate', '新配音', lambda: self._compare_source('candidate'))
        self.compare_tabs.setCurrentItem('current')
        compare_layout.addWidget(self.compare_tabs)
        player_layout.addWidget(self.compare_card)
        self.play_range = ComboBox(player)
        self.play_range.addItems(['当前片段', '当前页', '选中片段', '从当前位置继续'])
        self.play_range.setToolTip('播放范围')
        player_layout.addWidget(self.play_range)
        root.addWidget(player)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.device_watcher = AudioOutputWatcher(self.player, self.output, '配音审听', parent=self)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(lambda _code, message: self._audio_error(message))
        self.ctx.playback_bus.stop_requested.connect(self._stop_requested)
        reviewer.audio_changed.connect(self.reload)
        self.reload()

    @staticmethod
    def _rule(parent):
        rule = QFrame(parent)
        rule.setFixedHeight(1)
        rule.setStyleSheet('background: rgba(128,128,128,0.16); border: none;')
        return rule

    def eventFilter(self, obj, event):
        if (hasattr(self, 'table') and obj in (self.table, self.table.viewport())
                and event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange)):
            self._layout_segments()
        return super().eventFilter(obj, event)

    def _layout_segments(self):
        if self._syncing or getattr(self, '_laying_out_rows', False):
            return
        self._laying_out_rows = True
        try:
            selected = set(self.selected())
            width = max(80, self.table.viewport().width() - self.table.columnWidth(0) - 26)
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 2)
                if item is None:
                    continue
                data = dict(item.data(SEGMENT_ROLE) or {})
                data['expanded'] = item.data(Qt.ItemDataRole.UserRole) in selected
                item.setData(SEGMENT_ROLE, data)
                height = SegmentDelegate.row_height(data.get('text', ''), width,
                                                    self.table.font(), data['expanded'])
                if self.table.rowHeight(row) != height:
                    self.table.setRowHeight(row, height)
        finally:
            self._laying_out_rows = False

    def showEvent(self, event):
        super().showEvent(event)
        if not Path(self._directory.name).is_dir():
            self._directory = tempfile.TemporaryDirectory(prefix='audio-review-', dir=paths.PREVIEW_DIR)
        self.reload()
        self._refresh_segment_states()
        self._selection_changed()

    def selected(self):
        return [int(self.table.item(row.row(), 1).data(Qt.ItemDataRole.UserRole))
                for row in sorted(self.table.selectionModel().selectedRows(), key=lambda item: item.row())]

    def current(self):
        row = self.table.currentRow()
        selected = [item.row() for item in self.table.selectionModel().selectedRows()]
        if not selected:
            return None
        if row not in selected:
            row = min(selected)
        item = self.table.item(row, 1) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def reload(self):
        page = self.reviewer
        if not self.project.audio_loaded or page.is_busy():
            self.stop()
            if self.task is not None:
                self.task.requestInterruption()
            self.revision = None
            self.candidates.clear()
            self.drafts.clear()
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._syncing = True
            self.table.setRowCount(0)
            self.pages.clear()
            self._syncing = False
            self.selection_label.setText('选择一个片段')
            self.selection_detail.clear()
            self.script_text.clear()
            self.message.setText('配音准备好后，可继续逐段试听。')
            self._update_buttons()
            return
        revision = page.audio_revision()
        if revision == self.revision:
            return
        self.stop()
        if self.task is not None:
            self.task.requestInterruption()
        self.revision = revision
        self.candidates.clear()
        self.drafts.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.pages.blockSignals(True)
        self.pages.clear()
        self.pages.addItem('全部页面', userData=None)
        for number in sorted({item['page'] for item in self.project.notes_list}):
            count = sum(item['page'] == number for item in self.project.notes_list)
            self.pages.addItem(f'第 {number} 页（{count} 段）', userData=number)
        self.pages.setCurrentIndex(0)
        self.pages.blockSignals(False)
        self.total_label.setText(f'{len(self.project.notes_list)} 段 · {format_duration(sum(self.project.notes_duration_list))}')
        self._populate_segments()
        self._select_index(min(self.project.current_index, max(0, len(self.project.notes_list) - 1)))

    def _populate_segments(self, *_):
        if self._syncing or self.revision is None:
            return
        previous = self.current()
        selection = set(self.selected())
        self._syncing = True
        self.table.setRowCount(0)
        number = self.pages.currentData()
        query = self.search.text().strip().casefold()
        counts = {}
        for index, note in enumerate(self.project.notes_list):
            counts[note['page']] = counts.get(note['page'], 0) + 1
            spoken = self.project.audio_edits.get(index, {}).get('spoken_text', note['text'])
            if (number is not None and note['page'] != number) or query not in (note['text'] + '\n' + spoken).casefold():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            wrapper = QWidget(self.table)
            row_layout = QHBoxLayout(wrapper)
            button = TransparentToolButton(SystemIcon.PLAY, wrapper)
            button.setFixedSize(32, 32)
            button.setIconSize(QSize(16, 16))
            button.setToolTip('播放这一段')
            button.clicked.connect(lambda _checked=False, selected=index: self._play_segment(selected))
            row_layout.setSpacing(0)
            row_layout.setContentsMargins(8, 0, 8, 0)
            row_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 0, wrapper)
            for column, text in ((1, f'{note["page"]} · {counts[note["page"]]}'),
                                 (2, note['text'] or '（空白片段）'),
                                 (3, format_duration(self.project.notes_duration_list[index]))):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, index)
                if column == 2:
                    item.setData(SEGMENT_ROLE, {
                        'heading': f'第 {note["page"]} 页 · 第 {counts[note["page"]]} 段',
                        'text': spoken, 'duration': format_duration(self.project.notes_duration_list[index]),
                        'status': self._segment_status(index),
                    })
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 104)
        count = self.table.rowCount()
        self.table.setVisible(bool(count))
        self.empty_label.setVisible(not count)
        if self.table.rowCount():
            self.table.selectRow(0)
            if previous is not None:
                self._select_index(previous, reveal=False)
            matching = [row for row in range(count)
                        if self.table.item(row, 1).data(Qt.ItemDataRole.UserRole) in selection]
            if matching:
                self.table.clearSelection()
                for row in matching:
                    self.table.selectionModel().select(
                        self.table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self._syncing = False
        self._refresh_segment_states()
        self._selection_changed()

    def _select_index(self, index, *, reveal=True):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1).data(Qt.ItemDataRole.UserRole) == index:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 1))
                return
        if reveal:
            self.search.clear()
            self.pages.setCurrentIndex(0)
            self._populate_segments()
            self._select_index(index, reveal=False)

    def _play_segment(self, index):
        if index not in self.selected():
            self._select_index(index)
        self.play(indices=[index])

    def _draft(self, index):
        edit = self.project.audio_edits.get(index, {})
        return self.drafts.get(index, {'settings': segment_audio.settings(edit.get('settings')),
                                       'spoken_text': edit.get('spoken_text', self.project.notes_list[index]['text'])})

    def _segment_status(self, index):
        if index in self.candidates:
            return '待采用'
        if index in self.drafts:
            return '待试听'
        return '已调整' if index in self.project.audio_edits else ''

    def _refresh_segment_states(self, indices=None):
        indices = None if indices is None else set(indices)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            index = item.data(Qt.ItemDataRole.UserRole)
            if indices is not None and index not in indices:
                continue
            data = dict(item.data(SEGMENT_ROLE) or {})
            data['status'] = self._segment_status(index)
            item.setData(SEGMENT_ROLE, data)

    def _selection_changed(self):
        if self._syncing:
            return
        index, selected = self.current(), self.selected()
        self._syncing = True
        if index is not None:
            note = self.project.notes_list[index]
            offset = sum(item['page'] == note['page'] for item in self.project.notes_list[:index]) + 1
            self.selection_label.setText(f'第 {note["page"]} 页 · 第 {offset} 段' if len(selected) <= 1
                                         else f'已选择 {len(selected)} 个片段')
            duration = sum(self.project.notes_duration_list[i] for i in selected)
            speaker = str(self.project.generation_speaker or '').strip()
            if len(selected) == 1:
                profile = self.project.audio_edits.get(index, {}).get('voice_profile', {})
                speaker = str(profile.get('voice_name') or speaker)
            self.selection_detail.setText(f'{speaker or "当前配音"}　{format_duration(duration)}')
            self.selection_detail.setWordWrap(True)
            edit = self.project.audio_edits.get(index, {}) if len(selected) == 1 else {}
            parts = []
            for key, label, suffix, default in (('speed', '语速 ', '×', 1), ('volume', '音量 ', '%', 100),
                                                ('leading_ms', '开头 ', ' 毫秒', 0), ('trailing_ms', '结尾 ', ' 毫秒', 0)):
                value = edit.get('settings', {}).get(key, default)
                if value != default:
                    parts.append(f'{label}{value:g}{suffix}')
            self.edit_summary.setText('，'.join(parts) or ('已重新配音' if edit else ''))
            self.edit_summary.setVisible(bool(edit))
            self.selection_label.setToolTip('批量调整只应用修改的参数，其他设置分别保留。'
                                           if len(selected) > 1 else '')
            draft = self._draft(index)
            if len(selected) == 1:
                self.script_text.setText(draft['spoken_text'])
            else:
                texts = []
                for selected_index in selected:
                    selected_note = self.project.notes_list[selected_index]
                    selected_offset = sum(item['page'] == selected_note['page']
                                          for item in self.project.notes_list[:selected_index]) + 1
                    texts.append(f'第 {selected_note["page"]} 页 · 第 {selected_offset} 段\n'
                                 f'{self._draft(selected_index)["spoken_text"]}')
                self.script_text.setText('\n\n'.join(texts))
            for control, key in ((self.speed, 'speed'), (self.volume, 'volume'),
                                  (self.leading, 'leading_ms'), (self.trailing, 'trailing_ms')):
                control.setValue(draft['settings'][key])
        else:
            self.selection_label.setText('选择一个片段')
            self.selection_detail.clear()
            self.edit_summary.hide()
            self.script_text.clear()
        self._syncing = False
        self._layout_segments()
        self._update_buttons()
        if hasattr(self, 'player') and self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            if self._queue and self._queue[self._queue_offset][0] != index:
                self.stop()
            self._position_changed(0)

    def _controls(self):
        return segment_audio.settings({'speed': self.speed.value(), 'volume': self.volume.value(),
                                       'leading_ms': self.leading.value(), 'trailing_ms': self.trailing.value()})

    def _draft_changed(self, *_):
        if self._syncing:
            return
        selected = self.selected()
        if any(index in selected and source == 'candidate' for index, source in self._queue):
            self.stop()
        options = self._controls()
        changed_key = next((key for control, key in (
            (self.speed, 'speed'), (self.volume, 'volume'),
            (self.leading, 'leading_ms'), (self.trailing, 'trailing_ms')) if self.sender() is control), None)
        for index in selected:
            previous = self._draft(index)
            values = ({**previous['settings'], changed_key: options[changed_key]} if changed_key else options)
            draft = {'settings': values, 'spoken_text': previous['spoken_text']}
            edit = self.project.audio_edits.get(index, {})
            baseline = {'settings': segment_audio.settings(edit.get('settings')),
                        'spoken_text': edit.get('spoken_text', self.project.notes_list[index]['text'])}
            if draft == baseline:
                self.drafts.pop(index, None)
            else:
                self.drafts[index] = draft
            self.candidates.pop(index, None)
        self._update_buttons()

    def _update_buttons(self):
        selected = self.selected()
        ready = bool(self.revision is not None and selected)
        idle = self.task is None
        for control in (self.speed, self.volume, self.leading, self.trailing, self.speed_slider):
            control.setEnabled(ready and idle)
        pending = ready and any(index in self.drafts for index in selected)
        self.render_button.setEnabled(bool(pending and idle))
        self.generate_button.setEnabled(ready and idle)
        prepared = ready and all(index in self.candidates for index in selected)
        self.apply_button.setEnabled(prepared and idle)
        self.apply_button.setVisible(bool(prepared))
        self.render_button.setVisible(not prepared)
        self.discard_button.setEnabled(ready and any(
            index in self.candidates or index in self.drafts for index in selected) and idle)
        self.discard_button.setVisible(bool(ready and any(
            index in self.candidates or index in self.drafts for index in selected)))
        self.restore_button.setEnabled(ready and idle and any(index in self.project.audio_edits for index in selected))
        candidates = [self.candidates[i] for i in selected if i in self.candidates]
        self.compare_card.setVisible(bool(prepared))
        self.comparison_detail.setVisible(False)
        if prepared:
            old = sum(self.project.notes_duration_list[i] for i in selected if i in self.candidates)
            new = sum(item['duration'] for item in candidates)
            self.comparison_detail.setText(f'{format_duration(old)}  →  {format_duration(new)}')
            self.comparison_detail.setVisible(abs(old - new) >= 0.02)
        self.undo_button.setEnabled(bool(self.undo_stack) and idle and self.revision is not None)
        self.redo_button.setEnabled(bool(self.redo_stack) and idle and self.revision is not None)
        self._refresh_segment_states(selected)
        self.play_button.setEnabled(self.revision is not None)
        for button in (self.previous_button, self.next_button):
            button.setEnabled(self.revision is not None)

    def _ensure_current(self):
        if self.reviewer.is_busy() or not self.project.audio_loaded or self.revision != self.reviewer.audio_revision():
            self.reload()
            self.message.setText('讲稿已更新，请重新选择片段。')
            return False
        return True

    def prepare(self, regenerate=False):
        if self.task is not None or not self._ensure_current():
            return
        selected = self.selected()
        if not selected:
            return
        if not regenerate and not any(index in self.drafts for index in selected):
            self.play(indices=selected)
            return
        try:
            jobs = []
            for index in selected:
                edit = self.project.audio_edits.get(index, {})
                draft = self._draft(index)
                needs_generation = regenerate
                job_engine = None
                if needs_generation:
                    job_engine = self.ctx.tts_engine.snapshot()
                base = edit.get('base') or self._reference(index)
                base_path = segment_audio.cache_path(self.reviewer.audio_cache_path, base)
                if not base_path.is_file() and not needs_generation:
                    raise RuntimeError('原配音文件缺失，请重新生成这一段后再调整。')
                jobs.append({'index': index, 'base_path': base_path,
                             'regenerated': needs_generation,
                             'engine': job_engine,
                             'settings': draft['settings'], 'spoken_text': draft['spoken_text'],
                             'voice_profile': segment_audio.public_profile(job_engine.get_generation_profile()) if job_engine else
                                              edit.get('voice_profile', segment_audio.public_profile(self.project.generation_profile))})
            self.stop()
            self._job_revision = self.revision
            self.task = AudioReviewTask(jobs, self._directory.name, parent=self)
            self.task.ready.connect(self._prepared)
            self.task.failed.connect(self._failed)
            self.task.progress.connect(lambda done, total: self.progress.setValue(round(done * 100 / total)))
            self.task.finished.connect(self._task_finished)
            self.progress.setValue(0)
            self.progress.show()
            self.message.setText('正在生成试听配音…')
            self.task.start()
            self._update_buttons()
        except Exception as exc:
            self._failed(str(exc))

    def _prepared(self, results):
        if (self._close_after_task or self._job_revision != self.revision
                or self._job_revision != self.reviewer.audio_revision()):
            return
        self.candidates.update(results)
        self._refresh_segment_states(results)
        self.message.setText('')
        self.play(indices=list(results), source='candidate')

    def _failed(self, message):
        get_logger().error('配音审听准备失败：%s', message)
        self.message.setText(f'配音准备失败：{message}')

    def _task_finished(self):
        task, self.task = self.task, None
        if task is not None:
            task.deleteLater()
        self.progress.hide()
        self._update_buttons()
        if self._close_after_task:
            self._close_after_task = False
            self.close()

    def _reference(self, index):
        return segment_audio.reference(self.project.note_cache_keys[index], self.project.note_cache_exts[index],
                                       self.project.notes_duration_list[index])

    def _snapshot(self, indices):
        return {index: {'path': self.project.media_list[index],
                        **self._reference(index), 'edit': copy.deepcopy(self.project.audio_edits.get(index))}
                for index in indices}

    def _commit(self, updates, *, remember=True):
        before = self._snapshot(updates)
        self.stop()
        self.reviewer.apply_segment_audio(updates, self.revision)
        # audio_changed is deliberately not emitted during this transaction:
        # retain this window's drafts and undo history after adopting a candidate.
        self.revision = self.reviewer.audio_revision()
        if remember:
            self.undo_stack.append(before)
            self.undo_stack = self.undo_stack[-20:]
            self.redo_stack.clear()
        for index in updates:
            self.candidates.pop(index, None)
            self.drafts.pop(index, None)
        self.total_label.setText(f'{len(self.project.notes_list)} 段 · {format_duration(sum(self.project.notes_duration_list))}')
        self._populate_segments()

    def _notify_success(self, text):
        self.message.setText('')
        InfoBar.success(title=text, content='', isClosable=True,
                        position=InfoBarPosition.TOP, duration=1800, parent=self)

    def adopt(self):
        if not self._ensure_current():
            return
        try:
            updates = {}
            for index in self.selected():
                candidate = self.candidates[index]
                key, ext, path = segment_audio.store_audio(candidate['path'], self.reviewer.audio_cache_path)
                old = self.project.audio_edits.get(index, {})
                original = old.get('original')
                if not original:
                    original_key, original_ext, _original_path = segment_audio.store_audio(
                        self.project.media_list[index], self.reviewer.audio_cache_path)
                    original = segment_audio.reference(
                        original_key, original_ext, self.project.notes_duration_list[index])
                if candidate['regenerated'] or not old.get('base'):
                    base_key, base_ext, _path = segment_audio.store_audio(candidate['base_path'], self.reviewer.audio_cache_path)
                else:
                    base = old['base']
                    base_key, base_ext = base['key'], base['ext']
                edit = {'text': self.project.notes_list[index]['text'],
                        'profile_id': segment_audio.profile_id(self.project.generation_profile),
                        'original': copy.deepcopy(original),
                        'base': segment_audio.reference(base_key, base_ext, candidate['base_duration']),
                        'settings': candidate['settings'], 'spoken_text': candidate['spoken_text'],
                        'voice_profile': candidate['voice_profile'], 'audio_key': key, 'audio_ext': ext}
                updates[index] = {'path': path, 'key': key, 'ext': ext,
                                  'duration': candidate['duration'], 'edit': edit}
            if updates:
                self._commit(updates)
                self._notify_success('已采用配音')
        except Exception as exc:
            get_logger().exception('采用片段配音失败')
            self.message.setText(f'采用失败：{exc}')

    def discard(self):
        self.stop()
        for index in self.selected():
            self.candidates.pop(index, None)
            self.drafts.pop(index, None)
        self._selection_changed()
        self._notify_success('已放弃调整')

    def restore_original(self):
        if self.task is not None or not self._ensure_current():
            return
        try:
            updates = {}
            for index in self.selected():
                edit = self.project.audio_edits.get(index)
                if edit:
                    original = edit['original']
                    updates[index] = {**original, 'path': segment_audio.cache_path(
                        self.reviewer.audio_cache_path, original), 'edit': None}
            if updates:
                if not self._confirm_replace_pending(updates, '恢复原始配音'):
                    return
                self._commit(updates)
                self._notify_success('已恢复原始配音')
        except Exception as exc:
            get_logger().exception('恢复原始配音失败')
            self.message.setText(f'恢复失败：{exc}')

    def undo(self):
        self._replay_history(self.undo_stack, self.redo_stack, '撤销')

    def redo(self):
        self._replay_history(self.redo_stack, self.undo_stack, '重做')

    def _confirm_replace_pending(self, indices, action):
        if not any(index in self.candidates or index in self.drafts for index in indices):
            return True
        box = MessageBox(f'{action}？', '相关片段还有未采用的调整，继续将放弃这些调整。', self)
        box.yesButton.setText(f'继续{action}')
        box.cancelButton.setText('取消')
        return bool(box.exec())

    def _replay_history(self, source, destination, action):
        if not source or self.task is not None or not self._ensure_current():
            return
        target = source[-1]
        if not self._confirm_replace_pending(target, action):
            return
        try:
            before = self._snapshot(target)
            self._commit(target, remember=False)
            source.pop()
            destination.append(before)
            if self.current() not in target:
                self._select_index(min(target))
            self._update_buttons()
            self._notify_success(f'已{action}配音更改')
        except Exception as exc:
            get_logger().exception('%s配音更改失败', action)
            self.message.setText(f'{action}失败：{exc}')

    def protected_keys(self):
        keys = set()
        for snapshot in self.undo_stack + self.redo_stack:
            for update in snapshot.values():
                keys.add(update['key'])
                keys.update(ref['key'] for ref in segment_audio.references({'audio_edit': update.get('edit')}))
        return keys

    def play(self, indices=None, source='current'):
        if not self._ensure_current():
            return
        if indices is None:
            index = self.current()
            if index is None:
                return
            mode = self.play_range.currentIndex()
            if source == 'candidate' or mode == 2:
                indices = self.selected()
            elif mode == 1:
                number = self.project.notes_list[index]['page']
                indices = [i for i, note in enumerate(self.project.notes_list) if note['page'] == number]
            elif mode == 3:
                indices = list(range(index, len(self.project.notes_list)))
            else:
                indices = [index]
        if not indices or (source == 'candidate' and any(i not in self.candidates for i in indices)):
            return
        self.stop()
        self.ctx.playback_bus.request_stop(self)
        self._queue = [(index, source) for index in indices]
        self._queue_offset = 0
        self._playing = True
        self._play_queued()

    def _play_queued(self):
        if not self._playing or not self._queue:
            return
        index, source = self._queue[self._queue_offset]
        if source == 'candidate':
            candidate = self.candidates.get(index)
            if candidate is None:
                self.stop()
                return
            path = candidate['path']
        else:
            path = self.project.media_list[index]
        if not Path(path).is_file():
            self._audio_error('音频文件缺失，请重新生成这一段。')
            return
        note = self.project.notes_list[index]
        number = sum(item['page'] == note['page'] for item in self.project.notes_list[:index]) + 1
        self.playing_label.setText(f'第 {note["page"]} 页 · 第 {number} 段 · {"新配音" if source == "candidate" else "当前配音"}')
        self.compare_tabs.setCurrentItem(source)
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._position_changed(0)
        self.player.play()

    def _compare_source(self, source):
        self.play(indices=self.selected(), source=source)

    def toggle_play(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
            self.play(source=source)

    def navigate(self, offset):
        if not self._ensure_current() or not self.project.notes_list:
            return
        current = self._queue[self._queue_offset][0] if self._queue else (self.current() or 0)
        index = min(max(current + offset, 0), len(self.project.notes_list) - 1)
        self._select_index(index)
        self.play(indices=[index])

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing:
            serial = self._play_serial
            QTimer.singleShot(0, lambda: self._advance(serial))

    def _advance(self, serial):
        if serial != self._play_serial or not self._playing or not self._queue:
            return
        self._queue_offset += 1
        if self._queue_offset == len(self._queue):
            self._queue_offset = len(self._queue) - 1
            self._playing = False
            return
        self._play_queued()

    def _position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        index = self._queue[self._queue_offset][0] if self._queue else self.current()
        source = self._queue[self._queue_offset][1] if self._queue else (
            self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current')
        if not self._queue and index is not None and index < len(self.project.notes_list):
            note = self.project.notes_list[index]
            offset = sum(item['page'] == note['page'] for item in self.project.notes_list[:index]) + 1
            self.playing_label.setText(f'第 {note["page"]} 页 · 第 {offset} 段 · {"新配音" if source == "candidate" else "当前配音"}')
        duration = self.player.duration() / 1000 if not self.player.source().isEmpty() else 0
        if duration <= 0:
            if index is not None and 0 <= index < len(self.project.notes_duration_list):
                if source == 'candidate' and index in self.candidates:
                    duration = self.candidates[index]['duration']
                else:
                    duration = self.project.notes_duration_list[index]
        self.time_label.setText(f'{format_duration(position / 1000)} / {format_duration(duration)}')

    def _duration_changed(self, duration):
        self.seek.setRange(0, max(0, duration))
        self._position_changed(self.player.position())

    def _playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = SystemIcon.PAUSE if playing else SystemIcon.PLAY
        self.play_button.setIcon(icon.icon(Theme.DARK))
        self.play_button.setToolTip('暂停' if playing else '播放')
        active = state != QMediaPlayer.PlaybackState.StoppedState and bool(self._queue)
        self.table.setProperty('playingIndex', self._queue[self._queue_offset][0] if active else -1)
        self.table.setProperty('playingText', '试听中' if playing else '已暂停')
        self.table.viewport().update()

    def _seek(self, position):
        self.player.setPosition(position)

    def stop(self):
        self._play_serial += 1
        self._playing = False
        self._queue.clear()
        self.player.stop()
        self.player.setSource(QUrl())
        self._position_changed(0)

    def _stop_requested(self, requester):
        if requester is not self:
            self.stop()

    def _audio_error(self, message):
        get_logger().error('审听配音无法播放：%s', message)
        self.stop()
        self.message.setText(f'无法播放：{message}')

    def closeEvent(self, event):
        if self.task is not None:
            self._close_after_task = True
            self.task.requestInterruption()
            self.message.setText('正在结束配音准备，完成后关闭。')
            event.ignore()
            return
        if self.candidates or self.drafts:
            box = MessageBox('放弃未采用的调整？', '已经采用的配音会保留。', self)
            box.yesButton.setText('放弃并关闭')
            box.cancelButton.setText('继续调整')
            if not box.exec():
                event.ignore()
                return
        self.stop()
        self.candidates.clear()
        self.drafts.clear()
        try:
            self._directory.cleanup()
        except OSError:
            get_logger().debug('配音审听临时音频尚在释放，稍后清理', exc_info=True)
        self.hide()
        event.accept()

    def reject(self):
        self.close()
