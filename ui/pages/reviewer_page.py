"""实现讲稿导入、分段、音频生成、会话恢复和播放。"""

import copy
import ctypes
import json
import re
from collections import deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFileDialog, QSizePolicy, QWidget
from qfluentwidgets import (
    Action,
    FluentIcon,
    HyperlinkLabel,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    MessageBox,
    PushButton,
    Theme,
    ToolTipFilter,
)

from app import hotkeys, paths
from app.app_context import AppContext
from app.audio import segments as segment_audio
from app.audio.captions import SENTENCE_MODE
from app.audio.countdown import PlaybackCountdown
from app.audio.playback import AudioOutputWatcher
from app.audio.sentences import load_sentences
from app.documents import io as script_io
from app.documents import package as project_package
from app.documents.statistics import build_statistics
from app.documents.text import replace_spoken_text, spoken_notes, spoken_text
from app.logging_setup import get_logger, log_error
from app.presentation import animation as animation_check
from app.presentation import link as presentation_link
from app.presentation import slideshow, window_target
from app.presentation.monitor import PresentationMonitor, changed_pages, file_stamp
from app.presentation.playback import SlideshowPlayback
from app.project import history as project_history
from app.project.generation import GenerationWorkflow
from app.project.history import MissingAudioError
from app.project.model import ProjectSession, UnavailableReason
from tasks.audio_generation_task import AudioGenerationTask
from ui import icons, theme
from ui.dialogs.animation_check_dialog import AnimationCheckDialog
from ui.dialogs.audio_review_dialog import AudioReviewDialog
from ui.dialogs.edit_mark_dialog import EditMarkMessageBox
from ui.dialogs.ppt_import_dialog import PptImportDialog
from ui.dialogs.ppt_update_dialog import PptUpdateDialog
from ui.dialogs.script_statistics_dialog import ScriptStatisticsDialog
from ui.dialogs.session_history_dialog import SessionHistoryDialog
from ui.icon_menu import IconMenu
from ui.manuscript_preview import ManuscriptPreview
from ui.tooltips import TextToolTipFilter
from ui.widget_state import active_modal_dialog, set_enabled
from Ui_mainwindow import Ui_mainwindow

# 翻页只需向前台窗口发送一次 PageDown。为此引入 pyautogui 会连带装载
# Pillow 与 tkinter，启动多花约 240 毫秒，打包也多出二十余兆，因此直接调用 Win32。
_VK_NEXT = 0x22           # Page Down
_KEYEVENTF_KEYUP = 0x0002


def _press_page_down():
    """向当前前台窗口发送一次 PageDown 按键。"""
    user32 = ctypes.windll.user32
    user32.keybd_event(_VK_NEXT, 0, 0, 0)
    user32.keybd_event(_VK_NEXT, 0, _KEYEVENTF_KEYUP, 0)


class ElidedLabelText(QObject):
    """按标签的可用宽度省略显示，完整内容放入悬停提示。

    标签不再按文字长度撑开布局，窗口变窄时同行的其它控件不会被挤出可视区域。
    """

    def __init__(self, label):
        super().__init__(label)
        self.label = label
        self.text = ''
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, label.sizePolicy().verticalPolicy())
        label.installEventFilter(self)

    def set_text(self, text: str):
        """设置完整文本并刷新显示。"""
        self.text = text
        self.label.setToolTip(text)
        self.refresh()

    def refresh(self):
        """按当前宽度重新计算省略后的文本。"""
        width = max(self.label.width(), 80)
        self.label.setText(self.label.fontMetrics().elidedText(
            self.text, Qt.TextElideMode.ElideMiddle, width))

    # 字体变化会改变文字度量，省略位置需要同步重算。
    WATCHED = (QEvent.Type.Resize, QEvent.Type.FontChange)

    def eventFilter(self, obj, event):
        if event.type() in self.WATCHED:
            self.refresh()
        return False


