"""按页组织片段，试听、对比并采用片段级配音调整。"""

import copy
from collections import Counter
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QSignalBlocker,
    Qt,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    RoundMenu,
    ScrollArea,
    Slider,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    Theme,
    TransparentPushButton,
    TransparentToolButton,
    VerticalSeparator,
    setCustomStyleSheet,
)

from app import paths
from app.audio import segments as segment_audio
from app.audio.playback import AudioOutputWatcher, SentencePlayback
from app.audio.sentences import load_sentences
from app.documents.statistics import format_duration
from app.logging_setup import get_logger
from app.workspaces import TemporaryWorkspace
from tasks.audio_review_task import AudioReviewTask
from ui.dialogs.audio_review_widgets import (
    SEGMENT_ROLE,
    AudioSourceSwitch,
    ReviewStatusLabel,
    SegmentDelegate,
    SentenceTranscript,
    playback_timestamp,
)
from ui.dialogs.base import ThemedDialog
from ui.dialogs.slide_preview import SlidePreview
from ui.icons import SystemIcon
from ui.playback_buttons import PlayButton, StepPlaybackButton
from ui.scrolling import configure_reading_scroll
from ui.waveform import WaveformSlider


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
        self._active_audio = None
        self._playing = False
        self._play_serial = 0
        self._inspector_route = 'script'
        self._row_by_index = {}
        self._row_layout_key = None
        paths.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        self._directory = TemporaryWorkspace(prefix='audio-review-', dir=paths.PREVIEW_DIR)
        self.setWindowTitle('配音审听')
        self.configure_workspace_size(1180, 760, minimum=(960, 600))

        self._build_ui()
        self._setup_playback()
        self.reload()

    def _build_ui(self):
        """组装顶部操作、片段列表、调整面板和底部播放器。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(14)
        self._build_toolbar(root)
        self._build_workspace(root)
        self._build_player(root)

    def _build_toolbar(self, root):
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
        self.generate_button = Action('使用设置页的音色生成试听', triggered=lambda: self.prepare(True))
        self.generate_button.setToolTip(
            '使用“设置”中选定的引擎、发音人和语音参数重新生成，再应用右侧调整。'
            '点击“采用新配音”后替换所选片段。'
        )
        self.more_menu.addAction(self.generate_button)
        self.restore_button = Action('恢复选中片段的原始配音', triggered=self.restore_original)
        self.more_menu.addAction(self.restore_button)
        self.more_menu.addSeparator()
        self.stop_action = Action('停止试听', triggered=self.stop)
        self.more_menu.addAction(self.stop_action)
        more_button.clicked.connect(
            lambda: self.more_menu.exec(more_button.mapToGlobal(more_button.rect().bottomLeft()))
        )
        top.addWidget(more_button)
        root.addLayout(top)

    def _build_workspace(self, root):
        workspace = CardWidget(self)
        workspace.setBorderRadius(10)
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        splitter = self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal, workspace)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet('QSplitter::handle { background: rgba(128,128,128,0.16); }')
        self._build_segment_list(splitter)
        self._build_inspector(splitter)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([780, 360])
        workspace_layout.addWidget(splitter)
        root.addWidget(workspace, 1)

    def _build_segment_list(self, splitter):
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
        configure_reading_scroll(self.table)
        self.table.setColumnCount(4)
        delegate = SegmentDelegate(self.table)
        delegate.play_requested.connect(self._play_segment)
        self.table.setItemDelegate(delegate)
        self.table.horizontalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, SegmentDelegate.PLAY_COLUMN_WIDTH)
        for column in (1, 3):
            self.table.setColumnHidden(column, True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table_style = 'TableWidget { background: transparent; border: none; }'
        setCustomStyleSheet(self.table, table_style, table_style)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.play())
        self.table.verticalScrollBar().valueChanged.connect(self._refresh_visible_sentences)
        self.table.viewport().installEventFilter(self)
        self.table.installEventFilter(self)
        middle_layout.addWidget(self.table, 1)
        self.empty_label = CaptionLabel('没有找到匹配的片段', middle)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        middle_layout.addWidget(self.empty_label, 1)
        splitter.addWidget(middle)

    def _build_inspector(self, splitter):
        right_pane = QWidget(splitter)
        right_pane.setMinimumWidth(340)
        right_pane.setMaximumWidth(420)
        pane_layout = QVBoxLayout(right_pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)
        self.inspector_scroll = ScrollArea(right_pane)
        configure_reading_scroll(self.inspector_scroll)
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inspector_scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
        )
        self.inspector_scroll.viewport().setAutoFillBackground(False)
        right = QWidget()
        right.setObjectName('audioReviewInspector')
        right.setStyleSheet('QWidget#audioReviewInspector { background: transparent; }')
        self.inspector_scroll.setWidget(right)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 20, 20, 12)
        right_layout.setSpacing(12)
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

        self._build_adjustment_controls(right, right_layout)
        pane_layout.addWidget(self.inspector_scroll, 1)
        pane_layout.addWidget(self._rule(right_pane))
        actions = QWidget(right_pane)
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(20, 12, 20, 16)
        actions_layout.setSpacing(10)
        self.compare_card = QWidget(actions)
        compare_layout = QHBoxLayout(self.compare_card)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.addWidget(CaptionLabel('对比试听', self.compare_card))
        compare_layout.addStretch()
        self.compare_tabs = AudioSourceSwitch(self.compare_card)
        self.compare_tabs.addItem('current', '当前配音', lambda: self._compare_source('current'))
        self.compare_tabs.addItem('candidate', '调整后', lambda: self._compare_source('candidate'))
        self.compare_tabs.setCurrentItem('current')
        compare_layout.addWidget(self.compare_tabs)
        actions_layout.addWidget(self.compare_card)
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
        self.render_button.setToolTip(
            '调整所选片段已有配音的语速、音量和留白，生成试听。'
            '点击“采用新配音”后替换原配音。')
        action_row.addWidget(self.render_button, 1)
        self.apply_button = PrimaryPushButton('采用新配音', actions)
        self.apply_button.clicked.connect(self.adopt)
        action_row.addWidget(self.apply_button, 1)
        actions_layout.addLayout(action_row)
        pane_layout.addWidget(actions)
        splitter.addWidget(right_pane)

    def _build_adjustment_controls(self, right, right_layout):
        self.speed = DoubleSpinBox(right)
        self.speed.setRange(0.5, 2)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setSuffix(' ×')
        self.speed.setFixedWidth(88)
        speed_row = QHBoxLayout()
        speed_row.addWidget(BodyLabel('配音语速', right))
        speed_row.addStretch()
        speed_row.addWidget(self.speed)
        speed_layout = QVBoxLayout()
        speed_layout.setSpacing(8)
        speed_layout.addLayout(speed_row)
        self.speed_slider = Slider(Qt.Orientation.Horizontal, right)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.valueChanged.connect(lambda value: self.speed.setValue(value / 100))
        self.speed.valueChanged.connect(
            lambda value: self.speed_slider.setValue(round(value * 100))
        )
        self.speed_slider.setValue(100)
        speed_layout.addWidget(self.speed_slider)
        right_layout.addLayout(speed_layout)
        self.volume = SpinBox(right)
        self.volume.setRange(0, 200)
        self.volume.setSuffix(' %')
        self.volume.setFixedWidth(88)
        volume_row = QHBoxLayout()
        volume_row.addWidget(BodyLabel('配音音量', right))
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
        self.inspector_tabs = AudioSourceSwitch(right)
        self.inspector_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.inspector_tabs.hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.inspector_tabs.addItem('script', '讲稿', lambda: self._set_inspector_view('script'))
        self.inspector_tabs.addItem('slides', '幻灯片', lambda: self._set_inspector_view('slides'))
        for item in self.inspector_tabs.items.values():
            item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.inspector_tabs.setCurrentItem('script')
        right_layout.addWidget(self.inspector_tabs)
        self.script_panel = QWidget(right)
        script_layout = QVBoxLayout(self.script_panel)
        script_layout.setContentsMargins(0, 0, 0, 0)
        script_layout.setSpacing(6)
        self.sentence_count = CaptionLabel(self.script_panel)
        self.sentence_count.hide()
        script_layout.addWidget(self.sentence_count, 0, Qt.AlignmentFlag.AlignRight)
        self.script_text = BodyLabel(self.script_panel)
        self.script_text.setWordWrap(True)
        self.script_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.script_text.setTextFormat(Qt.TextFormat.PlainText)
        self.script_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.script_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        script_layout.addWidget(self.script_text)
        self.transcript = SentenceTranscript(self.script_panel)
        self.transcript.setFont(self.script_text.font())
        self.transcript.activated.connect(self._play_sentence)
        self.transcript.hide()
        script_layout.addWidget(self.transcript)
        right_layout.addWidget(self.script_panel)
        self.slide_preview = SlidePreview(right, keep_session_on_hide=True)
        self.slide_preview.setFixedHeight(180)
        self.slide_preview.hide()
        right_layout.addWidget(self.slide_preview)
        self.message = ReviewStatusLabel(right)
        self.message.setWordWrap(True)
        self.message.setText('')
        right_layout.addWidget(self.message)
        self.progress = ProgressBar(right)
        self.progress.hide()
        right_layout.addWidget(self.progress)
        right_layout.addStretch(1)

    def _build_player(self, root):
        player = self.player_bar = CardWidget(self)
        player.setBorderRadius(10)
        player.setFixedHeight(72)
        player_layout = QHBoxLayout(player)
        player_layout.setContentsMargins(16, 10, 16, 10)
        player_layout.setSpacing(16)
        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.previous_button = StepPlaybackButton(True, player)
        self.previous_button.setToolTip('上一段并播放')
        self.previous_button.clicked.connect(lambda: self.navigate(-1))
        self.play_button = PlayButton(player)
        self.play_button.setToolTip('播放')
        self.play_button.clicked.connect(self.toggle_play)
        self.next_button = StepPlaybackButton(False, player)
        self.next_button.setToolTip('下一段并播放')
        self.next_button.clicked.connect(lambda: self.navigate(1))
        for button in (self.previous_button, self.play_button, self.next_button):
            transport.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        player_layout.addLayout(transport)
        separator = VerticalSeparator(player)
        separator.setFixedHeight(26)
        player_layout.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)

        timeline = QWidget(player)
        timeline.setObjectName('reviewTimeline')
        timeline.setFixedHeight(48)
        timeline.setMinimumWidth(220)
        timeline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        setCustomStyleSheet(
            timeline,
            'QWidget#reviewTimeline { background: rgba(0,0,0,0.022); border: 1px solid rgba(0,0,0,0.035); border-radius: 6px; }',
            'QWidget#reviewTimeline { background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.045); border-radius: 6px; }',
        )
        timeline_layout = QHBoxLayout(timeline)
        timeline_layout.setContentsMargins(12, 0, 12, 0)
        timeline_layout.setSpacing(10)
        self.elapsed_label = CaptionLabel(timeline)
        self.elapsed_label.setMinimumWidth(self.elapsed_label.fontMetrics().horizontalAdvance('00:00.00'))
        self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.elapsed_label.setToolTip('已播放时间')
        timeline_layout.addWidget(self.elapsed_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.seek = WaveformSlider(timeline)
        self.seek.sliderMoved.connect(self._seek)
        self.seek.failed.connect(self._waveform_failed)
        timeline_layout.addWidget(self.seek, 1)
        self.duration_label = CaptionLabel(timeline)
        self.duration_label.setMinimumWidth(self.duration_label.fontMetrics().horizontalAdvance('00:00.00'))
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.duration_label.setToolTip('片段总时长')
        timeline_layout.addWidget(self.duration_label, 0, Qt.AlignmentFlag.AlignVCenter)
        player_layout.addWidget(timeline, 1)

        root.addWidget(player)

    def _setup_playback(self):
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.sentence_playback = SentencePlayback(self.player, self)
        self.sentence_playback.changed.connect(self._sentence_changed)
        self.device_watcher = AudioOutputWatcher(self.player, self.output, '配音审听', parent=self)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._player_error, Qt.ConnectionType.QueuedConnection)
        self.ctx.playback_bus.stop_requested.connect(self._stop_requested)
        self.reviewer.audio_changed.connect(self.reload)
        self.reviewer.presentation_state_changed.connect(self._project_state_changed)

    @staticmethod
    def _rule(parent):
        rule = QFrame(parent)
        rule.setFixedHeight(1)
        rule.setStyleSheet('background: rgba(128,128,128,0.16); border: none;')
        return rule

    def eventFilter(self, obj, event):
        if (
            hasattr(self, 'table')
            and obj in (self.table, self.table.viewport())
            and event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange)
        ):
            self._layout_segments()
            self._refresh_visible_sentences()
        if hasattr(self, 'table') and obj is self.table.viewport():
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
                index = self.table.indexAt(event.position().toPoint())
                rect = self.table.visualRect(index)
                over_play = index.column() == 0 and self.table.itemDelegate().play_rect(rect).contains(event.position())
                if event.type() == QEvent.Type.MouseMove:
                    obj.setCursor(Qt.CursorShape.PointingHandCursor if over_play else Qt.CursorShape.ArrowCursor)
                elif not over_play:
                    self.table.itemDelegate().cancel_play_press()
            elif event.type() == QEvent.Type.Leave:
                obj.unsetCursor()
                self.table.itemDelegate().cancel_play_press()
        return super().eventFilter(obj, event)

    def _play_sentence(self, number):
        index = self.current()
        if index is None or len(self.selected()) != 1:
            return
        source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
        self.play(index=index, source=source)
        self.sentence_playback.seek_to(number)

    def _set_inspector_view(self, route):
        self._inspector_route = route
        self._update_slide_preview()

    def _update_slide_preview(self):
        index = self.current()
        source = self.project.presentation_path
        available = bool(source and Path(source).is_file())
        self.inspector_tabs.items['slides'].setEnabled(available)
        self.inspector_tabs.items['slides'].setToolTip('' if available else '关联 PPT 后可查看页面预览。')
        show_slides = available and self._inspector_route == 'slides'
        self.inspector_tabs.setCurrentItem('slides' if show_slides else 'script')
        self.script_panel.setVisible(not show_slides)
        self.sentence_count.setVisible(bool(self.transcript.sentences) and not show_slides)
        if not show_slides:
            self.slide_preview.hide()
        if show_slides:
            self.slide_preview.set_page(
                source, self.project.notes_list[index]['page'] if index is not None else None,
                self.ctx.app_settings.get('presentation_preference'),
            )
        self.slide_preview.setVisible(show_slides)

    def _sentence_timeline(self, index, source='current'):
        if source == 'candidate' and index in self.candidates:
            item = self.candidates[index]
            return load_sentences(item['path'], item.get('spoken_text', self.project.notes_list[index]['text']))
        spoken = self.project.audio_edits.get(index, {}).get('spoken_text', self.project.notes_list[index]['text'])
        return load_sentences(self.project.media_list[index], spoken)

    def _refresh_visible_sentences(self, *_):
        if self._syncing or self.revision is None or not self.table.isVisible() or not self.table.rowCount():
            return
        selected = set(self.selected())
        source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
        first = max(0, self.table.rowAt(0))
        last = self.table.rowAt(self.table.viewport().height() - 1)
        last = last if last >= 0 else self.table.rowCount() - 1
        for row in range(first, last + 1):
            item = self.table.item(row, 2)
            index = item.data(Qt.ItemDataRole.UserRole)
            route = source if index in selected and index in self.candidates else 'current'
            if route == 'candidate':
                candidate = self.candidates[index]
                path = candidate['path']
                spoken = candidate.get('spoken_text', self.project.notes_list[index]['text'])
            else:
                path = self.project.media_list[index]
                spoken = self.project.audio_edits.get(index, {}).get('spoken_text', self.project.notes_list[index]['text'])
            key = (str(path), spoken, route)
            data = dict(item.data(SEGMENT_ROLE) or {})
            if data.get('sentence_key') != key:
                data['sentences'] = self._sentence_timeline(index, route).spans
                data['sentence_source'] = route
                data['sentence_key'] = key
                item.setData(SEGMENT_ROLE, data)

    def _refresh_sentence_view(self):
        self._refresh_visible_sentences()
        selected = self.selected()
        source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
        index = self.current()
        timeline = self._sentence_timeline(index, source) if len(selected) == 1 and index is not None else None
        self.transcript.set_sentences(timeline.spans if timeline else ())
        self.transcript.setVisible(bool(timeline))
        self.script_text.setVisible(not bool(timeline))
        self.sentence_count.setText(f'{len(timeline)} 句' if timeline else '')
        self.sentence_count.setVisible(bool(timeline) and self.inspector_tabs.currentRouteKey() == 'script')
        self._highlight_sentence()
        self._layout_segments()

    def _layout_segments(self):
        if self._syncing or getattr(self, '_laying_out_rows', False):
            return
        width = max(80, self.table.viewport().width() - self.table.columnWidth(0)
                    - SegmentDelegate.TEXT_LEFT - SegmentDelegate.TEXT_RIGHT)
        key = (width, self.table.font().toString())
        if key == self._row_layout_key:
            return
        self._row_layout_key = key
        self._laying_out_rows = True
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 2)
                if item is None:
                    continue
                data = item.data(SEGMENT_ROLE) or {}
                height = SegmentDelegate.row_height(
                    data.get('text', ''), width, self.table.font()
                )
                if self.table.rowHeight(row) != height:
                    self.table.setRowHeight(row, height)
        finally:
            self._laying_out_rows = False

    def showEvent(self, event):
        super().showEvent(event)
        if not Path(self._directory.name).is_dir():
            self._directory = TemporaryWorkspace(
                prefix='audio-review-', dir=paths.PREVIEW_DIR
            )
        self._inspector_route = 'script'
        if not self.reload():
            self._refresh_segment_states()
            self._selection_changed()

    def hideEvent(self, event):
        self.seek.release()
        self._inspector_route = 'script'
        self._update_slide_preview()
        self.slide_preview.release()
        super().hideEvent(event)

    def selected(self):
        return [
            int(self.table.item(row.row(), 1).data(Qt.ItemDataRole.UserRole))
            for row in sorted(
                self.table.selectionModel().selectedRows(), key=lambda item: item.row()
            )
        ]

    def current(self):
        row = self.table.currentRow()
        selected = [item.row() for item in self.table.selectionModel().selectedRows()]
        if not selected:
            return None
        if row not in selected:
            row = min(selected)
        item = self.table.item(row, 1) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _project_state_changed(self):
        """生成线程退出、配音就绪后，恢复暂时不可用的审听窗口。"""
        if (self.isVisible() and self.revision is None and self.project.audio_loaded
                and not self.reviewer.is_busy()):
            self.reload()

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
            self._row_by_index.clear()
            self._row_layout_key = None
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
        self.message.setText('')
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
        for number, count in sorted(Counter(item['page'] for item in self.project.notes_list).items()):
            self.pages.addItem(f'第 {number} 页（{count} 段）', userData=number)
        self.pages.setCurrentIndex(0)
        self.pages.blockSignals(False)
        self.total_label.setText(
            f'{len(self.project.notes_list)} 段 · {format_duration(sum(self.project.notes_duration_list))}'
        )
        self._populate_segments(preferred_index=min(self.project.current_index, max(0, len(self.project.notes_list) - 1)))
        return True

    def _populate_segments(self, *_, preferred_index=None):
        if self._syncing or self.revision is None:
            return
        previous = self.current() if preferred_index is None else preferred_index
        selection = set(self.selected()) if preferred_index is None else set()
        number = self.pages.currentData()
        query = self.search.text().strip().casefold()
        counts, matches = {}, []
        for index, note in enumerate(self.project.notes_list):
            counts[note['page']] = counts.get(note['page'], 0) + 1
            spoken = self.project.audio_edits.get(index, {}).get('spoken_text', note['text'])
            if (number is None or note['page'] == number) and query in (note['text'] + '\n' + spoken).casefold():
                matches.append((index, note, spoken, counts[note['page']]))
        self._syncing = True
        self.table.setUpdatesEnabled(False)
        self._row_by_index.clear()
        self._row_layout_key = None
        try:
            self.table.setRowCount(0)
            self.table.setRowCount(len(matches))
            for row, (index, note, spoken, offset) in enumerate(matches):
                self._row_by_index[index] = row
                play_item = QTableWidgetItem()
                play_item.setData(Qt.ItemDataRole.UserRole, index)
                play_item.setData(Qt.ItemDataRole.AccessibleTextRole, '播放这一段')
                play_item.setToolTip('播放这一段')
                self.table.setItem(row, 0, play_item)
                for column, value in (
                    (1, f'{note["page"]} · {offset}'),
                    (2, note['text'] or '（空白片段）'),
                    (3, format_duration(self.project.notes_duration_list[index])),
                ):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    if column == 2:
                        item.setData(SEGMENT_ROLE, {
                            'heading': f'第 {note["page"]} 页 · 第 {offset} 段',
                            'text': spoken,
                            'duration': format_duration(self.project.notes_duration_list[index]),
                            'status': self._segment_status(index),
                        })
                    self.table.setItem(row, column, item)
            self.table.setVisible(bool(matches))
            self.empty_label.setVisible(not matches)
            if matches:
                row = self._row_by_index.get(previous, 0)
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 1))
                matching = [self._row_by_index[index] for index in selection if index in self._row_by_index]
                if matching:
                    self.table.clearSelection()
                    for row in matching:
                        self.table.selectionModel().select(
                            self.table.model().index(row, 0),
                            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                        )
        finally:
            self._syncing = False
            self.table.setUpdatesEnabled(True)
        self._selection_changed()

    def _select_index(self, index, *, reveal=True):
        row = self._row_by_index.get(index)
        if row is not None:
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 1))
        elif reveal:
            with QSignalBlocker(self.search), QSignalBlocker(self.pages):
                self.search.clear()
                self.pages.setCurrentIndex(0)
            self._populate_segments(preferred_index=index)

    def focus_segment(self, index):
        """从当前讲稿进入时定位到对应片段，保留未采用的调整。"""
        if self.task is not None:
            return
        self.reload()
        if self.revision is not None and 0 <= index < len(self.project.notes_list):
            self._select_index(index)

    def _play_segment(self, index):
        if index not in self.selected():
            self._select_index(index)
        self.play(index=index)

    def _draft(self, index):
        edit = self.project.audio_edits.get(index, {})
        return self.drafts.get(
            index,
            {
                'settings': segment_audio.settings(edit.get('settings')),
                'spoken_text': edit.get('spoken_text', self.project.notes_list[index]['text']),
            },
        )

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
            status = self._segment_status(index)
            if data.get('status') != status:
                data['status'] = status
                item.setData(SEGMENT_ROLE, data)

    def _selection_changed(self):
        if self._syncing:
            return
        index, selected = self.current(), self.selected()
        self._syncing = True
        if index is not None:
            note = self.project.notes_list[index]
            offset = (
                sum(item['page'] == note['page'] for item in self.project.notes_list[:index]) + 1
            )
            self.selection_label.setText(
                f'第 {note["page"]} 页 · 第 {offset} 段'
                if len(selected) <= 1
                else f'已选择 {len(selected)} 个片段'
            )
            duration = sum(self.project.notes_duration_list[i] for i in selected)
            speaker = str(self.project.generation_speaker or '').strip()
            if len(selected) == 1:
                profile = self.project.audio_edits.get(index, {}).get('voice_profile', {})
                speaker = str(profile.get('voice_name') or speaker)
            self.selection_detail.setText(f'{speaker or "当前配音"}　{format_duration(duration)}')
            self.selection_detail.setWordWrap(True)
            edit = self.project.audio_edits.get(index, {}) if len(selected) == 1 else {}
            parts = []
            for key, label, suffix, default in (
                ('speed', '语速 ', '×', 1),
                ('volume', '音量 ', '%', 100),
                ('leading_ms', '开头 ', ' 毫秒', 0),
                ('trailing_ms', '结尾 ', ' 毫秒', 0),
            ):
                value = edit.get('settings', {}).get(key, default)
                if value != default:
                    parts.append(f'{label}{value:g}{suffix}')
            self.edit_summary.setText('，'.join(parts) or ('已重新配音' if edit else ''))
            self.edit_summary.setVisible(bool(edit))
            self.selection_label.setToolTip(
                '批量调整只应用修改的参数，其他设置分别保留。' if len(selected) > 1 else ''
            )
            draft = self._draft(index)
            if len(selected) == 1:
                self.script_text.setText(draft['spoken_text'])
            else:
                texts = []
                for selected_index in selected:
                    selected_note = self.project.notes_list[selected_index]
                    selected_offset = (
                        sum(
                            item['page'] == selected_note['page']
                            for item in self.project.notes_list[:selected_index]
                        )
                        + 1
                    )
                    texts.append(
                        f'第 {selected_note["page"]} 页 · 第 {selected_offset} 段\n'
                        f'{self._draft(selected_index)["spoken_text"]}'
                    )
                self.script_text.setText('\n\n'.join(texts))
            for control, key in (
                (self.speed, 'speed'),
                (self.volume, 'volume'),
                (self.leading, 'leading_ms'),
                (self.trailing, 'trailing_ms'),
            ):
                control.setValue(draft['settings'][key])
        else:
            self.selection_label.setText('选择一个片段')
            self.selection_detail.clear()
            self.edit_summary.hide()
            self.script_text.clear()
        self._syncing = False
        self._update_slide_preview()
        self._layout_segments()
        self._update_buttons()
        if (
            hasattr(self, 'player')
            and self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
        ):
            if self._active_audio and self._active_audio[0] != index:
                self.stop()
            self._position_changed(0)
        self._refresh_waveform()

    def _controls(self):
        return segment_audio.settings(
            {
                'speed': self.speed.value(),
                'volume': self.volume.value(),
                'leading_ms': self.leading.value(),
                'trailing_ms': self.trailing.value(),
            }
        )

    def _draft_changed(self, *_):
        if self._syncing:
            return
        selected = self.selected()
        if self._active_audio and self._active_audio[0] in selected and self._active_audio[1] == 'candidate':
            self.stop()
        options = self._controls()
        changed_key = next(
            (
                key
                for control, key in (
                    (self.speed, 'speed'),
                    (self.volume, 'volume'),
                    (self.leading, 'leading_ms'),
                    (self.trailing, 'trailing_ms'),
                )
                if self.sender() is control
            ),
            None,
        )
        for index in selected:
            previous = self._draft(index)
            values = (
                {**previous['settings'], changed_key: options[changed_key]}
                if changed_key
                else options
            )
            draft = {'settings': values, 'spoken_text': previous['spoken_text']}
            edit = self.project.audio_edits.get(index, {})
            baseline = {
                'settings': segment_audio.settings(edit.get('settings')),
                'spoken_text': edit.get('spoken_text', self.project.notes_list[index]['text']),
            }
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
        self.discard_button.setEnabled(
            ready
            and any(index in self.candidates or index in self.drafts for index in selected)
            and idle
        )
        self.discard_button.setVisible(
            bool(
                ready
                and any(index in self.candidates or index in self.drafts for index in selected)
            )
        )
        self.restore_button.setEnabled(
            ready and idle and any(index in self.project.audio_edits for index in selected)
        )
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
        self._refresh_sentence_view()

    def _ensure_current(self):
        if (
            self.reviewer.is_busy()
            or not self.project.audio_loaded
            or self.revision != self.reviewer.audio_revision()
        ):
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
            self.play()
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
                jobs.append(
                    {
                        'index': index,
                        'base_path': base_path,
                        'regenerated': needs_generation,
                        'engine': job_engine,
                        'settings': draft['settings'],
                        'spoken_text': draft['spoken_text'],
                        'voice_profile': segment_audio.public_profile(
                            job_engine.get_generation_profile()
                        )
                        if job_engine
                        else edit.get(
                            'voice_profile',
                            segment_audio.public_profile(self.project.generation_profile),
                        ),
                    }
                )
            self.stop()
            self._job_revision = self.revision
            self.task = AudioReviewTask(jobs, self._directory.name, parent=self)
            self.task.ready.connect(self._prepared)
            self.task.failed.connect(self._failed)
            self.task.progress.connect(self._task_progress)
            self.task.finished.connect(self._task_finished)
            self.progress.setValue(0)
            self.progress.show()
            self.message.setText('正在生成试听配音…')
            self.task.start()
            self._update_buttons()
        except Exception as exc:
            self._failed(str(exc))

    @Slot(object)
    def _prepared(self, results):
        if (
            self._close_after_task
            or self._job_revision != self.revision
            or self._job_revision != self.reviewer.audio_revision()
        ):
            return
        self.candidates.update(results)
        self._refresh_segment_states(results)
        self.message.setText('')
        if self.current() in results:
            self.play(source='candidate')

    @Slot(int, int)
    def _task_progress(self, done, total):
        self.progress.setValue(round(done * 100 / max(1, total)))

    @Slot(str)
    def _failed(self, message):
        get_logger().error('配音审听准备失败：%s', message)
        self.message.setText(f'配音准备失败：{message}')

    @Slot()
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
        return segment_audio.reference(
            self.project.note_cache_keys[index],
            self.project.note_cache_exts[index],
            self.project.notes_duration_list[index],
        )

    def _snapshot(self, indices):
        return {
            index: {
                'path': self.project.media_list[index],
                **self._reference(index),
                'edit': copy.deepcopy(self.project.audio_edits.get(index)),
            }
            for index in indices
        }

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
        self.total_label.setText(
            f'{len(self.project.notes_list)} 段 · {format_duration(sum(self.project.notes_duration_list))}'
        )
        self._populate_segments()

    def _notify_success(self, text):
        self.message.setText('')
        InfoBar.success(
            title=text,
            content='',
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1800,
            parent=self,
        )

    def adopt(self):
        if not self._ensure_current():
            return
        try:
            updates = {}
            for index in self.selected():
                candidate = self.candidates[index]
                key, ext, path = segment_audio.store_audio(
                    candidate['path'], self.reviewer.audio_cache_path
                )
                old = self.project.audio_edits.get(index, {})
                original = old.get('original')
                if not original:
                    original_key, original_ext, _original_path = segment_audio.store_audio(
                        self.project.media_list[index], self.reviewer.audio_cache_path
                    )
                    original = segment_audio.reference(
                        original_key, original_ext, self.project.notes_duration_list[index]
                    )
                if candidate['regenerated'] or not old.get('base'):
                    base_key, base_ext, _path = segment_audio.store_audio(
                        candidate['base_path'], self.reviewer.audio_cache_path
                    )
                else:
                    base = old['base']
                    base_key, base_ext = base['key'], base['ext']
                edit = {
                    'text': self.project.notes_list[index]['text'],
                    'profile_id': segment_audio.profile_id(self.project.generation_profile),
                    'original': copy.deepcopy(original),
                    'base': segment_audio.reference(base_key, base_ext, candidate['base_duration']),
                    'settings': candidate['settings'],
                    'spoken_text': candidate['spoken_text'],
                    'voice_profile': candidate['voice_profile'],
                    'audio_key': key,
                    'audio_ext': ext,
                }
                updates[index] = {
                    'path': path,
                    'key': key,
                    'ext': ext,
                    'duration': candidate['duration'],
                    'edit': edit,
                }
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
                    updates[index] = {
                        **original,
                        'path': segment_audio.cache_path(self.reviewer.audio_cache_path, original),
                        'edit': None,
                    }
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
                keys.update(
                    ref['key']
                    for ref in segment_audio.references({'audio_edit': update.get('edit')})
                )
        return keys

    def play(self, index=None, source='current'):
        if not self._ensure_current():
            return
        if index is None:
            index = self.current()
        if index is None or not 0 <= index < len(self.project.media_list):
            return
        if source == 'candidate' and index not in self.candidates:
            return
        self.stop()
        self.ctx.playback_bus.request_stop(self)
        self._active_audio = (index, source)
        self._playing = True
        self._play_current()

    def _play_current(self):
        if not self._playing or self._active_audio is None:
            return
        index, source = self._active_audio
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
        self.compare_tabs.setCurrentItem(source)
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._refresh_waveform()
        self._refresh_sentence_view()
        self._position_changed(0)
        self.player.play()

    def _sentence_changed(self):
        self._highlight_sentence()
        self._position_changed(self.player.position())

    def _highlight_sentence(self):
        current = None
        if (self._active_audio and self._active_audio[0] == self.current()
                and self.sentence_playback.current is not None):
            current = self.sentence_playback.current.number
        self.transcript.set_current(current)

    def _compare_source(self, source):
        self.play(source=source)

    def toggle_play(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        else:
            source = (
                self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
            )
            self.play(source=source)

    def navigate(self, offset):
        if not self._ensure_current() or not self.project.notes_list:
            return
        current = self._active_audio[0] if self._active_audio else (self.current() or 0)
        index = min(max(current + offset, 0), len(self.project.notes_list) - 1)
        self._select_index(index)
        self.play(index=index)

    @Slot(QMediaPlayer.MediaStatus)
    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing:
            serial = self._play_serial
            QTimer.singleShot(0, lambda: self._finish_playback(serial))

    def _finish_playback(self, serial):
        if serial != self._play_serial or not self._playing:
            return
        self._playing = False

    def _playback_selection(self):
        if self._active_audio is not None:
            return self._active_audio
        source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
        return self.current(), source

    @Slot('qint64')
    def _position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        index, source = self._playback_selection()
        duration = self.player.duration() / 1000 if not self.player.source().isEmpty() else 0
        if duration <= 0:
            if index is not None and 0 <= index < len(self.project.notes_duration_list):
                if source == 'candidate' and index in self.candidates:
                    duration = self.candidates[index]['duration']
                else:
                    duration = self.project.notes_duration_list[index]
        self.elapsed_label.setText(playback_timestamp(position / 1000))
        self.elapsed_label.setAccessibleName(f'已播放 {format_duration(position / 1000)}')
        self.duration_label.setText(playback_timestamp(duration))
        self.duration_label.setAccessibleName(f'片段总时长 {format_duration(duration)}')

    @Slot('qint64')
    def _duration_changed(self, duration):
        self.seek.setRange(0, max(0, duration))
        self._position_changed(self.player.position())

    def _refresh_waveform(self):
        if not hasattr(self, 'player') or not self.isVisible():
            return
        index, source = self._playback_selection()
        if self.revision is None or index is None or not 0 <= index < len(self.project.media_list):
            self.seek.set_source(None)
            return
        if source == 'candidate' and index in self.candidates:
            item = self.candidates[index]
            path, duration = item['path'], item['duration']
        else:
            path, duration = self.project.media_list[index], self.project.notes_duration_list[index]
        self.seek.set_source(path, duration * 1000)
        self.seek.set_markers(span.start_ms for span in self._sentence_timeline(index, source).spans)
        if self.player.source().isEmpty():
            self.seek.setRange(0, round(duration * 1000))

    def _waveform_failed(self, _message):
        if self.isVisible():
            InfoBar.warning('波形未能加载', '可继续试听和定位播放。',
                            isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=self)

    @Slot(QMediaPlayer.PlaybackState)
    def _playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = SystemIcon.PAUSE if playing else SystemIcon.PLAY
        self.play_button.setIcon(icon.icon(Theme.DARK))
        self.play_button.setToolTip('暂停' if playing else '播放')
        active = state != QMediaPlayer.PlaybackState.StoppedState and self._active_audio is not None
        self.table.setProperty('playingIndex', self._active_audio[0] if active else -1)
        self.table.setProperty('playingText', '试听中' if playing else '已暂停')
        self.table.viewport().update()

    def _seek(self, position):
        if self.player.source().isEmpty():
            index = self.current()
            if index is None:
                return
            source = self.compare_tabs.currentRouteKey() if self.compare_card.isVisible() else 'current'
            dragging = self.seek.isSliderDown()
            self.play(index=index, source=source)
            self.seek.setSliderDown(dragging)
        self.sentence_playback.seek_position(position)

    def stop(self):
        self._play_serial += 1
        self._playing = False
        self._active_audio = None
        self.seek.setSliderDown(False)
        self.player.stop()
        self.player.setSource(QUrl())
        self._position_changed(0)
        self._refresh_waveform()

    def _stop_requested(self, requester):
        if requester is not self:
            self.stop()

    def _audio_error(self, message):
        get_logger().error('审听配音无法播放：%s', message)
        self.stop()
        self.seek.release()
        self.message.setText(f'无法播放：{message}')

    @Slot(QMediaPlayer.Error, str)
    def _player_error(self, code, message):
        if code != QMediaPlayer.Error.NoError and self.player.error() != QMediaPlayer.Error.NoError:
            self._audio_error(message)

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
        self.seek.release()
        try:
            self._directory.cleanup()
        except OSError:
            get_logger().debug('配音审听临时音频尚在释放，稍后清理', exc_info=True)
        self.hide()
        event.accept()

    def reject(self):
        self.close()