class PPTReviewer(QWidget, Ui_mainwindow):
    """协调讲稿导入、分段、音频生成、会话恢复和连续播放。"""

    presentation_state_changed = Signal()
    sync_playback_changed = Signal(bool)
    countdown_preferences_changed = Signal(bool, int)
    playback_preferences_lock_changed = Signal(bool)
    audio_changed = Signal()
    minimum_home_height_changed = Signal(int)

    # 仅接受“页码_序号”命名的正文音频，避免倒计时或试听文件混入播放列表。
    _AUDIO_NAME_RE = re.compile(r'^(\d+)_(\d+)$')

    def __init__(self, context: AppContext, parent=None, *, project=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.project = project if project is not None else ProjectSession()
        if not self.project.notes:
            self.project.script_comments_enabled = bool(context.app_settings.get('script_comments_enabled'))
        self.generation = GenerationWorkflow(self.project)
        self.setupUi(self)
        self.verticalLayout_11.setSpacing(theme.PAGE_SECTION_SPACING)
        for layout, title in ((self.verticalLayout_2, self.SubtitleLabel),
                              (self.verticalLayout_6, self.SubtitleLabel_2)):
            layout.setSpacing(theme.PAGE_CARD_SPACING)
            theme.space_page_section(layout, title)
        self.infoPushButton = self.playbackOverview.statistics_button
        self._overview_key = None
        self.playbackOverview.page_requested.connect(
            lambda value: self._jump_to_position('page', value, restart_current=True))
        controls = self.playCardWidget_3
        self.playButton = controls.play_button
        self.previousPageButton = controls.previous_button
        self.nextPageButton = controls.next_button
        self.resetButton = controls.reset_button
        self.returnPositionButton = controls.return_button
        self._navigation_history = deque(maxlen=20)
        self._navigation_notes = self.project.notes_list
        self.currentSwitch = controls.countdown_switch
        self.currentSpinBox = controls.countdown_seconds
        controls.countdown_seconds.invalid_input.connect(
            lambda message: self.create_warning_info_bar('无法设置倒计时', message))
        self.playing_script_preview = ManuscriptPreview(self.scrollAreaWidgetContents_3)
        self._preview_notes = None
        self.playing_script_preview.page_requested.connect(lambda value: self._jump_to_position('page', value))
        self.playing_script_preview.segment_requested.connect(lambda value: self._jump_to_position('segment', value))
        self.playing_script_preview.review_requested.connect(
            lambda: self.open_audio_review(current_segment=True))
        self.playing_script_preview.navigation_error.connect(
            lambda message: self.create_warning_info_bar('无法跳转', message))
        self.verticalLayout_6.insertWidget(
            self.verticalLayout_6.indexOf(self.playCardWidget_3), self.playing_script_preview)
        self.bgScrollArea.viewport().installEventFilter(self)
        self.scrollAreaWidgetContents_3.installEventFilter(self)
        self.bgScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bgScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.CaptionLabel_5.setText('将一页讲稿分为多段，与幻灯片的点击动画对应')
        theme.wrap_title_with_help(
            self.verticalLayout_7, self.notesPathLabel_5,
            '开启同步翻页后，每读完一段发送一次翻页，用于触发幻灯片中的下一个动画。默认分隔符为 ●。')
        self.notes_path_text = ElidedLabelText(self.notesPathLabel)
        self.notes_path_text.set_text(self.notesPathLabel.text())
        self.notesPathLabel.installEventFilter(TextToolTipFilter(self.notesPathLabel))
        self.statusLabel.installEventFilter(ToolTipFilter(self.statusLabel))
        self.IconInfoBadge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.IconInfoBadge.installEventFilter(ToolTipFilter(self.IconInfoBadge))

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.player.playbackStateChanged.connect(self._refresh_playing_script)
        self.player.positionChanged.connect(self._refresh_overview_position)
        self.audio_changed.connect(self._refresh_playing_script)
        self.player.errorOccurred.connect(self.on_playback_error, Qt.ConnectionType.QueuedConnection)
        self.device_watcher = AudioOutputWatcher(self.player, self.audio_output, '播放器', parent=self)
        self.ctx.playback_bus.stop_requested.connect(self.on_stop_requested)

        self.countdown = PlaybackCountdown(self)
        self.countdown.changed.connect(self._refresh_playing_script)
        self.countdown.finished.connect(self.play_notes)
        self._presentation_state = None
        self._ppt_update_bar = None
        self._resume_bar = None
        self._resume_waiting_for_dialog = False
        self.audio_review_dialog = None
        self.is_play_notes = False
        self._playing_sequence = False
        self._playback_preferences_locked = False
        self._audio_only_playback = False
        self._advancing_show = False
        self._confirming_generation = False

        self.partingIconWidget.setIcon(icons.SystemIcon.LIST)
        self.fileIconWidget.setIcon(icons.SystemIcon.FOLDER)
        self.generate_audio_link = HyperlinkLabel('生成配音', self.widget_5)
        self.generate_audio_link.setFont(self.statusLabel.font())
        self.generate_audio_link.setFixedHeight(20)
        self.generate_audio_link.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.generate_audio_link.clicked.connect(lambda: self.regenerate())
        self.generate_audio_link.hide()
        self.statusLabel.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.horizontalLayout_2.addSpacing(8)
        self.horizontalLayout_2.addWidget(self.generate_audio_link)
        self.horizontalLayout_2.addStretch()
        self.IndeterminateProgressBar.setVisible(False)

        icons.apply(self.getFileButton, ':/image/image/ppt.svg')
        self.getFileButton.setToolTip('也可将 PPTX、Word、JSON 讲稿或工程包拖入窗口')
        self.file_button_menu = IconMenu(parent=self)
        self.file_button_menu.addAction(icons.apply(
            Action('导入 Word 讲稿', triggered=self.import_word), ':/image/image/word.svg'))
        self.file_button_menu.addAction(
            Action(icons.SystemIcon.DOCUMENT, '导入 JSON 讲稿', triggered=self.import_data_script)
        )
        self.file_button_menu.addAction(
            Action(icons.SystemIcon.FOLDER, '导入工程包', triggered=self.import_package)
        )
        self.file_button_menu.addSeparator()
        self.file_button_menu.addAction(
            Action(icons.SystemIcon.HISTORY, '历史记录列表', triggered=self.show_session_history_dialog))
        # 上半部分导入新内容，分隔线下方的操作只作用于当前讲稿。
        self.file_button_menu.addSeparator()
        self.update_ppt_action = Action(
            icons.SystemIcon.UPDATE, '更新 PPT 讲稿', triggered=self.update_ppt_notes)
        self.file_button_menu.addAction(self.update_ppt_action)
        self.import_notes_action = Action(
            icons.SystemIcon.DOCUMENT, '从文件更新讲稿', triggered=self.import_notes_from_file)
        self.import_notes_action.setToolTip('按页码更新讲稿，保留当前关联的 PPT。支持 PowerPoint、Word 和 JSON。')
        self.file_button_menu.addAction(self.import_notes_action)
        self.regenerate_action = Action(
            icons.SystemIcon.RESET, '忽略缓存重新生成', triggered=self.force_regenerate)
        self.file_button_menu.addAction(self.regenerate_action)
        self.getFileButton.setFlyout(self.file_button_menu)

        self.bgScrollArea.enableTransparentBackground()

        self.playButton.clicked.connect(self.toggle_play)
        self.previousPageButton.clicked.connect(lambda: self._change_page(-1))
        self.nextPageButton.clicked.connect(lambda: self._change_page(1))
        self.resetButton.clicked.connect(lambda: self.reset_audio())
        self.returnPositionButton.clicked.connect(self.return_to_previous_position)
        self.getFileButton.clicked.connect(self.import_pptx)
        self.editMarkPushButton.clicked.connect(self.show_edit_mark_dialog)
        self.infoPushButton.clicked.connect(self.show_info_dialog)

        self.next_timer = QTimer(self)
        self.next_timer.timeout.connect(self.timeout_play_next_audio)

        paths.ensure_runtime_dirs()
        self.wav_temp_path = paths.TEMP_DIR
        self.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.session_root_path = paths.SESSION_DIR

        self.save_thread = AudioGenerationTask(self.ctx.tts_engine, self)
        self.save_thread.signal_import_index.connect(self.thread_print_index)
        self.save_thread.signal_progress.connect(self.thread_generation_progress)
        self.save_thread.signal_finish.connect(self.thread_save_finish)
        self.save_thread.signal_error.connect(self.thread_save_error)
        self.save_thread.finished.connect(self.refresh_presentation_state)
        self.save_thread.finished.connect(self.remember_session)
        self.save_thread.finished.connect(self.check_import)

        self.playback_show = SlideshowPlayback(self)
        self.playback_show.ready.connect(self._playback_show_ready)
        self.playback_show.failed.connect(self._playback_prepare_failed)
        self.playback_show.restart_required.connect(self._playback_restart_required)

        self.presentation_monitor = PresentationMonitor(self)
        self.presentation_monitor.change_detected.connect(self._notify_ppt_update)
        self.presentation_monitor.unchanged.connect(self._dismiss_ppt_update)
        self.presentation_monitor.comments_changed.connect(self._sync_ppt_comments)
        self.presentation_monitor.availability_changed.connect(self._poll_presentation)
        self.presentation_monitor.can_check = lambda: bool(self.project.notes) and not self.is_busy()
        self.save_thread.finished.connect(self.presentation_monitor.poll)

        self.restore_preferences()
        self.check_import()
        self._refresh_home_minimum_height()

    def restore_preferences(self):
        """将已保存的分隔符和播放偏好恢复到主页控件。"""
        settings = self.ctx.app_settings
        self.project.mark = str(settings.get('mark')).strip() or '●'
        self.currentSwitch.setChecked(bool(settings.get('countdown_enabled')))
        self.currentSpinBox.setValue(int(settings.get('countdown_seconds')))
        self.playCardWidget_3.refresh_countdown()
        self._sync_playback_enabled = bool(settings.get('scroll_enabled'))

        # 初值设置完成后再接信号，避免把恢复动作误当成用户修改。
        self.currentSwitch.checkedChanged.connect(self._on_countdown_preferences_changed)
        self.currentSpinBox.valueChanged.connect(self._on_countdown_preferences_changed)
        self.playCardWidget_3.sync_switch.checkedChanged.connect(self.set_sync_playback_enabled)
        self._refresh_sync_switch()

    def persist_preferences(self, *_):
        """即时保存不影响音频生成的主页播放偏好。"""
        values = {
            'mark': self.project.mark,
            'countdown_enabled': self.currentSwitch.isChecked(),
            'countdown_seconds': self.currentSpinBox.value(),
            'scroll_enabled': self.sync_playback_enabled(),
        }
        if any(self.ctx.app_settings.get(key) != value for key, value in values.items()):
            self.ctx.app_settings.update(values)
            self.ctx.config.save_later()

    def _on_countdown_preferences_changed(self, *_):
        if self.playback_preferences_locked():
            with QSignalBlocker(self.currentSwitch), QSignalBlocker(self.currentSpinBox):
                self.currentSwitch.setChecked(bool(self.ctx.app_settings.get('countdown_enabled')))
                self.currentSpinBox.setValue(int(self.ctx.app_settings.get('countdown_seconds')))
            self.playCardWidget_3.refresh_countdown()
            return
        self.playCardWidget_3.refresh_countdown()
        self.persist_preferences()
        self.countdown_preferences_changed.emit(self.currentSwitch.isChecked(), self.currentSpinBox.value())

    def set_countdown_preferences(self, enabled, seconds):
        if self.playback_preferences_locked():
            return
        with QSignalBlocker(self.currentSwitch), QSignalBlocker(self.currentSpinBox):
            self.currentSwitch.setChecked(bool(enabled))
            self.currentSpinBox.setValue(int(seconds))
        self._on_countdown_preferences_changed()

    def sync_playback_enabled(self):
        return self._sync_playback_enabled

    def set_sync_playback_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._sync_playback_enabled:
            self._refresh_sync_switch()
            return
        if not self.can_sync_playback() or self.playback_preferences_locked():
            self._refresh_sync_switch()
            return
        self._sync_playback_enabled = enabled
        self._refresh_sync_switch()
        self.persist_preferences()
        self.refresh_slideshow_watch()
        self.sync_playback_changed.emit(enabled)

    def _refresh_sync_switch(self):
        control = self.playCardWidget_3.sync_switch
        with QSignalBlocker(control):
            control.setChecked(self._sync_playback_enabled)
        locked = self.playback_preferences_locked()
        set_enabled(control, self.can_sync_playback() and not locked)
        mode = self.ctx.app_settings.get('page_turn_mode')
        hint = {
            'foreground': '朗读时同步翻页。请先开始放映，并保持放映窗口在前台。',
            'window': '朗读时同步翻页。请先开始放映，并在设置中选择放映窗口。',
        }.get(mode, '朗读时同步控制 PPT 放映。放映联动可在设置中调整。')
        reason = ('请先停止播放，再修改此设置。' if locked else
                  self.sync_playback_unavailable_reason() if not control.isEnabled() else '')
        self.playCardWidget_3.set_sync_hint(hint + ('\n' + reason if reason else ''))

    def playback_preferences_locked(self):
        return (self._playing_sequence or self.playback_show.preparing
                or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

    def check_import(self):
        """根据讲稿导入状态更新播放控件和状态徽标。"""
        self._refresh_playing_script()
        if self.project.notes:
            self.dismiss_resume_notice()
        action = '更新配音' if self.project.audio_outdated else '生成配音'
        self.generate_audio_link.setText(action)
        self.generate_audio_link.setVisible(
            bool(self.project.notes) and not self.project.audio_loaded and not self.is_busy())
        self.generate_audio_link.setEnabled(bool(self.project.notes) and not self.is_busy())
        if self.project.audio_loaded:
            self.statusLabel.setText('已导入')
            self.IconInfoBadge.setLevel(InfoLevel.SUCCESS)
            self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
            self.IconInfoBadge.setToolTip('')
            self.statusLabel.setToolTip('')
            self.regenerate_action.setEnabled(True)
        else:
            pending = '配音待更新' if self.project.audio_outdated else '尚未生成配音'
            self.statusLabel.setText(pending if self.project.notes else '未导入')
            self.IconInfoBadge.setLevel(InfoLevel.INFOAMTION)
            self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
            self.regenerate_action.setEnabled(False)
            self.playButton.setEnabled(False)
            hint = f'点击“{action}”后可播放和导出音频。' if self.project.notes else ''
            self.IconInfoBadge.setToolTip(hint)
            self.statusLabel.setToolTip(hint)
        self.refresh_presentation_state()

    def remember_session(self):
        """保存最近一次可恢复的记录与段落，不记录播放中状态。"""
        if not self.project.audio_loaded or self.is_busy() or not self.project.session_record_path:
            return
        state = {'record_path': str(self.project.session_record_path.resolve()),
                 'index': min(max(self.project.current_index, 0), max(0, len(self.project.notes_list) - 1))}
        if state != self.ctx.app_settings.get('last_session'):
            self.ctx.app_settings.set('last_session', state)
            self.ctx.config.save_later()

    def prompt_resume_session(self):
        if not self.ctx.app_settings.get('resume_last_session') or self.project.notes or self.is_busy():
            return
        if self._resume_bar is not None:
            return
        window = self.window()
        if self._resume_waiting_for_dialog or getattr(window, '_waiting_to_close', False):
            return
        state = dict(self.ctx.app_settings.get('last_session') or {})
        if not state:
            return
        dialog = active_modal_dialog(window)
        if dialog is not None:
            self._resume_waiting_for_dialog = True
            dialog.finished.connect(self._resume_after_dialog, Qt.ConnectionType.SingleShotConnection)
            dialog.destroyed.connect(self._resume_after_dialog, Qt.ConnectionType.SingleShotConnection)
            return
        try:
            target = Path(state['record_path'])
            record = json.loads(target.read_text(encoding='utf-8'))
            items = record['items']
            if not items:
                return
            index = min(max(int(state.get('index', 0)), 0), len(items) - 1)
            name = record.get('source_name') or target.stem
            page = int(items[index]['page'])
        except (OSError, ValueError, TypeError, KeyError):
            return
        bar = InfoBar(icons.SystemIcon.HISTORY, '继续上次讲稿', f'{name} · 上次停在第 {page} 页',
                           orient=Qt.Horizontal, isClosable=True,
                           position=InfoBarPosition.TOP, duration=-1,
                           parent=getattr(window, 'stackedWidget', window))
        button = PushButton('恢复讲稿', bar)
        button.clicked.connect(lambda: self._resume_session(target, index))
        bar.addWidget(button)
        def closed():
            if self._resume_bar is bar:
                self._resume_bar = None
        bar.closedSignal.connect(closed)
        self._resume_bar = bar
        bar.show()

    def dismiss_resume_notice(self):
        if self._resume_bar is not None:
            self._resume_bar.close()
            self._resume_bar = None

    def on_resume_preference_changed(self, enabled):
        if not enabled:
            self.dismiss_resume_notice()

    def _resume_session(self, target, index):
        window = self.window()
        if self.is_busy() or active_modal_dialog(window) is not None:
            return
        self.dismiss_resume_notice()
        if (self.project.notes or not self.ctx.app_settings.get('resume_last_session')
                or getattr(window, '_waiting_to_close', False)):
            return
        try:
            self.load_session_record(target)
            self.project.current_index = min(index, len(self.project.notes_list) - 1)
            self.set_current_label_text()
            if hasattr(window, 'switchTo'):
                window.switchTo(self)
        except Exception as exc:
            self.create_error_info_bar('讲稿恢复失败', str(exc))

    def _resume_after_dialog(self, *_):
        if not self._resume_waiting_for_dialog:
            return
        self._resume_waiting_for_dialog = False
        if self.window().isVisible():
            QTimer.singleShot(0, self, self.prompt_resume_session)

    def _bind_presentation_monitor(self, baseline=None, expected_sha=None):
        self._dismiss_ppt_update()
        self.presentation_monitor.comments_enabled = self.project.script_comments_enabled
        self.presentation_monitor.bind(
            self.project.presentation_path or self.project.presentation_link.get('path'), baseline=baseline,
            expected_sha=(expected_sha if expected_sha is not None
                          else self.project.presentation_link.get('sha256', '')),
            fallback=self.project.notes)

    def _dismiss_ppt_update(self):
        if self._ppt_update_bar is not None:
            self._ppt_update_bar.close()
            self._ppt_update_bar = None

    def _notify_ppt_update(self, result):
        if self.is_busy():
            return
        self._dismiss_ppt_update()
        bar = InfoBar(icons.SystemIcon.UPDATE, 'PPT 讲稿有更新', '查看变化后可更新当前讲稿。',
                           orient=Qt.Horizontal, isClosable=True,
                           position=InfoBarPosition.TOP, duration=-1, parent=self)
        button = PushButton('查看更新', bar)
        button.clicked.connect(self.update_ppt_notes)
        bar.addWidget(button)
        def closed():
            if self._ppt_update_bar is bar:
                self._ppt_update_bar = None
        bar.closedSignal.connect(closed)
        self._ppt_update_bar = bar
        bar.show()

    def _sync_ppt_comments(self, result):
        data, link = result
        updated = dict(self.project.notes)
        for page in updated.keys() & data.notes.keys():
            updated[page] = replace_spoken_text(data.notes[page],
                                               spoken_text(updated[page], True))
        try:
            if self.project.session_record_path and self.project.audio_loaded:
                presentation_link.update_record(self.project.session_record_path, link,
                                                notes_baseline=data.notes, notes=updated)
        except Exception:
            get_logger().warning('无法保存更新后的备注注释', exc_info=True)
        self.project.notes = updated
        self.project.presentation_link = dict(link)
        self.presentation_monitor.baseline = dict(data.notes)

    def update_ppt_notes(self):
        if self.is_busy() or not self.has_presentation():
            return
        source = self.project.presentation_path
        if source.suffix.lower() != '.pptx':
            return
        try:
            stamp = file_stamp(source)
            data = script_io.load_script(source)
            link = presentation_link.describe(source)
            if stamp != file_stamp(source):
                raise RuntimeError('PPT 正在保存，请稍后再试。')
        except Exception as exc:
            self.create_error_info_bar('PPT 读取失败', str(exc))
            return
        pages = changed_pages(self.project.notes, data.notes, comments_enabled=self.project.script_comments_enabled)
        if not pages:
            try:
                if self.project.script_comments_enabled:
                    self._sync_ppt_comments((data, link))
                self._accept_ppt_baseline(link, data.notes)
            except Exception as exc:
                self.create_error_info_bar('讲稿更新失败', str(exc))
                return
            self.create_success_info_bar('讲稿已是最新', 'PPT 中的讲稿与当前内容一致。')
            return
        baseline = self.presentation_monitor.baseline
        edited = baseline is not None and bool(changed_pages(
            baseline, self.project.notes, comments_enabled=self.project.script_comments_enabled))
        automatic = bool(self.ctx.app_settings.get('auto_generate_audio'))
        voice = self.ctx.tts_engine.get_selected_voice_name() if automatic else ''
        dialog = PptUpdateDialog(source.name, self.reading_notes(),
                                 spoken_notes(data.notes, self.project.script_comments_enabled), edited,
                                 voice, self, **({} if automatic else {'generate_audio': False}))
        if not dialog.exec():
            return
        snapshot = self._project_snapshot()
        try:
            if stamp != file_stamp(source):
                raise RuntimeError('PPT 又有修改，请重新查看更新。')
            # 成功生成后再更新当前记录，失败或取消时保留上一版记录。
            self._accept_ppt_baseline(link, data.notes, persist=False)
        except Exception as exc:
            self.create_error_info_bar('讲稿更新失败', str(exc))
            return
        self.project.note_file_path = source
        self.project.note_file_name = data.source_name or source.stem
        self.project.notes = dict(data.notes)
        self.notes_path_text.set_text(str(source))
        if not self.refresh_script():
            self._restore_project(snapshot)

    def _accept_ppt_baseline(self, link, notes, *, persist=True):
        if persist and self.project.session_record_path and self.project.audio_loaded:
            presentation_link.update_record(self.project.session_record_path, link, notes_baseline=notes)
        self.project.presentation_link = dict(link)
        self._bind_presentation_monitor(baseline=notes)

    def has_presentation(self):
        return self.project.has_presentation()

    def is_restricted(self):
        return bool(self.project.notes) and not self.has_presentation()

    def sync_playback_unavailable_reason(self):
        reason = self.project_availability(verify_audio=False).sync_unavailable_reason()
        return {
            UnavailableReason.NOTES: '请先导入讲稿',
            UnavailableReason.PREPARING: '正在准备 PPT 放映',
            UnavailableReason.BUSY: '正在生成音频，请等待完成',
            UnavailableReason.AUDIO: '音频尚未就绪，请先完成语音生成',
            UnavailableReason.PRESENTATION: '请在“历史记录列表”中关联 PPT',
        }.get(reason, '')

    def project_availability(self, *, verify_audio=True):
        return self.project.availability(busy=self.is_busy(), preparing=self.playback_show.preparing,
                                         verify_audio=verify_audio)

    def can_sync_playback(self):
        return not self.sync_playback_unavailable_reason()

    def refresh_presentation_state(self):
        self._refresh_history_source_path()
        self.regenerate_action.setEnabled(self.project.audio_loaded and bool(self.project.notes) and not self.is_busy())
        set_enabled(self.editMarkPushButton, not self.is_busy())
        available = self.has_presentation()
        restricted = self.is_restricted()
        sync_available = self.can_sync_playback()
        self._refresh_sync_switch()
        self._refresh_play_button()
        self.update_ppt_action.setEnabled(
            bool(self.project.notes) and available and not self.is_busy()
            and self.project.presentation_path.suffix.lower() == '.pptx')
        self.import_notes_action.setEnabled(bool(self.project.notes) and not self.is_busy())
        hint = '在“历史记录列表”中关联 PPT 后，可使用同步翻页、视频导出和写回 PPT 备注。'
        if self.project.audio_loaded:
            self.statusLabel.setText('已导入 · 受限模式' if restricted else '已导入')
            self.IconInfoBadge.setLevel(InfoLevel.WARNING if restricted else InfoLevel.SUCCESS)
            self.IconInfoBadge.setIcon(icons.SystemIcon.INFO if restricted else FluentIcon.ACCEPT_MEDIUM)
            self.IconInfoBadge.setToolTip(hint if restricted else '')
            self.statusLabel.setToolTip(hint if restricted else '')
        if not available:
            self.ctx.slideshow_watcher.stop()
        state = (bool(self.project.notes), available, str(self.project.presentation_path or ''),
                 sync_available, self.is_busy(), self.project.audio_loaded)
        if state != self._presentation_state:
            self._presentation_state = state
            self.presentation_state_changed.emit()

    def _refresh_history_source_path(self):
        if not self.project.session_record_path:
            return
        source = self.project.presentation_path
        if source is None and self.project.presentation_link.get('path'):
            source = Path(self.project.presentation_link['path'])
        if source is None:
            source = self.project.note_file_path
        if source is not None:
            text = str(source)
            if not source.is_file():
                text += '（文件不可用）'
        else:
            text = f'{self.project.note_file_name}（未关联原文件）' if self.project.note_file_name else '未关联原文件'
        if text != self.notes_path_text.text:
            self.notes_path_text.set_text(text)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_home_minimum_height()
        self.refresh_presentation_state()

    def eventFilter(self, obj, event):
        if (obj is self.bgScrollArea.viewport()
                and event.type() == QEvent.Type.Resize
                or obj is self.scrollAreaWidgetContents_3
                and event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange)):
            self._refresh_home_minimum_height()
        return super().eventFilter(obj, event)

    def _refresh_home_minimum_height(self):
        content_height = self.verticalLayout_11.minimumSize().height()
        if self.IndeterminateProgressBar.isHidden():
            content_height += self.IndeterminateProgressBar.minimumHeight() + self.verticalLayout_2.spacing()
        margins = self.layout().contentsMargins()
        minimum = (content_height + margins.top() + margins.bottom()
                   + 2 * self.bgScrollArea.frameWidth())
        if self.minimumHeight() != minimum:
            self.setMinimumHeight(minimum)
            self.minimum_home_height_changed.emit(minimum)

    def _poll_presentation(self):
        if self.project.presentation_path is None and self.project.presentation_link.get('path'):
            candidate = Path(self.project.presentation_link['path'])
            if candidate.is_file():
                self.project.presentation_path = candidate
        if self.project.notes and self.has_presentation() != self._presentation_state[1]:
            if not self.has_presentation():
                self._dismiss_ppt_update()
            self.refresh_presentation_state()
            self.refresh_slideshow_watch()

    def set_presentation_link(self, link, *, persist=True, notes_baseline=None):
        # 先保存关联，失败时继续使用原文稿；不触碰讲稿、音频或生成配置。
        if (persist and self.project.audio_loaded and self.project.session_record_path
                and self.project.session_record_path.is_file()):
            presentation_link.update_record(self.project.session_record_path, link, notes_baseline=notes_baseline)
        self.stop_audio()
        self.release_playback_show()
        self.project.presentation_link = dict(link)
        self.project.presentation_path = Path(link['path'])
        self.notes_path_text.set_text(str(self.project.presentation_path))
        self._bind_presentation_monitor(baseline=notes_baseline)
        self.refresh_presentation_state()
        self.refresh_slideshow_watch()

    def _history_presentation_changed(self, record_path, link):
        if self.project.session_record_path and Path(record_path).resolve() == self.project.session_record_path.resolve():
            self.set_presentation_link(link, persist=False)

    def _restore_presentation_link(self, record):
        link = presentation_link.from_record(record)
        candidates = []
        if link.get('sha256') and not (link.get('path') and Path(link['path']).is_file()):
            candidates = [item['path'] for item in slideshow.list_open_presentations()]
            if link.get('path'):
                candidates.extend(presentation_link.nearby_files(Path(link['path']).parent))
        resolved = presentation_link.resolve(link, candidates)
        self.project.presentation_link = presentation_link.refresh_identity(link, resolved) if resolved else link
        self.project.presentation_path = resolved
        if resolved and self.project.session_record_path:
            try:
                presentation_link.update_record(self.project.session_record_path, self.project.presentation_link)
            except OSError:
                pass  # 关联落盘失败不影响已有音频的恢复。
        self.refresh_presentation_state()

    def on_stop_requested(self, requester):
        """响应其它页面的播放请求，停止当前正文或倒计时。"""
        if requester is self:
            return
        self.stop_audio()

    def play_audio(self):
        """播放当前正文音频。"""
        if not self.is_play_notes:
            return
        if self.project.current_index < len(self.project.media_list):
            self._playing_sequence = True
            if self.playback_show.session and not self._audio_only_playback:
                note = self.project.notes_list[self.project.current_index]
                self.playback_show.set_segment(note['page'], self._segment_offset())
            self.player.setSource(QUrl.fromLocalFile(str(self.project.media_list[self.project.current_index])))
            self.player.play()
            self.set_current_label_text()
        else:
            self.reset_audio(align_show=False, remember_position=False)
        self._refresh_playing_script()

    def media_status_changed(self, status):
        """在媒体结束后推进正文播放状态机。"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing_sequence and self.is_play_notes:
            self.next_timer.start(100)

    def on_playback_error(self, error, message):
        if (error == QMediaPlayer.Error.NoError or not self._playing_sequence
                or self.player.error() == QMediaPlayer.Error.NoError):
            return
        print(f'[播放] 配音无法播放：{message}')
        self.stop_audio()
        self.create_error_info_bar('配音无法播放', '请重新生成讲稿配音后重试')

    def stop_audio(self):
        """停止全部播放器并保留当前正文索引。"""
        self._playing_sequence = False
        self.countdown.stop()
        self.next_timer.stop()
        if self.playback_show.preparing:
            self.playback_show.cancel_preparation()
            self.getFileButton.setEnabled(True)
            self.refresh_presentation_state()
        self.player.stop()
        self._refresh_playing_script()
        self.ctx.playback_bus.request_stop(self)

    def reset_audio(self, *, align_show=True, remember_position=True):
        """停止播放并将正文索引重置到首段。"""
        if remember_position:
            self._remember_navigation(0)
        self.stop_audio()
        self.project.current_index = 0
        if (align_show and self.playback_show.session and self.sync_playback_enabled()
                and self.ctx.app_settings.get('slideshow_sync_navigation')
                and not self._audio_only_playback
                and self.ctx.app_settings.get('page_turn_mode') == 'com' and self.project.notes_list):
            try:
                self.playback_show.goto_page(self.project.notes_list[0]['page'])
            except Exception:
                self.release_playback_show()
        if self.project.notes_list:
            self.set_current_label_text()

    def _refresh_playing_script(self, *_):
        self._refresh_play_button()
        self._refresh_overview()
        if self._preview_notes is not self.project.notes_list:
            self.playing_script_preview.cancel_navigation()
            self._preview_notes = self.project.notes_list
        ready = self.project.audio_loaded and bool(self.project.notes_list)
        if not ready:
            self.playing_script_preview.set_position()
            self.playing_script_preview.set_content('', empty_text='播放时显示当前讲稿')
            return
        index = min(max(self.project.current_index, 0), len(self.project.notes_list) - 1)
        page = self.project.notes_list[index]['page']
        self.playing_script_preview.set_position(
            page=page, page_count=max(self.project.notes, default=1),
            segment=index + 1, segment_count=len(self.project.notes_list),
        )
        start, end = index, index + 1
        while start > 0 and self.project.notes_list[start - 1]['page'] == page:
            start -= 1
        while end < len(self.project.notes_list) and self.project.notes_list[end]['page'] == page:
            end += 1
        texts = tuple(self.project.audio_edits.get(i, {}).get('spoken_text', self.project.notes_list[i]['text'])
                      for i in range(start, end))
        offset = index - start
        self.playing_script_preview.set_content(
            texts[offset], following=texts[offset + 1:], page_text='\n\n'.join(texts),
            playing=self._playing_sequence and self.is_play_notes,
            countdown=self._playing_sequence and not self.is_play_notes,
        )

    def _refresh_play_button(self):
        active = self._playing_sequence or self.playback_show.preparing
        action = '停止' if active else '播放'
        self.playButton.setAccessibleName(action)
        self.playButton.setToolTip(action)
        self.playButton.setIcon((icons.SystemIcon.STOP if active else icons.SystemIcon.PLAY).icon(Theme.DARK))
        self.playButton.setEnabled(active or (self.project.audio_loaded and not self.is_busy()))
        navigation_enabled = self.project.audio_loaded and not self.is_busy()
        locked = self.playback_preferences_locked()
        self.playCardWidget_3.set_countdown_available(navigation_enabled, locked=locked)
        self._refresh_sync_switch()
        remaining = self.countdown.remaining if self._playing_sequence and not self.is_play_notes else None
        self.playCardWidget_3.set_countdown_progress(playing=self._playing_sequence, remaining=remaining)
        self.resetButton.setEnabled(navigation_enabled)
        self.playing_script_preview.set_navigation_enabled(navigation_enabled)
        previous, following = self._neighbor_pages()
        self.previousPageButton.setEnabled(navigation_enabled and previous is not None)
        self.nextPageButton.setEnabled(navigation_enabled and following is not None)
        self._refresh_return_position_button()
        self.playbackOverview.set_available(bool(self.project.notes) and not self.is_busy())
        self.playbackOverview.set_navigation_enabled(
            navigation_enabled and bool(self.project.notes_list)
            and len(self.project.media_list) == len(self.project.notes_list))
        self.playing_script_preview.set_review_available(
            navigation_enabled and bool(self.project.notes_list)
            and len(self.project.media_list) == len(self.project.notes_list))
        if locked != self._playback_preferences_locked:
            self._playback_preferences_locked = locked
            self.playback_preferences_lock_changed.emit(locked)

    def _refresh_overview(self):
        notes = self.reading_notes()
        durations = tuple(self.project.notes_duration_list) if self.project.audio_loaded else ()
        key = (tuple(notes.items()), tuple(item['page'] for item in self.project.notes_list),
               durations, self.project.audio_loaded, self.project.mark)
        if key != self._overview_key:
            self._overview_key = key
            statistics = build_statistics(notes, self.project.notes_list,
                                          durations if self.project.audio_loaded else None, self.project.mark)
            self.playbackOverview.set_statistics(statistics, self.project.notes_list, durations)
        self._refresh_overview_position()

    def _refresh_overview_position(self, *_):
        position = self.player.position() if self.is_play_notes else 0
        self.playbackOverview.set_position(self.project.current_index, position)

    def _sync_navigation_history(self):
        if self._navigation_notes is not self.project.notes_list or not self.project.audio_loaded:
            self._navigation_notes = self.project.notes_list
            self._navigation_history.clear()

    def _remember_navigation(self, target):
        self._sync_navigation_history()
        current = self.project.current_index
        limit = min(len(self.project.notes_list), len(self.project.media_list))
        if (not 0 <= current < limit or current == target
                or (self._navigation_history and self._navigation_history[-1] == current)):
            return
        self._navigation_history.append(current)

    def _return_position_target(self):
        self._sync_navigation_history()
        limit = min(len(self.project.notes_list), len(self.project.media_list))
        return next((index for index in reversed(self._navigation_history)
                     if 0 <= index < limit and index != self.project.current_index), None)

    def _refresh_return_position_button(self):
        target = self._return_position_target()
        self.returnPositionButton.setEnabled(
            target is not None and self.project.audio_loaded and not self.is_busy())
        hint = '手动跳转后可返回刚才的位置'
        if target is not None:
            page = self.project.notes_list[target]['page']
            segment = target - self.get_index_from_page(page) + 1
            hint = f'返回第 {page} 页 · 第 {segment} 段'
        self.returnPositionButton.setToolTip(hint)

    def return_to_previous_position(self):
        """取回上一处手动定位，沿用当前播放状态，不写入新的返回记录。"""
        if self.is_busy() or not self.project.audio_loaded:
            return
        target = self._return_position_target()
        if target is None:
            self._refresh_return_position_button()
            return
        while self._navigation_history.pop() != target:
            pass
        self._jump_to_position('segment', target + 1, remember_position=False)

    def _neighbor_pages(self):
        if not self.project.notes_list:
            return None, None
        index = min(max(self.project.current_index, 0), len(self.project.notes_list) - 1)
        current_page = self.project.notes_list[index]['page']
        previous = following = None
        for note in self.project.notes_list:
            page = note['page']
            if page < current_page and (previous is None or page > previous):
                previous = page
            elif page > current_page and (following is None or page < following):
                following = page
        return previous, following

    def _change_page(self, direction):
        previous, following = self._neighbor_pages()
        target = previous if direction < 0 else following
        if target is not None:
            self._jump_to_position('page', target)

    def _jump_to_position(self, kind, value, *, remember_position=True, restart_current=False):
        """定位到目标讲稿，保留当前播放状态；播放中跳转不重放倒计时。"""
        if self.is_busy() or not self.project.audio_loaded or not self.project.notes_list:
            return
        index = self.get_index_from_page(value) if kind == 'page' else value - 1
        if index < 0 and kind == 'page':
            self.create_warning_info_bar('此页没有讲稿', f'当前讲稿不包含第 {value} 页')
            return
        if not 0 <= index < min(len(self.project.notes_list), len(self.project.media_list)):
            return
        if index == self.project.current_index:
            if restart_current and self._playing_sequence:
                self._seek_segment(0, remember_position=False)
            return
        if self._playing_sequence:
            self._seek_segment(index - self.project.current_index, remember_position=remember_position)
        else:
            if remember_position:
                self._remember_navigation(index)
            self.stop_audio()
            self.project.current_index = index
            self.set_current_label_text()

    def set_current_label_text(self):
        """刷新当前段数和页码标签。"""
        self._refresh_playing_script()
        if not self.project.notes_list:
            return
        self.remember_session()

    def get_index_from_page(self, page):
        """返回指定页第一条讲稿的索引，找不到时返回 -1。"""
        for i, item in enumerate(self.project.notes_list):
            if item['page'] == page:
                return i
        return -1

    def timeout_play_next_audio(self):
        """推进正文片段，并按设置发送翻页指令。"""
        self.next_timer.stop()
        if not self._playing_sequence or not self.is_play_notes:
            return
        next_index = self.project.current_index + 1
        if next_index >= len(self.project.notes_list):
            self.reset_audio(align_show=False, remember_position=False)
            return
        if self.sync_playback_enabled() and not self._audio_only_playback:
            if self.send_page_down(next_index) is False:
                return
        self.project.current_index = next_index
        self.play_audio()

    def send_page_down(self, next_index=None):
        """发送翻页键。

        非前台方式在目标不可用时回退到前台按键，避免翻页整体失效。
        """
        if not self.can_sync_playback():
            self.refresh_presentation_state()
            return
        if self._audio_only_playback:
            return True
        mode = str(self.ctx.app_settings.get('page_turn_mode') or 'com')
        if mode == 'com':
            if self.playback_show.session:
                try:
                    self._advancing_show = True
                    index = self.project.current_index if next_index is None else next_index
                    self.playback_show.advance_to(self.project.notes_list[index]['page'])
                    return True
                except Exception as exc:
                    if (not self.ctx.app_settings.get('slideshow_stop_on_exit')
                            and not self.playback_show.session.presentation.is_active()):
                        self.on_slideshow_ended()
                        return True
                    self.stop_audio()
                    self.create_error_info_bar('同步播放已停止', str(exc))
                    return False
                finally:
                    self._advancing_show = False
            if slideshow.advance(self.project.presentation_path):
                return
            self._notify_page_turn_fallback('未检测到正在进行的放映')
        elif mode == 'window':
            hwnd = window_target.resolve(self.ctx.app_settings.get('page_turn_window'))
            if window_target.send_scroll(hwnd):
                return
            self._notify_page_turn_fallback('指定的窗口已失效')
        _press_page_down()

    def _notify_page_turn_fallback(self, reason: str):
        """回退到前台按键时给出可见提示，每次播放只提示一次。"""
        print(f'[翻页] {reason}，本次改用前台按键')
        if getattr(self, '_page_turn_notified', False):
            return
        self._page_turn_notified = True
        self.create_warning_info_bar('翻页已改用前台按键', reason)

    # 全局热键

    def hotkey_ready(self) -> bool:
        """讲稿与音频就绪且不在生成中时才响应全局热键。"""
        return bool(self.project.notes_list) and bool(self.project.media_list) and not self.is_busy()

    def handle_hotkey(self, action: str):
        """执行全局热键对应的播放动作。"""
        if self.playback_show.preparing:
            if action in ('stop', 'play_pause'):
                self.stop_audio()
            return
        if not self.hotkey_ready():
            return
        handler = {
            'play_pause': self.toggle_play,
            'stop': self.stop_audio,
            'prev_segment': self.prev_segment,
            'next_segment': self.next_segment,
            'restart_segment': self.restart_segment,
        }.get(action)
        if handler is not None:
            handler()

    def toggle_play(self):
        """在播放与停止之间切换。"""
        if (self._playing_sequence or self.playback_show.preparing
                or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self.stop_audio()
        else:
            self.init_play()

    def _seek_segment(self, offset: int, *, remember_position=True):
        """按偏移移动正文索引，并从该段开始播放。"""
        if self.is_busy() or not self.project.audio_loaded or not self.project.media_list:
            return
        index = min(max(self.project.current_index + offset, 0), len(self.project.media_list) - 1)
        if remember_position:
            self._remember_navigation(index)
        self.stop_audio()
        self.is_play_notes = True
        self.project.current_index = index
        self.set_current_label_text()
        if (self.sync_playback_enabled() and self.can_sync_playback()
                and not self._audio_only_playback
                and self.ctx.app_settings.get('slideshow_sync_navigation')
                and self.ctx.app_settings.get('page_turn_mode') == 'com'):
            self._prepare_playback_show(countdown=False)
        else:
            self.play_audio()

    def prev_segment(self):
        """回到上一段并播放。"""
        self._seek_segment(-1)

    def next_segment(self):
        """前进到下一段并播放。"""
        self._seek_segment(1)

    def restart_segment(self):
        """重播当前段。"""
        self._seek_segment(0)

    def refresh_hotkeys(self):
        """按当前配置与就绪状态重新注册全局热键。"""
        manager = self.ctx.hotkeys
        settings = self.ctx.app_settings
        if not (settings.get('hotkeys_enabled') and self.hotkey_ready()):
            manager.clear()
            return {}
        return manager.apply(hotkeys.normalize_bindings(settings.get('hotkeys')))

    def is_busy(self) -> bool:
        """返回音频生成任务是否仍在运行。"""
        return self._confirming_generation or self.save_thread.isRunning() or self.playback_show.preparing

    def force_regenerate(self):
        """确认后绕过缓存，重新合成当前讲稿的全部音频。"""
        if not self.project.notes:
            self.create_warning_info_bar('尚未导入讲稿', '请先导入 PowerPoint 或 Word 文件')
            return

        if self.is_busy():
            self.create_warning_info_bar('正在生成音频', '等待当前转换完成后再试')
            return

        if not self.project.audio_loaded:
            return

        detail = '将跳过缓存，按当前引擎设置重新合成全部语句；在线引擎会产生调用开销'
        if self.project.audio_edits:
            detail += '。已采用的片段调整也会被替换。'
        box = MessageBox(
            '重新生成音频',
            detail,
            self,
        )
        box.yesButton.setText('重新生成')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        self.regenerate(force=True)

    def regenerate(self, force: bool = False) -> bool:
        """按当前分隔符重新切分讲稿并启动音频生成。

        正在生成或尚未导入时返回 ``False``，由跨页调用方在当前页面展示提示。
        """
        if self.is_busy() or not self.project.notes:
            return False

        snapshot = self._project_snapshot()
        set_enabled(self.getFileButton, False)
        if self.init_general_play(force_regenerate=force) is False:
            self._restore_project(snapshot)
            return False
        return True

    def refresh_script(self):
        """讲稿变化后按偏好生成配音，或保留仅处理讲稿的状态。"""
        if self.ctx.app_settings.get('auto_generate_audio'):
            return self.regenerate()
        return self.prepare_script_only()

    def prepare_import(self, *, comments_enabled=None):
        if self.ctx.app_settings.get('auto_generate_audio'):
            if comments_enabled is None:
                return self.init_general_play()
            return self.init_general_play(comments_enabled=comments_enabled)
        return self.prepare_script_only(comments_enabled=comments_enabled)

    def prepare_script_only(self, *, comments_enabled=None):
        """读取和分段独立于语音引擎，不写入配音历史。"""
        if self.is_busy() or not self.project.notes:
            return False
        enabled = (bool(self.ctx.app_settings.get('script_comments_enabled'))
                   if comments_enabled is None else bool(comments_enabled))
        try:
            self.generation.prepare_notes(comments_enabled=enabled)
        except Exception as exc:
            self.getFileButton.setEnabled(True)
            self.create_error_info_bar('讲稿解析错误', str(exc))
            return False
        self.presentation_monitor.comments_enabled = self.project.script_comments_enabled
        self.stop_audio()
        self.release_playback_show()
        self.player.setSource(QUrl())
        self.generation.apply_script_only()
        self.getFileButton.setEnabled(True)
        self.check_import()
        self.set_current_label_text()
        self.refresh_hotkeys()
        self.refresh_slideshow_watch()
        self.audio_changed.emit()
        return True

    def _project_snapshot(self):
        return (copy.deepcopy(self.project), self.generation.resume_page,
                copy.deepcopy(self.presentation_monitor.baseline), self.notes_path_text.text)

    def _restore_project(self, snapshot):
        project, resume_page, baseline, path_text = snapshot
        self.project.replace_from(project)
        self.generation.resume_page = resume_page
        self.notes_path_text.set_text(path_text)
        self._bind_presentation_monitor(baseline=baseline)
        self.persist_preferences()
        self.check_import()
        self.set_current_label_text()
        self.refresh_hotkeys()
        self.refresh_slideshow_watch()
        self.audio_changed.emit()
        self.getFileButton.setEnabled(True)

    def apply_rewritten_notes(self, mapping: dict) -> int:
        """写回确认采用的逐页改写结果，并按偏好准备配音。

        未改动页面会自然命中原缓存。返回实际写回页数；任务忙时返回 -1。
        """
        if self.is_busy():
            return -1

        snapshot = self._project_snapshot()
        applied = 0
        for page, text in dict(mapping).items():
            if page in self.project.notes and str(text).strip():
                self.project.notes[page] = (replace_spoken_text(self.project.notes[page], text)
                                    if self.project.script_comments_enabled else text)
                applied += 1

        if not applied:
            return 0

        if not self.refresh_script():
            self._restore_project(snapshot)
            return -1
        return applied

    def import_pptx(self):
        """打开文件选择器并从 PowerPoint 备注导入讲稿。"""
        return self.import_script('选择 PowerPoint 文件', 'PowerPoint 演示文稿 (*.pptx)')

    def import_word(self):
        """打开文件选择器并导入可往返编辑的 Word 讲稿。"""
        return self.import_script('选择 Word 讲稿', 'Word 文档 (*.docx)')

    def import_data_script(self):
        """打开文件选择器并导入结构化讲稿 JSON。"""
        return self.import_script('选择 JSON 讲稿', 'JSON 讲稿 (*.json)')

    def import_notes_from_file(self):
        if self.is_busy() or not self.project.notes:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self, '选择讲稿来源文件', '', script_io.SCRIPT_FILTER)
        if selected:
            self.update_notes_from_file(Path(selected))

    def update_notes_from_file(self, path):
        """只读取来源讲稿，保留当前文稿关联、文件名称和历史身份。"""
        if self.is_busy() or not self.project.notes:
            return False
        snapshot = self._project_snapshot()
        set_enabled(self.getFileButton, False)
        try:
            path = Path(path)
            stamp = file_stamp(path)
            data = script_io.load_script(path)
            if not data.notes or any(page < 1 for page in data.notes):
                raise ValueError('来源文件中没有有效的分页讲稿。')
            if stamp != file_stamp(path):
                raise RuntimeError('来源文件正在保存，请稍后重试。')
            if self.has_presentation():
                presentation_link.validate(self.project.presentation_path, data.notes)
            missing = set(self.project.notes) - set(data.notes)
            if path.suffix.lower() in presentation_link.PPT_SUFFIXES and missing:
                box = MessageBox(
                    '按页码更新讲稿？',
                    f'来源 PPT 包含 {len(data.notes)} 页讲稿，当前有 {len(missing)} 页未包含在来源中。'
                    '这些页面将保留原讲稿。请确认两份 PPT 的页面顺序一致。', self)
                box.yesButton.setText('更新讲稿')
                box.cancelButton.setText('取消')
                if not box.exec():
                    return False
                if stamp != file_stamp(path):
                    raise RuntimeError('来源文件又有修改，请重新选择。')
            if not self._update_imported_script(data):
                self._restore_project(snapshot)
                return False
            return True
        except Exception as exc:
            self._restore_project(snapshot)
            self.create_error_info_bar('讲稿更新失败', str(exc))
            return False
        finally:
            if not self.is_busy():
                self.getFileButton.setEnabled(True)

    def import_script(self, title: str, file_filter: str):
        """执行脚本文件的选择、解析、装载和音频生成共用流程。"""
        selected, _ = QFileDialog.getOpenFileName(
            self, title, '', f'{file_filter};;{script_io.SCRIPT_FILTER};;所有文件 (*.*)')
        if not selected:
            self.create_warning_info_bar('导入已取消', '未选择文件')
            return False

        return self.import_file(selected)

    def import_file(self, path) -> bool:
        """按文件类型导入；按钮选择和拖放共用任务保护及处理流程。"""
        if self.is_busy():
            self.create_warning_info_bar('暂时无法导入', '请等待当前任务完成后再导入文件')
            return False
        path = Path(path)
        if path.suffix.lower() not in (*script_io.SCRIPT_SUFFIXES, project_package.PACKAGE_SUFFIX):
            self.create_warning_info_bar(
                '不支持此文件', '请选择 PPTX、Word（DOCX）、JSON 讲稿或工程包（PPRPKG）')
            return False
        if not path.is_file():
            self.create_warning_info_bar('文件不可用', '请确认文件存在，并拖入文件而非文件夹')
            return False
        snapshot = self._project_snapshot()
        result = (self._import_package_path(path) if path.suffix.lower() == project_package.PACKAGE_SUFFIX
                  else self._import_script_path(path))
        if not result:
            self._restore_project(snapshot)
        return result

    def _import_script_path(self, path: Path) -> bool:
        """解析讲稿后再替换当前内容，并按偏好准备配音。"""
        set_enabled(self.getFileButton, False)
        try:
            stamp = file_stamp(path)
            data = script_io.load_script(path)
            if not data.notes:
                raise ValueError('文件中没有可导入的讲稿页面。')
            if any(page < 1 for page in data.notes):
                raise ValueError('讲稿页码须从第 1 页开始。')
            link = (presentation_link.describe(path)
                    if path.suffix.lower() in presentation_link.PPT_SUFFIXES else {})
            if stamp != file_stamp(path):
                raise RuntimeError('文件正在保存，请稍后再导入。')
        except Exception as e:
            print(e)
            self.create_error_info_bar('讲稿解析失败', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        if not link and self.has_presentation():
            try:
                return self._update_imported_script(data)
            except Exception as exc:
                self.create_error_info_bar('讲稿更新失败', str(exc))
                return False
            finally:
                if not self.is_busy():
                    self.getFileButton.setEnabled(True)

        if link and self.project.notes and not self.has_presentation():
            try:
                choice = self._choose_ppt_import()
                if choice is None:
                    return False
                if stamp != file_stamp(path):
                    raise RuntimeError('PPT 又有修改，请重新导入。')
                if choice == 'link':
                    return self._link_imported_ppt(path, data, link)
            except Exception as exc:
                self.create_error_info_bar('PPT 导入失败', str(exc))
                return False
            finally:
                self.getFileButton.setEnabled(True)

        set_enabled(self.getFileButton, False)
        same_source = bool(self.project.note_file_path and Path(self.project.note_file_path).resolve() == path.resolve())
        same_presentation = bool(link and self.project.presentation_path
                                 and Path(self.project.presentation_path).resolve() == path.resolve())
        if not (same_source or same_presentation):
            self.project.session_record_path = None
            self.generation.resume_page = None
            self.project.audio_edits = {}
            self.project.clear_audio()
        self.project.note_file_path = path
        self.project.presentation_link = link
        self.project.presentation_path = path if self.project.presentation_link else None
        self.project.note_file_name = data.source_name or path.stem
        self.project.notes = data.notes
        self._bind_presentation_monitor(baseline=data.notes if link else None)
        # 交换文件声明的分隔符是其结构一部分，应优先于当前应用偏好。
        if data.mark.strip():
            self.project.mark = data.mark
            self.persist_preferences()

        self.notes_path_text.set_text(str(path))
        if self.prepare_import() is not False:
            InfoBar.info(
                title='正在生成配音' if self.ctx.app_settings.get('auto_generate_audio') else '讲稿已导入', content=' / '.join(data.report),
                orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=2000, parent=self)
            return True
        return False

    def _update_imported_script(self, data: script_io.ScriptData) -> bool:
        """按页码更新当前讲稿，沿用 PPT 关联与已有备注对比基准。"""
        if self.has_presentation():
            presentation_link.validate(self.project.presentation_path, data.notes)
        incoming = dict(data.notes)
        # JSON 声明的分段转换为当前分隔符，避免影响未更新页的动画分段。
        if data.mark.strip() and data.mark != self.project.mark:
            incoming = {page: text.replace(data.mark, self.project.mark) for page, text in incoming.items()}
        updated = {**self.project.notes, **incoming}
        comments_enabled = bool(self.ctx.app_settings.get('script_comments_enabled'))
        pages = changed_pages(self.project.notes, updated, comments_enabled=comments_enabled)
        if not pages and self.project.audio_loaded and comments_enabled == self.project.script_comments_enabled:
            if updated != self.project.notes:
                if self.project.session_record_path:
                    try:
                        presentation_link.update_record(self.project.session_record_path, self.project.presentation_link,
                                                        notes=updated)
                    except Exception as exc:
                        self.create_error_info_bar('备注保存失败', str(exc))
                        return False
                self.project.notes = updated
            self.create_success_info_bar('讲稿已是最新', '文件中的讲稿与当前内容一致')
            return True
        self.project.notes = updated
        if self.prepare_import() is False:
            return False
        if pages:
            self.create_success_info_bar('讲稿已更新', f'已更新 {len(pages)} 页讲稿')
        elif self.ctx.app_settings.get('auto_generate_audio'):
            self.create_success_info_bar('正在准备配音', '讲稿内容未变化，将继续生成配音')
        else:
            self.create_success_info_bar('讲稿已是最新', '文件中的讲稿与当前内容一致')
        return True

    def _choose_ppt_import(self):
        options = {}
        if not self.project.audio_loaded:
            options['has_audio'] = False
        if not self.ctx.app_settings.get('auto_generate_audio'):
            options['generate_audio'] = False
        dialog = PptImportDialog(self, **options)
        return dialog.selected_action() if dialog.exec() else None

    def _link_imported_ppt(self, path, data, link) -> bool:
        if any(int(page) < 1 for page in self.project.notes):
            raise ValueError('讲稿页码须从第 1 页开始。')
        required = max(int(page) for page in self.project.notes)
        count = len(data.notes)
        if required > count:
            raise ValueError(f'讲稿包含第 {required} 页，但所选 PPT 只有 {count} 页。')
        self.set_presentation_link({**link, 'slide_count': count}, notes_baseline=data.notes)
        self.create_success_info_bar('PPT 已关联', '当前讲稿已与所选 PPT 关联。')
        return True

    def import_package(self):
        """导入工程包，并复用历史记录加载路径恢复讲稿和音频。"""
        if self.is_busy():
            self.create_warning_info_bar('正在生成音频', '等待当前转换完成后再试')
            return

        selected, _ = QFileDialog.getOpenFileName(
            self, '选择工程包', '', project_package.PACKAGE_FILTER)
        if not selected:
            return

        return self.import_file(selected)

    def _import_package_path(self, path: Path) -> bool:
        """恢复工程包内的讲稿和配音，不重复生成已有配音。"""
        try:
            result = project_package.import_package(path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('工程包导入失败', f'详情：{e}')
            return False

        try:
            self.load_session_record(result['record_path'])
        except MissingAudioError:
            try:
                with result['record_path'].open('r', encoding='utf-8') as stream:
                    record = json.load(stream)
                notes = {int(page): str(text) for page, text in record.get('notes', {}).items()}
                if not notes or any(page < 1 for page in notes):
                    raise RuntimeError('工程包中没有可用的讲稿，请重新导出后再导入。')
                # 此记录由本次导入新建，配音不完整时不加入历史列表。
                result['record_path'].unlink()
                self.clean_and_reset()
                self.project.notes = notes
                self.project.mark = str(record.get('mark') or self.project.mark).strip() or '●'
                self.project.note_file_path = None
                self.project.note_file_name = str(record.get('source_name') or path.stem)
                self.project.session_record_path = None
                self.generation.resume_page = None
                self.project.audio_edits = {}
                self._restore_presentation_link(record)
                self._bind_presentation_monitor(baseline=record.get('presentation_notes'))
                self.notes_path_text.set_text(str(path))
                self.persist_preferences()
                detail = ('已导入讲稿，将按当前语音设置生成配音' if self.ctx.app_settings.get('auto_generate_audio')
                          else '已导入讲稿，可继续处理讲稿或手动生成配音')
                self.create_warning_info_bar('工程包配音不完整', detail)
                return self.prepare_import(
                    comments_enabled=bool(record.get('script_comments_enabled', False))) is not False
            except Exception as exc:
                self.create_error_info_bar('工程包加载失败', str(exc))
                return False
        except Exception as e:
            print(e)
            self.create_error_info_bar('工程包加载失败', f'详情：{e}')
            return False

        detail = f'已导入 {result["restored"]} 条音频'
        if result['skipped']:
            detail += f'，另有 {result["skipped"]} 条本机已存在'
        self.create_success_info_bar('导入完成', detail)
        return True

    def init_general_play(self, force_regenerate: bool = False, *, comments_enabled=None):
        """规范化当前讲稿、重建分段并启动后台音频生成。"""
        enabled = (bool(self.ctx.app_settings.get('script_comments_enabled'))
                   if comments_enabled is None else bool(comments_enabled))
        try:
            self.generation.prepare_notes(comments_enabled=enabled)
            self.presentation_monitor.comments_enabled = self.project.script_comments_enabled
        except Exception as e:
            print(e)
            self.create_error_info_bar('讲稿解析错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        if not self._confirm_animation_alignment():
            # 导入和更新入口会恢复操作前的工程，校验取消时不清理原有配音。
            self.getFileButton.setEnabled(True)
            return False

        outdated = self.project.audio_loaded or self.project.audio_outdated
        self.clean_and_reset()
        self.project.audio_outdated = outdated

        try:
            self.clean_temp_folder(self.wav_temp_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('缓存清理错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        try:
            extra = {'audio_edits': self.project.audio_edits} if self.project.audio_edits else {}
            self.save_thread.configure(self.project.notes_list,
                                       force_regenerate=force_regenerate, **extra)
            self.save_thread.start()
            self.check_import()
            self.IndeterminateProgressBar.setVisible(True)
        except Exception as e:
            print(e)
            self.create_error_info_bar('语音转换错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

    def _confirm_animation_alignment(self):
        if not self.ctx.app_settings.get('animation_check_enabled') or not self.project.presentation_path:
            return True
        try:
            issues = animation_check.check_alignment(self.project.presentation_path, self.project.notes_list)
        except Exception:
            get_logger().warning('无法预先检查点击动画', exc_info=True)
            issues = [animation_check.AnimationIssue(
                None, None, None, '无法读取 PPT 动画，请确认文稿已保存且可以正常打开')]
        if not issues:
            return True
        self.stop_audio()
        self._confirming_generation = True
        try:
            return bool(AnimationCheckDialog(issues, self).exec())
        finally:
            self._confirming_generation = False

    def clean_and_reset(self):
        """停止播放并清空当前配音，讲稿和关联信息由工程对象保留。"""
        self.stop_audio()
        self.release_playback_show()
        self.player.setSource(QUrl())
        self.project.clear_audio()
        self.check_import()
        self.audio_changed.emit()

    def reading_notes(self):
        return self.project.reading_notes()

    @staticmethod
    def clean_temp_folder(path: Path):
        """删除指定目录顶层的临时 WAV 和 MP3 文件。"""
        for pattern in ('*.wav', '*.mp3'):
            for file_path in path.glob(pattern):
                try:
                    file_path.unlink()
                    print(f'已清理 {file_path.name}')
                except Exception as e:
                    print(f'清理文件失败: {file_path.name}, 原因: {e}')
        print('临时音频清理完成')

    def thread_print_index(self, import_index):
        """根据已完成条数刷新生成进度。"""
        if self.save_thread.tts_engine.get_current_option_values().get('sentence_mode') == SENTENCE_MODE:
            return
        text = f'已生成：{import_index}/{len(self.project.notes_list)}'
        print(text)
        self.statusLabel.setText(text)

    def thread_generation_progress(self, progress):
        if progress['short']:
            detail = ('空白讲稿' if progress['blank'] else
                      f'{progress["sentence"]} / {progress["sentences"]} 句')
            self.statusLabel.setText(
                f'配音准备 {progress["completed"]} / {progress["total"]} · '
                f'第 {progress["page"]} 页 · 第 {progress["segment"]} 段'
                f'（{detail}）')

    def thread_save_error(self, message: str):
        """报告生成错误，并恢复被任务占用的界面状态。"""
        print(f'音频生成失败：{message}')
        self.IndeterminateProgressBar.setVisible(False)
        self.getFileButton.setEnabled(True)
        self.project.audio_loaded = False
        self.check_import()
        self.refresh_hotkeys()
        self.refresh_slideshow_watch()
        self.create_error_info_bar('语音转换失败', f'详情：{message}')

    def thread_save_finish(self, result):
        """接收有序生成结果、恢复界面并保存会话。"""
        print('转换完成')
        self.generation.apply_result(result, self.ctx.tts_engine)
        self.project.audio_loaded = True
        try:
            self.save_session_record()
        except Exception as exc:
            self.create_error_info_bar('历史记录保存失败', f'配音仍可播放，请检查保存位置的权限和剩余空间。\n{exc}')
        self.IndeterminateProgressBar.setVisible(False)

        # 将缓存命中数纳入提示，便于用户判断是否发生重新合成。
        total_notes = len(self.project.notes_list)
        if getattr(result, 'sentence_cache_hit_count', 0) > 0:
            self.create_success_info_bar('转换完成',
                                         f'配音已准备就绪，复用 {result.sentence_cache_hit_count} 句配音')
        elif self.project.cache_hit_count > 0:
            cache_info = f'（命中缓存 {self.project.cache_hit_count}/{total_notes} 条）'
            self.create_success_info_bar('转换完成', f'音频播放功能已准备就绪 {cache_info}')
        else:
            self.create_success_info_bar('转换完成', '音频播放功能已准备就绪')
        self.getFileButton.setEnabled(True)
        self.check_import()
        if self.project.notes_list:
            self.set_current_label_text()
        self.refresh_hotkeys()
        self.refresh_slideshow_watch()
        self.remember_session()
        self.audio_changed.emit()


    def save_session_record(self):
        """将当前工程交给历史记录服务保存。"""
        project_history.save_session(self.project, self.session_root_path, self.ctx.tts_engine,
                                     baseline=self.presentation_monitor.baseline)

    def show_session_history_dialog(self):
        """显示历史记录管理器，并加载用户确认的记录。"""
        if self.is_busy():
            self.create_warning_info_bar('暂时无法加载', '请等待当前任务完成后再打开历史记录')
            return
        dialog = SessionHistoryDialog(self.session_root_path, self)
        dialog.presentation_link_changed.connect(self._history_presentation_changed)
        try:
            accepted = dialog.exec()
            record_path = dialog.get_selected_record_path() if accepted else None
        finally:
            dialog.deleteLater()
        if not accepted:
            return
        if not record_path:
            self.create_warning_info_bar('未选择记录', '在历史记录中选择一条记录')
            return

        try:
            self.load_session_record(record_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('历史记录加载失败', f'详情：{e}')

    def load_session_record(self, record_path: Path):
        """先校验历史记录，再装载工程并恢复页面的停止状态。"""
        if self.is_busy():
            raise RuntimeError('请等待当前任务完成后再加载历史记录。')
        loaded = project_history.load_session(
            record_path, self.audio_cache_path, self.ctx.tts_engine.build_audio_cache_key,
            fallback_mark=self.project.mark)
        self.clean_and_reset()
        self.generation.resume_page = None
        self.project.replace_from(loaded.project)
        self._restore_presentation_link(loaded.record)
        self._bind_presentation_monitor(
            baseline=loaded.record.get('presentation_notes'),
            expected_sha=presentation_link.from_record(loaded.record).get('sha256', ''))
        self.check_import()
        if self.project.notes_list:
            self.set_current_label_text()
        self.create_success_info_bar('加载成功', '历史记录已恢复，可直接播放')
        self.refresh_hotkeys()
        self.refresh_slideshow_watch()
        self.audio_changed.emit()

    def audio_revision(self):
        return (str(self.project.session_record_path or ''), tuple(
            (item['page'], item['text'], self.project.note_cache_keys[index], self.project.note_cache_exts[index])
            for index, item in enumerate(self.project.notes_list)
            if index < len(self.project.note_cache_keys) and index < len(self.project.note_cache_exts)))

    def protected_audio_keys(self):
        keys = set(self.project.note_cache_keys)
        for edit in self.project.audio_edits.values():
            keys.update(value['key'] for value in segment_audio.references({'audio_edit': edit}))
        if self.audio_review_dialog is not None:
            keys.update(self.audio_review_dialog.protected_keys())
        return keys

    def apply_segment_audio(self, updates, revision):
        """更新片段音频引用；历史记录保存失败时恢复整批原引用。"""
        if self.is_busy() or not self.project.audio_loaded or revision != self.audio_revision():
            raise RuntimeError('当前讲稿已变化，请重新选择片段后再采用。')
        fields = ('media_list', 'notes_duration_list', 'note_cache_keys', 'note_cache_exts', 'audio_edits')
        before = {name: copy.deepcopy(getattr(self.project, name)) for name in fields}
        self.stop_audio()
        self.player.setSource(QUrl())
        try:
            for index, update in updates.items():
                path = Path(update['path'])
                if not 0 <= index < len(self.project.notes_list) or not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError('片段音频已失效，请重新试听。')
                self.project.media_list[index] = path
                self.project.notes_duration_list[index] = float(update['duration'])
                self.project.note_cache_keys[index] = str(update['key'])
                self.project.note_cache_exts[index] = str(update['ext'])
                if update.get('edit'):
                    self.project.audio_edits[index] = copy.deepcopy(update['edit'])
                else:
                    self.project.audio_edits.pop(index, None)
            self.save_session_record()
        except Exception:
            for name, value in before.items():
                setattr(self.project, name, value)
            raise
        self.remember_session()
        self.refresh_hotkeys()
        self.presentation_state_changed.emit()
        self._refresh_playing_script()

    def open_audio_review(self, *, current_segment=False):
        if self.is_busy() or not self.project.audio_loaded or len(self.project.media_list) != len(self.project.notes_list):
            self.create_warning_info_bar('配音尚未就绪', '完成讲稿配音后可逐段试听和调整。')
            return
        if self.audio_review_dialog is None:
            self.audio_review_dialog = AudioReviewDialog(self, self.window())
        self.audio_review_dialog.show()
        if current_segment:
            self.audio_review_dialog.focus_segment(self.project.current_index)
        self.audio_review_dialog.raise_()
        self.audio_review_dialog.activateWindow()

    def init_play(self):
        """从当前索引开始新的倒计时或正文播放流程。"""
        if self.is_busy() or not self.project.audio_loaded or not self.project.notes_list or not self.project.media_list:
            return
        self.refresh_presentation_state()
        self._page_turn_notified = False
        self._audio_only_playback = False
        self.ctx.playback_bus.request_stop(self)
        if (self.sync_playback_enabled() and self.can_sync_playback()
                and self.ctx.app_settings.get('page_turn_mode') == 'com'):
            self._prepare_playback_show()
            return
        self._continue_audio_playback()

    def _segment_offset(self):
        page = self.project.notes_list[self.project.current_index]['page']
        return self.project.current_index - self.get_index_from_page(page)

    def _prepare_playback_show(self, *, countdown=True):
        self._playback_countdown = countdown
        self.ctx.slideshow_watcher.stop()
        self.getFileButton.setEnabled(False)
        self.playback_show.prepare(self.project.presentation_path,
                                   self.project.notes_list[self.project.current_index]['page'], self._segment_offset(),
                                   allow_start=self.ctx.app_settings.get('slideshow_auto_start'),
                                   align=self.ctx.app_settings.get('slideshow_sync_navigation'),
                                   windowed=self.ctx.app_settings.get('slideshow_windowed'),
                                   preference=self.ctx.app_settings.get('presentation_preference'))
        self._refresh_play_button()
        self.refresh_presentation_state()

    def _finish_playback_preparation(self):
        self.getFileButton.setEnabled(True)
        self._refresh_play_button()
        self.refresh_presentation_state()
        self.presentation_monitor.poll()

    def _playback_show_ready(self):
        self._finish_playback_preparation()
        self.refresh_slideshow_watch()
        if self._playback_countdown:
            self._continue_audio_playback()
        else:
            self.play_notes()

    def _bring_playback_dialog_forward(self):
        window = self.window()
        if hasattr(window, 'switchTo'):
            window.switchTo(self)
        window.raise_()
        window.activateWindow()

    def _playback_restart_required(self, page):
        self._bring_playback_dialog_forward()
        box = MessageBox('从本页开头播放？',
                         f'第 {page} 页暂时无法从当前段落继续放映。可以从本页第一段开始播放。', self)
        box.yesButton.setText('从本页开始')
        box.cancelButton.setText('取消')
        if box.exec():
            self.project.current_index = self.get_index_from_page(page)
            self.set_current_label_text()
            self._prepare_playback_show(countdown=self._playback_countdown)
        else:
            self.stop_audio()

    def _playback_prepare_failed(self, message):
        self._finish_playback_preparation()
        self.create_warning_info_bar('无法准备播放', message)

    def _continue_audio_playback(self):
        if self.currentSwitch.isChecked():
            self.play_wait()
        else:
            self.play_notes()

    def slideshow_source(self):
        return self.playback_show.target_path or self.project.presentation_path

    def describe_slideshow(self):
        if self.playback_show.session:
            show = self.playback_show.session.presentation
            if show.is_active():
                try:
                    return slideshow.describe_window(show.provider, show.window)
                except Exception:
                    return ''
            return ''
        return slideshow.describe(self.project.presentation_path)

    def release_playback_show(self):
        self.ctx.slideshow_watcher.stop()
        self.playback_show.release()

    def on_slideshow_checked(self):
        if (not self._playing_sequence or self.playback_show.preparing
                or not self.playback_show.session or self._audio_only_playback
                or not self.sync_playback_enabled()
                or self.ctx.app_settings.get('page_turn_mode') != 'com'):
            return
        try:
            self.playback_show.ensure_running()
        except RuntimeError as exc:
            self.stop_audio()
            self.create_warning_info_bar('同步播放已停止', str(exc))

    def on_slideshow_ended(self):
        if self.playback_show.preparing:
            return
        if self.playback_show.session and self.playback_show.session.presentation.is_active():
            return
        if (self.sync_playback_enabled() and not self._audio_only_playback
                and self.ctx.app_settings.get('page_turn_mode') == 'com'):
            was_playing = self._playing_sequence
            stop = bool(self.ctx.app_settings.get('slideshow_stop_on_exit'))
            if stop:
                self.stop_audio()
            else:
                self._audio_only_playback = True
            self.release_playback_show()
            self.refresh_slideshow_watch()
            if was_playing:
                if stop:
                    self.create_warning_info_bar('放映已结束', '配音已停止，可点击播放重新连接放映。')
                else:
                    self.create_success_info_bar('放映已结束', '继续播放配音。')

    def play_wait(self):
        """显示数字倒计时，结束后开始正文。"""
        self.is_play_notes = False
        self._playing_sequence = True
        self.countdown.start(self.currentSpinBox.value())
        self._refresh_playing_script()

    def play_notes(self):
        """播放当前正文段对应的音频。"""
        self.is_play_notes = True
        self.countdown.stop()
        if not self.project.media_list:
            self.load_audio_files()
        self.play_audio()

    def load_audio_files(self):
        """扫描临时目录，作为内存播放列表缺失时的恢复兜底。"""
        indexed = []
        for path in list(self.wav_temp_path.glob('*.wav')) + list(self.wav_temp_path.glob('*.mp3')):
            match = self._AUDIO_NAME_RE.match(path.stem)
            if match:
                indexed.append((int(match.group(1)), int(match.group(2)), path))

        self.project.media_list = [item[2] for item in sorted(indexed)]
        print(f'音频列表载入完成，共 {len(self.project.media_list)} 条')

    # 放映进度跟随

    def refresh_slideshow_watch(self):
        """按翻页方式与讲稿状态决定是否订阅放映事件。

        没有讲稿时无从跟随，此时不订阅，启动阶段也就不必加载 COM 依赖。
        """
        self._refresh_sync_switch()
        watcher = self.ctx.slideshow_watcher
        if (self.ctx.app_settings.get('scroll_enabled')
                and self.ctx.app_settings.get('page_turn_mode') == 'com'
                and self.project.notes_list and self.can_sync_playback() and not self._audio_only_playback):
            if self.playback_show.session:
                watcher.start(self.slideshow_source(), application=self.playback_show.application)
            else:
                watcher.start(self.project.presentation_path)
        else:
            watcher.stop()

    def on_slideshow_slide_changed(self, page: int):
        """放映页码变化时对齐播放索引。

        用户在演示软件里手动翻页后，软件不再按自己的计数继续，避免两边错位。
        正在播放正文时从新页重新开始，否则当前音频放完会在新索引上再前进一段。
        """
        if (not self.sync_playback_enabled() or not self.can_sync_playback()
                or not self.ctx.app_settings.get('slideshow_follow_page')
                or self.ctx.app_settings.get('page_turn_mode') != 'com'
                or self._audio_only_playback or self._advancing_show
                or not self.project.notes_list):
            return
        index = self.get_index_from_page(int(page))
        if index < 0 or index == self.project.current_index:
            return

        playing = (self.is_play_notes
                   and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.project.current_index = index
        if self.playback_show.session:
            self.playback_show.click_base = self.playback_show._click_index(
                self.playback_show.session.presentation.window.View)
        self.set_current_label_text()
        if playing:
            self.next_timer.stop()
            self.player.stop()
            self.play_audio()

    def create_success_info_bar(self, title, text):
        """在主页顶部显示短暂的成功提示。"""
        InfoBar.success(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self
        )

    def create_warning_info_bar(self, title, text):
        """在主页顶部显示短暂的警告提示。"""
        InfoBar.warning(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self
        )

    def create_error_info_bar(self, title, text):
        """在主页顶部显示需要手动关闭的错误提示。"""
        log_error(title, text)
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self
        )

    def show_edit_mark_dialog(self):
        """编辑页内分隔符，并在确认后重切讲稿。"""
        if self.is_busy():
            self.create_warning_info_bar('暂时无法修改', '请等待当前任务完成后再修改分隔符')
            return
        extra = {} if self.ctx.app_settings.get('auto_generate_audio') else {'generate_audio': False}
        box = EditMarkMessageBox(self.project.mark, self.window(), has_script=bool(self.project.notes), **extra)
        if box.exec():
            text = box.get_mark()
            if not text or text == self.project.mark:
                return
            snapshot = self._project_snapshot()
            print(f'分隔符：{text}')
            self.project.mark = text
            self.persist_preferences()
            if self.project.notes:
                if not self.refresh_script():
                    self._restore_project(snapshot)

    def show_info_dialog(self):
        """展示当前讲稿快照；配音未就绪时仍提供文字统计。"""
        if not self.project.notes:
            self.create_warning_info_bar('暂无统计信息', '导入讲稿后可查看统计')
            return
        statistics = build_statistics(self.reading_notes(), self.project.notes_list,
                                      self.project.notes_duration_list if self.project.audio_loaded else None,
                                      self.project.mark, sentence_counts=self._sentence_counts())
        dialog = ScriptStatisticsDialog(statistics, self.window())
        dialog.exec()
        dialog.deleteLater()

    def _sentence_counts(self):
        if not self.project.audio_loaded or len(self.project.media_list) != len(self.project.notes_list):
            return None
        result = []
        for index, note in enumerate(self.project.notes_list):
            spoken = self.project.audio_edits.get(index, {}).get('spoken_text', note['text'])
            timeline = load_sentences(self.project.media_list[index], spoken)
            result.append(len(timeline) if timeline else (0 if not spoken.strip() else None))
        return result
