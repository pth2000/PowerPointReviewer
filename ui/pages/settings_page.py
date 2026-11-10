"""动态呈现 TTS 与应用设置，并处理试听、主题和更新检查。"""

import time
import webbrowser
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    HorizontalSeparator,
    IconWidget,
    InfoBadge,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    ToolButton,
    isDarkTheme,
)

from app import paths
from app.app_context import AppContext
from app.audio import cache as audio_cache
from app.audio.countdown import MAX_COUNTDOWN_SECONDS, MIN_COUNTDOWN_SECONDS
from app.audio.playback import AudioOutputWatcher
from app.logging_setup import get_logger, log_error
from app.presentation import slideshow, window_target
from app.presentation.com import PREFERENCES, PROG_IDS, installed_applications
from app.updates import manager as updater
from app.workspaces import TemporaryWorkspace
from engines import qwentts
from settingInterface import Ui_settingInterface
from tasks.local_tts_connect_task import LocalTtsConnectTask
from tasks.preview_task import PreviewTask
from tasks.update_task import UpdateCheckTask, UpdateDownloadTask
from tasks.voice_catalog_task import VoiceCatalogTask
from ui import icons, theme
from ui.dialogs.hotkey_dialog import HotkeyDialog
from ui.dialogs.preview_text_dialog import PreviewTextMessageBox
from ui.dialogs.qwen_clone_voice_dialog import QwenCloneVoiceDialog
from ui.dialogs.unsaved_settings_dialog import UnsavedSettingsDialog
from ui.dialogs.window_target_dialog import WindowTargetDialog
from ui.settings_layout import SettingsColumns
from ui.widget_state import active_modal_dialog, set_enabled


class SettingInterface(QWidget, Ui_settingInterface):
    """管理显式保存的语音设置和即时生效的外观设置。"""

    CONTROL_WIDTH = 180
    CONTROL_SPACING = 8
    CARD_SPACING = theme.PAGE_CARD_SPACING
    ROW_PADDING = 20
    OPTION_ICONS = {
        'rate': icons.FluentSystemIcon.SPEED,
        'speed': icons.FluentSystemIcon.SPEED,
        'volume': icons.FluentSystemIcon.VOLUME,
        'pitch': icons.FluentSystemIcon.WAVE,
        'locale': icons.FluentSystemIcon.LANGUAGE,
        'language_type': icons.FluentSystemIcon.LANGUAGE,
        'region': icons.FluentSystemIcon.GLOBE,
        'api_key': icons.FluentSystemIcon.KEY,
        'model': icons.FluentSystemIcon.MODEL,
        'workspace_id': icons.FluentSystemIcon.FOLDER,
        'instructions': icons.FluentSystemIcon.INSTRUCTIONS,
        'voice': icons.FluentSystemIcon.MICROPHONE,
        'preferred_name': icons.FluentSystemIcon.NAME,
        'reference_audio_path': icons.FluentSystemIcon.MUSIC,
        'audio_mime_type': icons.FluentSystemIcon.DOCUMENT,
        'base_url': icons.FluentSystemIcon.LINK,
        'ws_url': icons.FluentSystemIcon.LINK,
        'request_timeout': icons.FluentSystemIcon.TIMER,
        'seed': icons.FluentSystemIcon.NUMBER,
        'temperature': icons.FluentSystemIcon.SHUFFLE,
        'leading_silence_ms': icons.FluentSystemIcon.LEADING_SPACE,
        'trailing_silence_ms': icons.FluentSystemIcon.TRAILING_SPACE,
        'sentence_mode': FluentIcon.ALIGNMENT,
    }

    # 设置页只发出重新生成意图，主窗口负责将其转接给主页。
    regenerate_requested = Signal()
    hotkeys_changed = Signal()
    page_turn_mode_changed = Signal()
    sync_playback_changed = Signal(bool)
    countdown_preferences_changed = Signal(bool, int)
    slideshow_preferences_changed = Signal()
    usage_guide_requested = Signal()
    advanced_guide_requested = Signal()
    resume_session_changed = Signal(bool)

    # 这些字段与语音设置一起参加脏状态比较；其它偏好由其所属页面即时保存。
    OWNED_APP_KEYS = ('preview_text',)

    def __init__(self, context: AppContext, parent=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.setupUi(self)
        self.verticalLayout_4.setSpacing(theme.PAGE_SECTION_SPACING)
        self.verticalLayout_2.setSpacing(self.CARD_SPACING)
        self.verticalLayout_6.setSpacing(self.CARD_SPACING)
        self.horizontalLayout_14.setSpacing(self.ROW_PADDING)
        self.horizontalLayout_15.setSpacing(self.ROW_PADDING)
        self.savePushButton.setFixedWidth(self.CONTROL_WIDTH)
        self.versionPrimaryPushButton.setFixedWidth(self.CONTROL_WIDTH)
        self.engineSelectComboBox.setFixedWidth(self.CONTROL_WIDTH)

        paths.ensure_runtime_dirs()

        self.versionIconWidget.setIcon(icons.FluentSystemIcon.INFO)
        self.copyrightIconWidget.setIcon(icons.SystemIcon.COPYRIGHT)
        self.githubButton.setIcon(FluentIcon.GITHUB)
        icons.apply(self.giteeButton, ':/image/image/gitee.svg')
        self.versionLabel.setText(self.ctx.version)
        self.bgScrollArea.enableTransparentBackground()
        self.SubtitleLabel.setText('配音设置')
        self.SubtitleLabel_2.setText('关于与帮助')
        theme.space_page_section(self.verticalLayout_2, self.SubtitleLabel)
        theme.space_page_section(self.verticalLayout_6, self.SubtitleLabel_2)
        self._setup_responsive_sections()

        # 引擎 schema 字段名到运行期控件的映射。
        self.dynamic_option_widgets = {}
        self.dynamic_option_cards = []
        # 音色卡片会随引擎、地区或模型变化而重建。
        self._voice_card = None
        self._voice_combo = None
        self._voice_refresh_button = None
        self._catalog_thread = None
        self._qwentts_task = None
        self._qwentts_card = None
        self._qwentts_test_button = None
        self._qwentts_refresh_button = None
        self._qwentts_voice_combo = None
        self._qwen_clone_manage_card = None
        self._preview_text_card = None
        self.saved_state = None
        self._prompting_unsaved = False
        # 由主窗口注入，避免设置页直接依赖主页对象。
        self.can_regenerate_check = lambda: False
        self.sync_playback_available_check = lambda: False
        self.sync_playback_unavailable_hint = lambda: '请先导入讲稿'
        self.playback_preferences_locked_check = lambda: False
        self.page_turn_source = lambda: None
        self.page_turn_description = lambda: slideshow.describe(self.page_turn_source())

        # 语音设置显式保存；保存栏同时承担脏状态提示。
        self.saveCaptionLabel.setText('将修改应用到之后的语音合成')
        self.saveCaptionLabel.setWordWrap(False)

        theme.wrap_title_with_help(
            self.verticalLayout_12, self.savePathLabel,
            '先试听比较效果，再保存设置。保存后可选择用新设置重新生成当前讲稿的配音。')
        # 引擎说明随所选引擎更新
        _row, self.engine_help_icon = theme.wrap_title_with_help(
            self.verticalLayout_11, self.engineSelectPathLabel)
        self.unsaved_badge = InfoBadge.warning('未保存', self.CardWidget_4)
        self.horizontalLayout_15.insertWidget(
            self.horizontalLayout_15.indexOf(self.savePushButton), self.unsaved_badge)
        # clicked 会携带 checked 参数，使用 lambda 防止其误绑定到业务参数。
        self.savePushButton.clicked.connect(lambda: self.save_settings())

        self.versionPrimaryPushButton.clicked.connect(self.get_update)
        self.githubButton.clicked.connect(self.open_github_url)
        self.giteeButton.clicked.connect(self.open_gitee_url)

        self.engineSelectComboBox.clear()
        self.engineSelectComboBox.addItems(self.ctx.tts_engine.get_engine_names())
        self.engineSelectComboBox.currentTextChanged.connect(self.engineSelectComboBox.setToolTip)
        self.engineSelectComboBox.currentIndexChanged.connect(self.change_tts_engine)

        self.horizontalLayout_14.setStretch(0, 1)
        self.engineSelectCaptionLabel.setWordWrap(True)
        self._insert_option_icon(self.horizontalLayout_14, icons.FluentSystemIcon.ENGINE, self.CardWidget_5)
        self._insert_option_icon(self.horizontalLayout_15, icons.FluentSystemIcon.SAVE, self.CardWidget_4)

        # ConfigStore 已在创建页面前加载数据，此处只负责渲染现有状态。
        self.engineSelectComboBox.blockSignals(True)
        self.engineSelectComboBox.setCurrentIndex(self.ctx.tts_engine.get_mode_index())
        self.engineSelectComboBox.blockSignals(False)
        self.engineSelectComboBox.setToolTip(self.engineSelectComboBox.currentText())

        # 固定尾部只创建一次；切换引擎时仅重建 schema 选项和音色卡片。
        self.apply_engine_schema_to_ui()
        self.build_persistent_tail()
        self.setup_voices_list()

        # 更新依赖在首次检查时加载，避免拖慢页面构造。
        self.update_thread = None
        self._automatic_update_checked = False
        self._update_check_automatic = False
        self._update_bar = None

        self.preview_player = QMediaPlayer()
        self.preview_audio_output = QAudioOutput()
        self.preview_player.setAudioOutput(self.preview_audio_output)
        self.preview_device_watcher = AudioOutputWatcher(
            self.preview_player, self.preview_audio_output, '试听器', parent=self)
        self.ctx.playback_bus.stop_requested.connect(self.on_stop_requested)

        self.preview_thread = None
        self._preview_directory = None
        self._preview_requested = False
        self.previewButton.clicked.connect(self.preview_audio)

        # 全部控件渲染后再记录基线，避免将初值规范化误判为用户修改。
        self.capture_saved_state()

    def _size_action_button(self, button, width=None):
        """单个操作占一列，成组按钮按同一列宽分配空间。"""
        button.setFixedSize(QSize(self.CONTROL_WIDTH if width is None else width, 33))

    @staticmethod
    def _insert_option_icon(layout, icon, parent):
        """统一设置项左侧图标的尺寸与文字间距。"""
        widget = IconWidget(icon, parent)
        widget.setFixedSize(QSize(20, 20))
        layout.insertWidget(0, widget, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.insertSpacing(1, max(0, 20 - layout.spacing()))

    def _make_setting_row(self, widget, title, caption, icon, help_text='', *,
                          compact=False, grouped=False):
        """独立卡片和卡内设置行共用标题、说明与控件排版。"""
        layout = QHBoxLayout(widget)
        horizontal = 0 if grouped else self.ROW_PADDING
        layout.setContentsMargins(horizontal, self.ROW_PADDING, horizontal, self.ROW_PADDING)
        layout.setSpacing(self.ROW_PADDING)
        self._insert_option_icon(layout, icon, widget)
        info = QVBoxLayout()
        info.setSpacing(0)
        point_size = theme.COMPACT_CARD_TITLE_POINT_SIZE if compact else theme.CARD_TITLE_POINT_SIZE
        widget.title_label = theme.make_card_title(title, widget, point_size=point_size)
        widget.title_row, widget.help_icon = theme.make_title_row(widget.title_label, help_text)
        info.addLayout(widget.title_row)
        widget.caption_label = CaptionLabel(caption, widget)
        widget.caption_label.setWordWrap(True)
        widget.caption_label.setVisible(bool(caption))
        info.addWidget(widget.caption_label)
        layout.addLayout(info, 1)
        return layout

    def _make_setting_group(self):
        """相关设置共用一个外框，行间使用内缩分隔线。"""
        card = CardWidget(self.importWidget)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(self.ROW_PADDING, 0, self.ROW_PADDING, 0)
        layout.setSpacing(0)
        return card

    def _add_group_row(self, card, title, caption, icon, help_text=''):
        item = QWidget(card)
        item.separator = HorizontalSeparator(card) if card.layout().count() else None
        if item.separator is not None:
            card.layout().addWidget(item.separator)
        self._make_setting_row(item, title, caption, icon, help_text, grouped=True)
        card.layout().addWidget(item)
        return item

    # 未保存状态

    def current_state(self) -> dict:
        """返回包含当前界面值和引擎状态的可比较快照。"""
        return {
            'engine': self.ctx.tts_engine.export_persistent_state(),
            'app': {key: self.ctx.app_settings.get(key) for key in self.OWNED_APP_KEYS},
        }

    def capture_saved_state(self):
        """将当前快照记录为新的已保存基线。"""
        self.saved_state = self.current_state()
        self.update_dirty_indicator()

    def is_dirty(self) -> bool:
        """返回当前配置是否偏离上次保存的基线。"""
        return self.saved_state is not None and self.current_state() != self.saved_state

    def mark_dirty(self):
        """在任一受管字段变化后刷新脏状态。"""
        self.update_dirty_indicator()

    def update_dirty_indicator(self):
        """根据脏状态更新徽标和保存按钮。"""
        dirty = self.is_dirty()
        self.unsaved_badge.setVisible(dirty)
        set_enabled(self.savePushButton, dirty)

    def save_settings(self, allow_regenerate: bool = True):
        """持久化当前设置并更新已保存基线。

        可选地询问是否重建当前讲稿音频，因为既有音频不会随参数修改自动变化。
        """
        if not self.ctx.config.save():
            self.create_error_info_bar('保存失败', f'无法写入 {self.ctx.config.path}')
            return False

        self.capture_saved_state()
        self.create_success_info_bar('设置已保存', '新设置将应用于后续的语音生成')

        if allow_regenerate:
            self.prompt_regenerate()
        return True

    def prompt_regenerate(self):
        """存在可用讲稿时询问是否立即用新设置重新生成音频。"""
        if not self.can_regenerate_check():
            return

        box = MessageBox(
            '重新生成音频',
            '已生成的音频仍使用旧设置。是否立即用新设置重新生成当前讲稿的音频？',
            self.window(),
        )
        box.yesButton.setText('重新生成')
        box.cancelButton.setText('暂不')
        if box.exec():
            self.regenerate_requested.emit()

    def discard_changes(self):
        """恢复已保存基线，并重绘依赖引擎 schema 的控件。"""
        if self.saved_state is None:
            return

        self.ctx.tts_engine.import_persistent_state(self.saved_state['engine'])
        for key, value in self.saved_state['app'].items():
            self.ctx.app_settings.set(key, value)

        self.engineSelectComboBox.blockSignals(True)
        self.engineSelectComboBox.setCurrentIndex(self.ctx.tts_engine.get_mode_index())
        self.engineSelectComboBox.blockSignals(False)

        self.engineSelectComboBox.setToolTip(self.engineSelectComboBox.currentText())
        self.apply_engine_schema_to_ui()
        self.setup_voices_list()
        self.update_dirty_indicator()

    def hideEvent(self, event):
        """用户切离设置页时安排未保存更改确认。"""
        super().hideEvent(event)
        if hasattr(self, 'page_turn_status_timer'):
            self.page_turn_status_timer.stop()
        # 最小化和关窗也会触发 hideEvent；关窗路径由主窗口单独处理。
        if event.spontaneous() or not self.window().isVisible():
            return
        if self._prompting_unsaved or not self.is_dirty():
            return
        # 将模态框推迟到下一轮事件循环，避免打断导航切换动画。
        QTimer.singleShot(0, self.prompt_unsaved_changes)

    def prompt_unsaved_changes(self, allow_regenerate: bool = True):
        """让用户保存或放弃更改，并按需衔接音频重新生成。"""
        if self._prompting_unsaved:
            return False
        if not self.is_dirty():
            return True

        self._prompting_unsaved = True
        try:
            box = UnsavedSettingsDialog(self.window())
            if box.exec():
                if box.action == 'discard':
                    self.discard_changes()
                    return True
                if self.save_settings(allow_regenerate=allow_regenerate):
                    return True
            window = self.window()
            if hasattr(window, 'switchTo'):
                window.switchTo(self)
            return False
        finally:
            self._prompting_unsaved = False

    def on_stop_requested(self, requester):
        """响应其它页面的播放请求，停止当前试听。"""
        if requester is self:
            return
        self._preview_requested = False
        self.preview_player.stop()

    def preview_audio(self):
        """使用当前未保存的语音设置异步生成并播放试听。"""
        if self.preview_thread is not None and self.preview_thread.isRunning():
            return
        self._preview_requested = True
        try:
            if self.preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.preview_player.stop()
            self.preview_player.setSource(QUrl())

            text = str(self.ctx.app_settings.get('preview_text')).strip()
            if not text:
                self._preview_requested = False
                self.create_warning_info_bar('试听文字为空', '在"试听文字"中填写要朗读的内容')
                return
            # 试听文件必须与主页扫描的 TEMP_DIR 根目录隔离。
            paths.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            if self._preview_directory is None:
                self._preview_directory = TemporaryWorkspace(prefix='voice-preview-', dir=paths.PREVIEW_DIR)
            self.clean_preview_files()
            preview_ext = self.ctx.tts_engine.get_output_extension()
            preview_path = Path(self._preview_directory.name) / f'preview_{time.time_ns()}.{preview_ext}'

            set_enabled(self.previewButton, False)
            self.previewButton.setText('生成中…')

            self.preview_thread = PreviewTask(self.ctx.tts_engine, text, str(preview_path), self)
            self.preview_thread.signal_finish.connect(self.on_preview_generated)
            self.preview_thread.signal_error.connect(self.on_preview_error)
            self.preview_thread.finished.connect(partial(self._preview_finished, self.preview_thread))
            self.preview_thread.finished.connect(self.preview_thread.deleteLater)
            self.preview_thread.start()

        except Exception as e:
            self._preview_requested = False
            print(e)
            self.create_error_info_bar('试听启动失败', f'详情：{e}')
            self.previewButton.setEnabled(True)
            self.previewButton.setText('试听')

    def clean_preview_files(self):
        """只删除本窗口的旧试听，不影响其他程序实例。"""
        if self._preview_directory is None:
            return
        for path in Path(self._preview_directory.name).glob('preview_*'):
            try:
                path.unlink()
            except OSError as e:
                print(f'[试听] 清理旧试听文件失败：{path.name}, 原因: {e}')

    def release_preview_workspace(self):
        self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        if self._preview_directory is not None:
            try:
                self._preview_directory.cleanup()
            except OSError:
                get_logger().warning('试听临时目录暂未释放，将在下次启动重试', exc_info=True)
            self._preview_directory = None

    def on_preview_generated(self, path):
        """恢复按钮状态，并以互斥方式播放新试听音频。"""
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        if not self._preview_requested:
            return
        self._preview_requested = False
        self.ctx.playback_bus.request_stop(self)
        self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.preview_player.play()

    def on_preview_error(self, err_msg):
        """恢复按钮状态并展示试听生成错误。"""
        self._preview_requested = False
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        self.create_error_info_bar('试听生成失败', f'详情：{err_msg}')

    def _preview_finished(self, task):
        if self.preview_thread is task:
            self.preview_thread = None

    def clear_dynamic_option_cards(self):
        """移除当前引擎对应的动态选项和音色卡片。"""
        for card in self.dynamic_option_cards:
            self.verticalLayout_2.removeWidget(card)
            card.deleteLater()
        self.dynamic_option_cards = []
        self.dynamic_option_widgets = {}

        if self._voice_card is not None:
            self.verticalLayout_2.removeWidget(self._voice_card)
            self._voice_card.deleteLater()
            self._voice_card = None
            self._voice_combo = None

        if self._qwen_clone_manage_card is not None:
            self.verticalLayout_2.removeWidget(self._qwen_clone_manage_card)
            self._qwen_clone_manage_card.deleteLater()
            self._qwen_clone_manage_card = None

        self._voice_refresh_button = None
        if self._qwentts_card is not None:
            self.verticalLayout_2.removeWidget(self._qwentts_card)
            self._qwentts_card.deleteLater()
        self._qwentts_card = None
        self._qwentts_test_button = None
        self._qwentts_refresh_button = None
        self._qwentts_voice_combo = None

    def tail_index(self) -> int:
        """返回动态引擎卡片应插入的布局索引。

        固定尾部不会随引擎切换重建，动态卡片必须始终插入其前方。
        """
        anchor = self._preview_text_card if self._preview_text_card is not None else self.CardWidget_4
        return max(self.verticalLayout_2.indexOf(anchor), 0)

    def add_section_label(self, title: str, append: bool = False):
        """即时生效的偏好使用独立分区，语音设置保留在试听与保存之前。"""
        if append:
            section = QWidget(self.settings_columns)
            layout = QVBoxLayout(section)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(self.CARD_SPACING)
            self.preference_sections.append(section)
            self._preference_section_layout = layout
            label = SubtitleLabel(title, section)
            layout.addWidget(label)
            theme.space_page_section(layout, label)
            self.settings_columns.set_sections([self.importWidget, *self.preference_sections, self.about_section])
        else:
            label = SubtitleLabel(title, self.importWidget)
            self.verticalLayout_2.insertWidget(
                max(self.verticalLayout_2.indexOf(self.CardWidget_4), 0), label)
            theme.space_page_section(self.verticalLayout_2, label, separated=True)
        return label

    def _setup_responsive_sections(self):
        self.verticalLayout_4.removeWidget(self.importWidget)
        self.verticalLayout_4.removeItem(self.verticalLayout_6)
        self.about_section = QWidget(self.scrollAreaWidgetContents)
        self.about_section.setLayout(self.verticalLayout_6)
        self.verticalLayout_6.setContentsMargins(0, 0, 0, 0)
        spacer = self.verticalLayout_6.itemAt(self.verticalLayout_6.count() - 1)
        if spacer is not None and spacer.spacerItem() is not None:
            self.verticalLayout_6.takeAt(self.verticalLayout_6.count() - 1)
        self.preference_sections = []
        self.settings_columns = SettingsColumns(self.importWidget, self.about_section,
                                               self.scrollAreaWidgetContents)
        self.verticalLayout_4.addWidget(self.settings_columns, 0, Qt.AlignmentFlag.AlignTop)
        self.verticalLayout_4.addStretch()

    def setup_appearance_cards(self):
        """创建即时生效的主题模式、主题色和界面字体卡片。"""
        mode_card = self._make_appearance_card(
            '主题模式', '切换界面的浅色或深色外观',
            icon=icons.FluentSystemIcon.THEME,
            help_text='选择“跟随系统”时，随 Windows 的深浅色设置自动切换。')
        self.theme_mode_combo = ComboBox(mode_card)
        self.theme_mode_combo.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        self.theme_mode_combo.addItems(theme.mode_labels())
        self.theme_mode_combo.setCurrentText(
            theme.label_for_mode(str(self.ctx.app_settings.get('theme_mode'))))
        self.theme_mode_combo.currentTextChanged.connect(self.on_theme_mode_changed)
        mode_card.layout().addWidget(self.theme_mode_combo)

        color_card = self._make_appearance_card(
            '主题色', '设置按钮、选中项等处的强调色', icon=icons.FluentSystemIcon.PALETTE)
        color_controls = QWidget(color_card)
        color_controls.setFixedWidth(self.CONTROL_WIDTH)
        color_layout = QHBoxLayout(color_controls)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(self.CONTROL_SPACING)
        self._color_dots = []
        for value, name in theme.PRESET_COLORS:
            dot = QPushButton(color_card)
            dot.setFixedSize(QSize(22, 22))
            dot.setCursor(Qt.CursorShape.PointingHandCursor)
            dot.setToolTip(f'{name}  {value}')
            dot.clicked.connect(lambda _checked=False, c=value: self.set_theme_color(c))
            color_layout.addWidget(dot)
            self._color_dots.append((value, dot))

        pick_button = PushButton('自定义', color_card)
        self._size_action_button(
            pick_button, self.CONTROL_WIDTH - len(theme.PRESET_COLORS) * (22 + self.CONTROL_SPACING))
        pick_button.clicked.connect(self.pick_theme_color)
        color_layout.addWidget(pick_button)
        color_card.layout().addWidget(color_controls)

        self.refresh_color_dots()

        font_card = self._make_appearance_card(
            '界面字体', '设置界面文字的显示字体',
            icon=icons.FluentSystemIcon.FONT,
            help_text='选择软件界面使用的字体。'
                      '可选项为本机已安装的字体。')
        fonts = theme.available_fonts()
        current = theme.normalize_font(self.ctx.app_settings.get('ui_font'))
        labels = [label for _family, label in fonts]
        # 配置里的字体可能不在列表中，补一项避免当前选择显示不出来。
        if current and current not in [family for family, _label in fonts]:
            labels.append(theme.label_for_font(current))
        self.ui_font_combo = ComboBox(font_card)
        self.ui_font_combo.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        self.ui_font_combo.addItems(labels)
        self.ui_font_combo.setCurrentText(theme.label_for_font(current))
        self.ui_font_combo.currentTextChanged.connect(self.on_ui_font_changed)
        font_card.layout().addWidget(self.ui_font_combo)

    def _make_appearance_card(self, title: str, caption: str, help_text: str = '', *, icon=icons.FluentSystemIcon.SETTING):
        """创建即时生效的偏好设置卡片，加入当前分区。"""
        card = CardWidget(self._preference_section_layout.parentWidget())
        self._make_setting_row(card, title, caption, icon, help_text)
        self._preference_section_layout.addWidget(card)
        return card

    def _make_about_card(self, title, caption, icon, help_text=''):
        """沿用“关于”区域的紧凑卡片样式，按创建顺序排在版权之前。"""
        card = CardWidget(self.scrollAreaWidgetContents)
        card.setFixedHeight(80)
        self._make_setting_row(card, title, caption, icon, help_text, compact=True)
        self.verticalLayout_6.insertWidget(self.verticalLayout_6.indexOf(self.importCardWidget), card)
        return card

    def _set_card_badge(self, card, text: str, level=InfoLevel.INFOAMTION):
        """在卡片标题右侧显示状态标签；说明文字保持静态，状态只体现在标签上。"""
        state = (text, level)
        if getattr(card, '_status_badge_state', None) == state:
            return
        badge = getattr(card, 'status_badge', None)
        if badge is None:
            badge = InfoBadge.make('', card, level=level)
            card.title_row.insertWidget(1, badge)
            card.status_badge = badge
        badge.setText(text)
        badge.setLevel(level)
        badge.setVisible(bool(text))
        card._status_badge_state = state

    def refresh_color_dots(self):
        """刷新主题色按钮，并用描边标记当前颜色。"""
        current = theme.normalize_color(self.ctx.app_settings.get('theme_color'))
        ring = '#FFFFFF' if isDarkTheme() else '#202020'
        for value, dot in self._color_dots:
            border = (f'2px solid {ring}' if value.upper() == current
                      else '1px solid rgba(128, 128, 128, 0.45)')
            dot.setStyleSheet(
                f'QPushButton {{ background-color: {value}; '
                f'border-radius: 11px; border: {border}; }}')

    def on_theme_mode_changed(self, label: str):
        """应用所选明暗模式并立即持久化。"""
        self.ctx.app_settings.set('theme_mode', theme.mode_for_label(label))
        self.apply_current_theme()

    def on_ui_font_changed(self, label: str):
        """应用所选界面字体并立即持久化。"""
        self.ctx.app_settings.set('ui_font', theme.font_for_label(label))
        theme.apply_font(self.ctx.app_settings.get('ui_font'))
        self.ctx.config.save_later()

    def pick_theme_color(self):
        """打开取色器，并在确认后应用自定义主题色。"""
        current = QColor(theme.normalize_color(self.ctx.app_settings.get('theme_color')))
        dialog = theme.create_color_dialog(current, self.window())
        dialog.colorChanged.connect(lambda color: self.set_theme_color(color.name()))
        dialog.exec()

    def set_theme_color(self, color: str):
        """规范化、应用并持久化主题色。"""
        self.ctx.app_settings.set('theme_color', theme.normalize_color(color))
        self.apply_current_theme()

    def apply_current_theme(self):
        """应用外观偏好，并安排配置写入。"""
        theme.apply_theme(self.ctx.app_settings.get('theme_mode'),
                          self.ctx.app_settings.get('theme_color'))
        self.refresh_color_dots()
        self.ctx.config.save_later()

    def build_persistent_tail(self):
        """构建不随引擎切换重建的固定设置区块。"""
        self.setup_preview_text_card()
        self.add_section_label('讲稿与播放', append=True)
        self.setup_resume_session_card()
        self.setup_script_preferences_cards()
        self.setup_countdown_card()
        self.setup_hotkey_card()
        self.add_section_label('放映控制', append=True)
        self.setup_presentation_preference_card()
        self.setup_sync_playback_card()
        self.setup_page_turn_card()
        self.setup_slideshow_preferences_card()
        self.add_section_label('外观', append=True)
        self.setup_appearance_cards()
        self.setup_auto_update_card()
        self.setup_usage_guide_card()
        self.setup_log_card()

    PAGE_TURN_MODES = (('com', '演示软件接口（COM）'), ('foreground', '前台按键'), ('window', '指定窗口'))

    def setup_presentation_preference_card(self):
        card = self._make_appearance_card(
            '演示软件', '选择放映时优先使用的软件',
            icon=icons.FluentSystemIcon.SLIDES,
            help_text='优先连接已打开的对应文稿或放映。自动选择时优先使用已运行的软件。'
                      '首选软件不可用时尝试另一款软件。“动画导出”目前需要 Microsoft PowerPoint。')
        self.presentation_preference_combo = ComboBox(card)
        self.presentation_preference_combo.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        for value, label in PREFERENCES:
            self.presentation_preference_combo.addItem(label, userData=value)
        self.presentation_preference_caption = card.findChild(CaptionLabel)
        current = self.ctx.app_settings.get('presentation_preference')
        index = next((i for i, (value, _label) in enumerate(PREFERENCES) if value == current), 0)
        self.presentation_preference_combo.setCurrentIndex(index)
        self.presentation_preference_combo.currentIndexChanged.connect(self.on_presentation_preference_changed)
        card.layout().addWidget(self.presentation_preference_combo)
        self.refresh_presentation_preference()

    def refresh_presentation_preference(self):
        """仅在构造及返回设置页时刷新本机安装情况，保留已保存的偏好。"""
        installed = dict(installed_applications())
        available = {value: prog_id in installed for (value, _), (prog_id, _name)
                     in zip(PREFERENCES[1:], PROG_IDS)}
        combo = self.presentation_preference_combo
        for index, (value, label) in enumerate(PREFERENCES[1:], 1):
            combo.setItemEnabled(index, available[value])
            combo.setItemText(index, label if available[value] else label + '（未安装）')
        set_enabled(combo, bool(installed))
        self._installed_presentation_preferences = available
        self._update_presentation_preference_hint()

    def _update_presentation_preference_hint(self):
        available = self._installed_presentation_preferences
        names = {value: name for (value, _), (_prog, name) in zip(PREFERENCES[1:], PROG_IDS)}
        installed = [names[value] for value in available if available[value]]
        selected = self.presentation_preference_combo.currentData()
        if not installed:
            text = '安装 PowerPoint 或 WPS 演示后，可自动开始放映'
        elif selected in names and not available[selected]:
            text = f'{names[selected]} 未安装，将使用 {installed[0]}'
        elif len(installed) == 1:
            text = f'当前可用：{installed[0]}'
        else:
            text = '选择放映时优先使用的软件'
        self.presentation_preference_combo.setToolTip(text)

    def on_presentation_preference_changed(self, _index):
        selected = self.presentation_preference_combo.currentData()
        if selected != 'auto' and not self._installed_presentation_preferences.get(selected, False):
            combo = self.presentation_preference_combo
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(self.ctx.app_settings.get('presentation_preference'))))
            combo.blockSignals(False)
            return
        self.ctx.app_settings.set('presentation_preference', self.presentation_preference_combo.currentData())
        self.ctx.config.save_later()
        self._update_presentation_preference_hint()

    def setup_usage_guide_card(self):
        card = self._make_about_card(
            '使用指引', '了解基础操作，探索讲稿与配音的更多用法', icons.FluentSystemIcon.GUIDE)
        self.usage_guide_card = card
        actions = QWidget(card)
        actions.setFixedWidth(self.CONTROL_WIDTH)
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.CONTROL_SPACING)
        width = (self.CONTROL_WIDTH - self.CONTROL_SPACING) // 2
        self.usage_guide_button = PushButton('入门指引', actions)
        self.advanced_guide_button = PushButton('进阶技巧', actions)
        for button in (self.usage_guide_button, self.advanced_guide_button):
            self._size_action_button(button, width)
            layout.addWidget(button)
        self.usage_guide_button.clicked.connect(lambda: self.usage_guide_requested.emit())
        self.advanced_guide_button.clicked.connect(lambda: self.advanced_guide_requested.emit())
        card.layout().addWidget(actions)

    def setup_script_preferences_cards(self):
        options = (
            ('auto_generate_audio', '自动生成配音', '导入或更新讲稿后自动生成配音',
             '关闭后可先处理和导出讲稿，需要配音时点击首页的“生成配音”或“更新配音”。'),
            ('script_comments_enabled', '识别讲稿注释', '朗读时跳过以 // 开头的整行文字',
             '在备注中以 // 开头的整行文字会作为注释保留，可在 // 前添加空格或缩进。'
             '正文中的网址和行末 // 按正文朗读。调整后在下次生成配音时生效。'),
            ('animation_check_enabled', '生成前校验点击动画', '检查空讲稿及点击动画与分段的对应情况',
             '没有可朗读讲稿、点击次数与分段不一致或需要确认的页面会列出提醒，'
             '可查看结果后继续生成或取消。'
             '检查前请先保存对 PPT 的修改。'),
        )
        for key, title, caption, help_text in options:
            card = self._make_appearance_card(
                title, caption, help_text=help_text,
                icon={'script_comments_enabled': icons.FluentSystemIcon.COMMENT,
                      'auto_generate_audio': icons.FluentSystemIcon.MICROPHONE}.get(key, icons.FluentSystemIcon.CHECK))
            control = SwitchButton(card)
            control.setOnText('启用')
            control.setOffText('停用')
            control.setChecked(bool(self.ctx.app_settings.get(key)))
            control.checkedChanged.connect(partial(self.on_script_preference_changed, key))
            card.layout().addWidget(control)
            setattr(self, key.removesuffix('_enabled') + '_switch', control)

    def on_script_preference_changed(self, key, checked):
        self.ctx.app_settings.set(key, bool(checked))
        self.ctx.config.save_later()

    def setup_countdown_card(self):
        card = self.countdown_card = self._make_setting_group()
        self.countdown_switch = SwitchButton(card)
        self.countdown_switch.setOnText('启用')
        self.countdown_switch.setOffText('停用')
        self.countdown_seconds_spin = SpinBox(card)
        self.countdown_seconds_spin.setRange(MIN_COUNTDOWN_SECONDS, MAX_COUNTDOWN_SECONDS)
        self.countdown_seconds_spin.setSuffix(' 秒')
        self.countdown_seconds_spin.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        for title, caption, icon, control in (
            ('倒计时播放', '开始朗读前显示倒计时，每秒播放提示音', icons.FluentSystemIcon.TIMER,
             self.countdown_switch),
            ('倒计时时长', '设置倒计时持续的秒数', icons.SystemIcon.CLOCK,
             self.countdown_seconds_spin),
        ):
            item = self._add_group_row(card, title, caption, icon)
            if control is self.countdown_seconds_spin:
                self.countdown_duration_row = item
                self.countdown_duration_separator = item.separator
            item.layout().addWidget(control)
        self._preference_section_layout.addWidget(card)
        self.refresh_countdown_preferences()
        self.countdown_switch.checkedChanged.connect(self.on_countdown_preferences_changed)
        self.countdown_seconds_spin.valueChanged.connect(self.on_countdown_preferences_changed)

    def on_countdown_preferences_changed(self, *_):
        if self.playback_preferences_locked_check():
            self.refresh_countdown_preferences()
            return
        values = {
            'countdown_enabled': self.countdown_switch.isChecked(),
            'countdown_seconds': self.countdown_seconds_spin.value(),
        }
        self._refresh_countdown_duration()
        if any(self.ctx.app_settings.get(key) != value for key, value in values.items()):
            self.ctx.app_settings.update(values)
            self.ctx.config.save_later()
        self.countdown_preferences_changed.emit(values['countdown_enabled'], values['countdown_seconds'])

    def refresh_countdown_preferences(self, *_):
        with QSignalBlocker(self.countdown_switch), QSignalBlocker(self.countdown_seconds_spin):
            self.countdown_switch.setChecked(bool(self.ctx.app_settings.get('countdown_enabled')))
            self.countdown_seconds_spin.setValue(int(self.ctx.app_settings.get('countdown_seconds')))
        self._refresh_countdown_duration()

    def _refresh_countdown_duration(self):
        enabled = self.countdown_switch.isChecked()
        locked = self.playback_preferences_locked_check()
        self.countdown_duration_row.setVisible(enabled)
        self.countdown_duration_separator.setVisible(enabled)
        set_enabled(self.countdown_switch, not locked)
        set_enabled(self.countdown_seconds_spin, enabled and not locked)
        for control in (self.countdown_switch, self.countdown_seconds_spin):
            control.setToolTip('请先停止播放，再修改此设置。' if locked else '')

    def refresh_playback_preferences_state(self, *_):
        self.refresh_countdown_preferences()
        self.refresh_sync_playback_state()

    def setup_resume_session_card(self):
        card = self._make_appearance_card(
            '继续上次讲稿', '启动时提示恢复上次讲稿和播放位置',
            icon=icons.FluentSystemIcon.HISTORY,
            help_text='恢复后等待手动播放。关闭后仍可在“历史记录列表”中加载讲稿。')
        self.resume_session_switch = SwitchButton(card)
        self.resume_session_switch.setOnText('启用')
        self.resume_session_switch.setOffText('停用')
        self.resume_session_switch.setChecked(bool(self.ctx.app_settings.get('resume_last_session')))
        self.resume_session_switch.checkedChanged.connect(self.on_resume_session_changed)
        card.layout().addWidget(self.resume_session_switch)

    def on_resume_session_changed(self, checked):
        checked = bool(checked)
        if checked != bool(self.ctx.app_settings.get('resume_last_session')):
            self.ctx.app_settings.set('resume_last_session', checked)
            self.ctx.config.save_later()
            self.resume_session_changed.emit(checked)

    def setup_auto_update_card(self):
        card = self._make_about_card(
            '自动检查更新', '启动时检查新版本，有更新时显示通知', icons.FluentSystemIcon.UPDATE)
        self.auto_update_card = card
        self.auto_update_switch = SwitchButton(card)
        self.auto_update_switch.setOnText('启用')
        self.auto_update_switch.setOffText('停用')
        self.auto_update_switch.setChecked(bool(self.ctx.app_settings.get('auto_check_updates')))
        self.auto_update_switch.checkedChanged.connect(self.on_auto_update_changed)
        card.layout().addWidget(self.auto_update_switch)

    def on_auto_update_changed(self, checked):
        self.ctx.app_settings.set('auto_check_updates', bool(checked))
        self.ctx.config.save_later()
        if checked:
            QTimer.singleShot(0, self, self.check_updates_automatically)
        else:
            self.dismiss_update_notice()

    def setup_sync_playback_card(self):
        card = self._make_appearance_card(
            '同步翻页', '朗读讲稿时同步控制 PPT 放映',
            icon=icons.FluentSystemIcon.SYNC,
            help_text='与主页的同步翻页开关保持一致，请在播放前调整。'
                      '未关联 PPT 时，请先在“历史记录列表”中关联文稿。')
        self.sync_playback_switch = SwitchButton(card)
        self.sync_playback_switch.setOnText('启用')
        self.sync_playback_switch.setOffText('停用')
        self.sync_playback_switch.setChecked(bool(self.ctx.app_settings.get('scroll_enabled')))
        self.sync_playback_switch.checkedChanged.connect(self.on_sync_playback_changed)
        card.layout().addWidget(self.sync_playback_switch)
        self.refresh_sync_playback_state()

    def on_sync_playback_changed(self, checked):
        if not self.sync_playback_available_check() or self.playback_preferences_locked_check():
            self.refresh_sync_playback_state()
            return
        checked = bool(checked)
        if checked != bool(self.ctx.app_settings.get('scroll_enabled')):
            self.ctx.app_settings.set('scroll_enabled', checked)
            self.ctx.config.save_later()
        self.sync_playback_changed.emit(checked)

    def refresh_sync_playback_state(self):
        control = self.sync_playback_switch
        control.blockSignals(True)
        control.setChecked(bool(self.ctx.app_settings.get('scroll_enabled')))
        control.blockSignals(False)
        available = bool(self.sync_playback_available_check())
        locked = self.playback_preferences_locked_check()
        set_enabled(control, available and not locked)
        control.setToolTip('请先停止播放，再修改此设置。' if locked else
                           self.sync_playback_unavailable_hint() if not available else '')
        if hasattr(self, 'slideshow_preference_switches'):
            self.refresh_slideshow_preferences_state()

    SLIDESHOW_PREFERENCES = (
        ('slideshow_auto_start', '自动开始放映',
         '点击播放时，自动打开对应 PPT 并开始放映',
         '关闭后，请先在 PowerPoint 或 WPS 中手动开始放映。已有对应放映时直接连接。'),
        ('slideshow_follow_page', '朗读跟随 PPT 翻页',
         '在 PPT 中手动翻页时，朗读切换到该页',
         '播放中翻页，从新页第一段开始朗读。停止时翻页，将讲稿定位到该页。'),
        ('slideshow_sync_navigation', '讲稿定位时同步放映',
         '开始播放、跳转或重置讲稿时，同步调整放映位置',
         '也适用于上一段、下一段和重播。关闭后可单独定位讲稿，连续朗读时仍按讲稿推进放映。'),
        ('slideshow_stop_on_exit', '退出放映时停止朗读',
         '关闭 PPT 放映后，同时停止配音',
         '关闭此项后，退出放映将继续播放剩余配音。'),
    )

    def setup_slideshow_preferences_card(self):
        card = self._make_setting_group()
        self.slideshow_preferences_card = card
        self.slideshow_preference_switches = {}
        self.slideshow_preference_labels = {}
        item = self._add_group_row(card, '放映显示', '设置自动开始放映时的显示方式',
                                   icons.FluentSystemIcon.FULL_SCREEN)
        self.slideshow_display_combo = ComboBox(item)
        self.slideshow_display_combo.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        self.slideshow_display_combo.addItem('全屏放映', userData=False)
        self.slideshow_display_combo.addItem('窗口放映', userData=True)
        self.slideshow_display_combo.setCurrentIndex(int(bool(self.ctx.app_settings.get('slideshow_windowed'))))
        self.slideshow_display_combo.setToolTip('选择自动开始 PowerPoint 放映时的显示方式。\n'
                                               '已开始的放映沿用当前方式。需要在其他窗口中操作时，可选择窗口放映。')
        self.slideshow_display_combo.currentIndexChanged.connect(self.on_slideshow_display_changed)
        item.layout().addWidget(self.slideshow_display_combo)
        for key, title, caption, help_text in self.SLIDESHOW_PREFERENCES:
            icon = {
                'slideshow_auto_start': icons.FluentSystemIcon.SLIDESHOW,
                'slideshow_follow_page': icons.FluentSystemIcon.NEXT_SLIDE,
                'slideshow_sync_navigation': icons.FluentSystemIcon.LINKED_SLIDE,
                'slideshow_stop_on_exit': icons.FluentSystemIcon.STOP,
            }[key]
            item = self._add_group_row(card, title, caption, icon, help_text)
            self.slideshow_preference_labels[key] = (item.title_label, item.caption_label)
            control = SwitchButton(item)
            control.setOnText('启用')
            control.setOffText('停用')
            control.setChecked(bool(self.ctx.app_settings.get(key)))
            control.checkedChanged.connect(partial(self.on_slideshow_preference_changed, key))
            self.slideshow_preference_switches[key] = control
            item.layout().addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self._preference_section_layout.addWidget(card)
        self.refresh_slideshow_preferences_state()

    def refresh_slideshow_preferences_state(self):
        com = self.ctx.app_settings.get('page_turn_mode') == 'com'
        self.slideshow_preferences_card.setVisible(com)
        available = bool(self.sync_playback_available_check())
        locked = self.playback_preferences_locked_check()
        enabled = com and available and not locked and bool(self.ctx.app_settings.get('scroll_enabled'))
        hint = ('请先停止播放，再修改此设置。' if locked else
                self.sync_playback_unavailable_hint() if not available else
                '请先启用同步翻页' if not enabled else '')
        self.slideshow_display_combo.blockSignals(True)
        self.slideshow_display_combo.setCurrentIndex(int(bool(self.ctx.app_settings.get('slideshow_windowed'))))
        self.slideshow_display_combo.blockSignals(False)
        self.slideshow_display_combo.setEnabled(enabled and bool(self.ctx.app_settings.get('slideshow_auto_start')))
        self.slideshow_display_combo.setToolTip(
            hint if not enabled else
            '选择自动开始放映时使用全屏或窗口显示。已开始的放映沿用当前方式。'
            if self.ctx.app_settings.get('slideshow_auto_start') else '请先启用自动开始放映')
        for key, control in self.slideshow_preference_switches.items():
            control.blockSignals(True)
            control.setChecked(bool(self.ctx.app_settings.get(key)))
            control.blockSignals(False)
            set_enabled(control, enabled)
            control.setToolTip(hint)
            title, caption = self.slideshow_preference_labels[key]
            title.setTextColor('#000000' if enabled else '#919191',
                               '#ffffff' if enabled else '#8a8a8a')
            caption.setTextColor('#000000' if enabled else '#a5a5a5',
                                 '#ffffff' if enabled else '#777777')

    def on_slideshow_preference_changed(self, key, checked):
        if (self.playback_preferences_locked_check() or not self.sync_playback_available_check()
                or not self.ctx.app_settings.get('scroll_enabled')
                or self.ctx.app_settings.get('page_turn_mode') != 'com'):
            self.refresh_slideshow_preferences_state()
            return
        if bool(checked) != bool(self.ctx.app_settings.get(key)):
            self.ctx.app_settings.set(key, bool(checked))
            self.ctx.config.save_later()
            self.slideshow_preferences_changed.emit()
        self.refresh_slideshow_preferences_state()

    def on_slideshow_display_changed(self, index):
        if not self.slideshow_display_combo.isEnabled():
            self.refresh_slideshow_preferences_state()
            return
        self.ctx.app_settings.set('slideshow_windowed', bool(self.slideshow_display_combo.currentData()))
        self.ctx.config.save_later()
        self.slideshow_preferences_changed.emit()

    def setup_page_turn_card(self):
        """创建翻页方式卡片。"""
        card = self._make_appearance_card('翻页方式', '', icon=icons.FluentSystemIcon.SLIDE_SETTINGS)
        self.page_turn_card = card
        self.page_turn_caption = card.findChildren(CaptionLabel)[0]
        controls = QWidget(card)
        controls.setFixedWidth(self.CONTROL_WIDTH)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(self.CONTROL_SPACING)
        self.page_turn_combo = ComboBox(controls)
        self.page_turn_combo.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        self.page_turn_combo.addItems([label for _mode, label in self.PAGE_TURN_MODES])
        current = str(self.ctx.app_settings.get('page_turn_mode') or 'com')
        labels = dict(self.PAGE_TURN_MODES)
        self.page_turn_combo.setCurrentText(labels.get(current, labels['com']))
        self.page_turn_combo.currentTextChanged.connect(self.on_page_turn_mode_changed)
        controls_layout.addWidget(self.page_turn_combo)

        self.page_turn_pick_button = PushButton('选择窗口', controls)
        self._size_action_button(self.page_turn_pick_button)
        self.page_turn_pick_button.clicked.connect(self.open_window_target_dialog)
        controls_layout.addWidget(self.page_turn_pick_button)
        card.layout().addWidget(controls)
        # 构造阶段不探测放映状态，避免为此在启动时加载 COM 依赖
        self.refresh_page_turn_status(probe=False)
        self.page_turn_status_timer = QTimer(self)
        self.page_turn_status_timer.setInterval(500)
        self.page_turn_status_timer.timeout.connect(self._poll_page_turn_status)

    def open_window_target_dialog(self):
        """打开窗口选择器，确认后立即保存。"""
        dialog = WindowTargetDialog(self.ctx.app_settings.get('page_turn_window'), self)
        if dialog.exec():
            self.ctx.app_settings.set('page_turn_window', dialog.selected)
            self.ctx.config.save_later()
            self.refresh_page_turn_status()

    def on_page_turn_mode_changed(self, label: str):
        """切换翻页方式并立即落盘。"""
        mode = next((m for m, text in self.PAGE_TURN_MODES if text == label), 'com')
        self.ctx.app_settings.set('page_turn_mode', mode)
        self.ctx.config.save_later()
        self.refresh_page_turn_status()
        self.refresh_slideshow_preferences_state()
        self.page_turn_mode_changed.emit()

    def showEvent(self, event):
        """每次切回本页时重新检测放映状态。"""
        super().showEvent(event)
        if hasattr(self, 'presentation_preference_combo'):
            self.refresh_presentation_preference()
        if hasattr(self, 'sync_playback_switch'):
            self.refresh_sync_playback_state()
        if getattr(self, 'page_turn_caption', None) is not None:
            self.refresh_page_turn_status()
            self.page_turn_status_timer.start()

    def _poll_page_turn_status(self):
        if self.isVisible():
            self.refresh_page_turn_status()

    PAGE_TURN_CAPTIONS = {
        'foreground': '模拟按下键盘上的翻页键',
        'com': '通过 PowerPoint 或 WPS 演示的接口翻页',
        'window': '向选定的放映窗口发送翻页',
    }
    PAGE_TURN_HELP = {
        'foreground': '翻页键发送到当前处于前台的窗口，放映时需保持放映窗口在前台。',
        'com': '控制对应的 PowerPoint 或 WPS 放映，切换到其他程序后仍可同步翻页。'
               '可在下方分别设置自动开始放映、翻页跟随、位置同步和退出时停止朗读。',
        'window': '向选定窗口发送滚轮翻页，切换到其他程序后仍然有效。'
                  '放映开始后，点击“选择窗口”指定放映窗口。',
    }

    def refresh_page_turn_status(self, probe: bool = True):
        """按所选翻页方式设置说明文字，并用状态标签反映目标是否可用。

        probe 为假时跳过对演示软件的探测，供构造阶段使用。
        """
        mode = str(self.ctx.app_settings.get('page_turn_mode') or 'com')
        if getattr(self, 'page_turn_pick_button', None) is not None:
            self.page_turn_pick_button.setVisible(mode == 'window')
        if mode not in self.PAGE_TURN_CAPTIONS:
            mode = 'com'
        if getattr(self, '_page_turn_description_mode', None) != mode:
            self.page_turn_caption.setText(self.PAGE_TURN_CAPTIONS[mode])
            theme.set_help_text(self.page_turn_card.help_icon, self.PAGE_TURN_HELP[mode])
            self._page_turn_description_mode = mode

        if mode == 'com':
            if not probe:
                self._set_card_badge(self.page_turn_card, '')
                return
            source = self.page_turn_source()
            if not source:
                self._set_card_badge(self.page_turn_card, '')
                return
            detected = self.page_turn_description()
            if detected:
                self._set_card_badge(self.page_turn_card, detected,
                                     InfoLevel.WARNING if detected.endswith('已暂停') else InfoLevel.SUCCESS)
            else:
                self._set_card_badge(self.page_turn_card, '未放映', InfoLevel.INFOAMTION)
        elif mode == 'window':
            target = self.ctx.app_settings.get('page_turn_window')
            if target and window_target.describe(target):
                self._set_card_badge(self.page_turn_card, '窗口有效', InfoLevel.SUCCESS)
            elif target:
                self._set_card_badge(self.page_turn_card, '窗口已关闭', InfoLevel.WARNING)
            else:
                self._set_card_badge(self.page_turn_card, '未选择窗口', InfoLevel.INFOAMTION)
        else:
            self._set_card_badge(self.page_turn_card, '')

    def setup_hotkey_card(self):
        """创建全局热键配置入口卡片。"""
        card = self._make_appearance_card(
            '快捷键', '在其他程序处于前台时控制朗读',
            icon=icons.FluentSystemIcon.KEYBOARD,
            help_text='讲稿合成完成后生效。可设置播放、停止、上一段、下一段与重播当前段的组合键。')
        self.hotkey_card = card
        self.hotkey_button = PushButton('配置', card)
        self._size_action_button(self.hotkey_button)
        self.hotkey_button.clicked.connect(self.open_hotkey_dialog)
        card.layout().addWidget(self.hotkey_button)
        self.refresh_hotkey_status()

    def refresh_hotkey_status(self):
        """用状态标签反映全局热键是否启用。"""
        enabled = bool(self.ctx.app_settings.get('hotkeys_enabled'))
        self._set_card_badge(
            self.hotkey_card,
            '已启用' if enabled else '未启用',
            InfoLevel.SUCCESS if enabled else InfoLevel.INFOAMTION)

    def open_hotkey_dialog(self):
        """打开热键配置，确认后立即按新配置重新注册。"""
        dialog = HotkeyDialog(self.ctx.app_settings, self.ctx.hotkeys, self)
        accepted = bool(dialog.exec())
        if accepted:
            self.ctx.config.save_later()
        # 对话框会试注册以检测系统占用，结束后需按最终配置重新注册
        self.hotkeys_changed.emit()
        self.refresh_hotkey_status()
        if accepted:
            self.create_success_info_bar('已保存', '全局热键配置已更新')

    def setup_log_card(self):
        """在帮助入口之后、版权之前追加运行日志入口。"""
        card = self._make_about_card(
            '运行日志', '反馈问题时提供运行日志', icons.FluentSystemIcon.LOG,
            help_text='遇到问题时，可将此目录中的日志文件附在反馈中，帮助查找原因。')
        self.log_card = card
        open_button = PushButton('打开目录', card)
        self._size_action_button(open_button)
        open_button.clicked.connect(self.open_log_dir)
        card.layout().addWidget(open_button)

    def open_log_dir(self):
        """在文件管理器中打开日志目录。"""
        try:
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.create_error_info_bar('无法打开日志目录', f'详情：{e}')
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.LOG_DIR))):
            self.create_error_info_bar('无法打开日志目录', f'路径：{paths.LOG_DIR}')

    def setup_preview_text_card(self):
        """创建可编辑试听文字卡片。

        编辑和试听入口放在同一卡片中，使文本来源与操作保持邻近。
        """
        card = CardWidget(self.importWidget)
        h_layout = self._make_setting_row(
            card, '试听文字', '编辑试听时朗读的内容', icons.FluentSystemIcon.HEADPHONE)

        controls = QWidget(card)
        controls.setFixedWidth(self.CONTROL_WIDTH)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(self.CONTROL_SPACING)
        button_width = (self.CONTROL_WIDTH - self.CONTROL_SPACING) // 2
        edit_button = PushButton('编辑', controls)
        self._size_action_button(edit_button, button_width)
        edit_button.clicked.connect(self.edit_preview_text)
        controls_layout.addWidget(edit_button)

        # Qt 在 addWidget 时接管按钮父对象，原有信号连接不会丢失。
        self._size_action_button(self.previewButton, button_width)
        controls_layout.addWidget(self.previewButton)
        h_layout.addWidget(controls)

        insert_idx = self.verticalLayout_2.indexOf(self.CardWidget_4)
        self.verticalLayout_2.insertWidget(max(insert_idx, 0), card)
        self._preview_text_card = card

    def edit_preview_text(self):
        """在独立弹窗中编辑并暂存试听文本。"""
        box = PreviewTextMessageBox(self.ctx.app_settings.get('preview_text'), self)
        if not box.exec():
            return

        self.ctx.app_settings.set('preview_text', box.get_text())
        self.mark_dirty()

    def create_dynamic_option_card(self, option_schema):
        """根据单个 schema 字段创建标签和对应输入控件。"""
        card = CardWidget(self.importWidget)
        title = option_schema.get('label', option_schema.get('key', '配置项'))
        h_layout = self._make_setting_row(
            card, title, option_schema.get('description', ''),
            self.OPTION_ICONS.get(option_schema.get('key'), icons.FluentSystemIcon.SETTING),
            option_schema.get('help', ''))

        option_type = option_schema.get('type')
        default_value = option_schema.get('default')

        if option_type == 'int':
            control = SpinBox(card)
            control.setRange(int(option_schema.get('min', -9999)), int(option_schema.get('max', 9999)))
            control.setSingleStep(int(option_schema.get('step', 1)))
            control.setValue(int(default_value if default_value is not None else 0))
        elif option_type == 'float':
            control = DoubleSpinBox(card)
            control.setRange(float(option_schema.get('min', -9999.0)), float(option_schema.get('max', 9999.0)))
            control.setSingleStep(float(option_schema.get('step', 0.1)))
            control.setValue(float(default_value if default_value is not None else 0.0))
        elif option_type == 'choice':
            control = ComboBox(card)
            # choice 可由引擎在运行期提供，例如 Edge 地区目录。
            choices = self.ctx.tts_engine.get_option_choices(option_schema)
            control.addItems(choices)
            if choices and str(default_value) in choices:
                control.setCurrentIndex(choices.index(str(default_value)))
            elif choices:
                control.setCurrentIndex(0)
        elif option_type == 'password':
            control = LineEdit(card)
            control.setEchoMode(QLineEdit.EchoMode.Password)
            control.setText(str(default_value if default_value is not None else ''))
        else:
            control = LineEdit(card)
            control.setText(str(default_value if default_value is not None else ''))

        control.setFixedSize(QSize(self.CONTROL_WIDTH, 33))
        h_layout.addWidget(control)

        self.verticalLayout_2.insertWidget(self.tail_index(), card)

        option_key = option_schema.get('key')
        if option_key:
            self.dynamic_option_widgets[option_key] = control
        self.dynamic_option_cards.append(card)

    def apply_engine_schema_to_ui(self):
        """清理旧控件，并按当前引擎 schema 重新渲染设置项。"""
        self.clear_dynamic_option_cards()
        current_values = self.ctx.tts_engine.get_current_option_values()
        schema_list = self.ctx.tts_engine.get_current_options_schema()
        mode = self.ctx.tts_engine.get_mode()

        engine_def = self.ctx.tts_engine.get_current_engine_definition()
        self.engineSelectCaptionLabel.setText(engine_def.get('description', ''))
        theme.set_help_text(self.engine_help_icon, engine_def.get('help', ''))

        rendered = []
        for item in schema_list:
            key = item.get('key')

            # 这些千问字段由音色管理弹窗统一编辑，主设置页不重复展示。
            if mode == 'qwen_clone' and key in ('voice', 'reference_audio_path', 'audio_mime_type'):
                continue
            if mode == 'qwentts' and key in ('model', 'voice'):
                continue

            self.create_dynamic_option_card(item)
            control = self.dynamic_option_widgets.get(key)
            if control is None:
                continue
            rendered.append((item, control))

            # 设置初值时屏蔽信号，避免程序化赋值触发脏状态。
            control.blockSignals(True)
            value = current_values.get(key, item.get('default'))
            if isinstance(control, SpinBox):
                control.setValue(int(value))
            elif isinstance(control, DoubleSpinBox):
                control.setValue(float(value))
            elif isinstance(control, ComboBox):
                idx = control.findText(str(value))
                if idx >= 0:
                    control.setCurrentIndex(idx)
            elif isinstance(control, LineEdit):
                control.setText(str(value))
            control.blockSignals(False)

        # 初值就位后再接信号，此后仅真实用户输入会更新引擎状态。
        for item, control in rendered:
            self.connect_option_control(item, control)
        self.update_qwen_option_controls()

    def update_qwen_option_controls(self):
        """按千问模型显示有效参数和语言选项，不重建正在操作的模型控件。"""
        if self.ctx.tts_engine.get_mode() != 'qwen_clone':
            return
        values = self.ctx.tts_engine.get_current_option_values()
        for item in self.ctx.tts_engine.get_current_options_schema():
            control = self.dynamic_option_widgets.get(item['key'])
            if control is None:
                continue
            models = item.get('supported_models')
            if models:
                control.parentWidget().setVisible(values.get('model') in models)
            if item['key'] == 'language_type':
                choices = self.ctx.tts_engine.get_option_choices(item)
                value = values.get('language_type', 'Auto')
                if value not in choices:
                    value = 'Auto'
                    self.ctx.tts_engine.set_current_option('language_type', value)
                control.blockSignals(True)
                control.clear()
                control.addItems(choices)
                control.setCurrentText(value)
                control.blockSignals(False)

    def connect_option_control(self, option_schema, control):
        """将配置控件连接到引擎状态和脏状态更新逻辑。"""
        key = option_schema.get('key')
        if not key:
            return

        # 地区或模型变化会改变音色作用域，需要重建音色下拉。
        rebuild = bool(option_schema.get('rebuild_voices'))

        if isinstance(control, (SpinBox, DoubleSpinBox)):
            control.valueChanged.connect(partial(self.on_option_changed, key, rebuild))
        elif isinstance(control, ComboBox):
            control.currentTextChanged.connect(partial(self.on_option_changed, key, rebuild))
        elif isinstance(control, LineEdit):
            control.textChanged.connect(partial(self.on_option_changed, key, rebuild))
            if self.ctx.tts_engine.get_mode() == 'qwen_clone' and key == 'workspace_id':
                # 工作空间只在编辑完成后查询，避免每输入一个字符就发起请求。
                control.editingFinished.connect(self.setup_voices_list)

    def on_option_changed(self, key, rebuild_voices, value):
        """将控件值写入当前引擎，并标记为未保存。"""
        self.ctx.tts_engine.set_current_option(key, value)
        if self.ctx.tts_engine.get_mode() == 'qwentts' and key == 'base_url':
            self.update_qwentts_service_card()
        if key == 'model':
            self.update_qwen_option_controls()
        if rebuild_voices:
            self.setup_voices_list()
        self.mark_dirty()

    def setup_voices_list(self):
        """按当前引擎、地区和模型重建音色选择卡片。"""
        if self.ctx.tts_engine.get_mode() == 'qwentts':
            self.setup_qwentts_service_card()
            return
        if self._voice_card is not None:
            self.verticalLayout_2.removeWidget(self._voice_card)
            self._voice_card.deleteLater()
            self._voice_card = None
            self._voice_combo = None

        voices_list = []
        if self.ctx.tts_engine.get_mode() == 'qwen_clone':
            # 即使账户尚无音色，也先提供创建和管理入口。
            self.setup_qwen_clone_manage_card()

            # 千问列表以远端为准，避免本地旧值让已删除音色继续显示。
            try:
                voice_items = self.ctx.tts_engine.list_qwen_clone_voice_items()
                voices_list = [str(item.get('voice', '')).strip() for item in voice_items if str(item.get('voice', '')).strip()]
            except Exception as e:
                print(f'[设置][qwen_clone] 刷新远端音色失败：{e}')
                voices_list = []
        else:
            voices_list = self.ctx.tts_engine.get_voices_list()

        if not voices_list:
            return

        card = CardWidget(self.importWidget)
        h_layout = self._make_setting_row(
            card, '发音人选择', '选择合成语音时使用的音色', icons.FluentSystemIcon.VOICE)

        combo = ComboBox(card)
        combo.setFixedWidth(self.CONTROL_WIDTH)
        combo.currentTextChanged.connect(combo.setToolTip)
        combo.addItems(voices_list)
        combo.currentIndexChanged.connect(self._on_voice_combo_changed)

        # 只有 Edge 音色目录会由服务端持续更新，因此只为该引擎提供刷新按钮。
        self._voice_refresh_button = None
        controls = QHBoxLayout()
        controls.setSpacing(self.CONTROL_SPACING)
        if self.ctx.tts_engine.get_mode() == 'edge':
            refresh_button = ToolButton(icons.SystemIcon.UPDATE, card)
            refresh_button.setFixedSize(QSize(33, 33))
            refresh_button.setToolTip('联网刷新音色列表')
            refresh_button.clicked.connect(self.refresh_voice_catalog)
            controls.addWidget(refresh_button)
            self._voice_refresh_button = refresh_button
        controls.addWidget(combo)
        h_layout.addLayout(controls)

        self.verticalLayout_2.insertWidget(self.tail_index(), card)

        # 千问使用服务端音色 ID；其余引擎按稳定名称优先、历史索引兜底恢复。
        target_voice = ''
        if self.ctx.tts_engine.get_mode() == 'qwen_clone':
            target_voice = str(self.ctx.tts_engine.get_current_option_values().get('voice', '')).strip()

        matched_index = combo.findText(target_voice) if target_voice else -1
        combo.setCurrentIndex(
            matched_index if matched_index >= 0 else self.ctx.tts_engine.resolve_voice_index(voices_list))
        combo.setToolTip(combo.currentText())

        self._voice_card = card
        self._voice_combo = combo
        # 重建后索引可能数值未变而信号不触发，必须主动同步名称以保证缓存身份正确。
        self._on_voice_combo_changed(combo.currentIndex())

    def setup_qwentts_service_card(self):
        """始终提供连接入口；尚无音色时允许使用 Base 模型的默认声音。"""
        if self._qwentts_card is None:
            card = self._make_setting_group()
            connection_row = self._add_group_row(
                card, '服务连接', '连接 qwentts.cpp 服务，读取模型与音色',
                icons.FluentSystemIcon.CONNECT,
                '可连接本机或其他设备部署的 qwentts.cpp 服务。语言和模型在服务端设置。'
                '更换模型或创建复刻音色后，请刷新列表并重新生成旧音频。')
            self._qwentts_test_button = PrimaryPushButton('测试连接', card)
            self._size_action_button(self._qwentts_test_button)
            self._qwentts_test_button.clicked.connect(self.test_qwentts_connection)
            connection_row.layout().addWidget(self._qwentts_test_button)
            voice_row = self._add_group_row(card, '发音人选择', '选择合成语音时使用的音色',
                                            icons.FluentSystemIcon.VOICE)
            controls = QHBoxLayout()
            controls.setSpacing(self.CONTROL_SPACING)
            self._qwentts_voice_combo = ComboBox(card)
            self._qwentts_voice_combo.setFixedWidth(self.CONTROL_WIDTH)
            self._qwentts_voice_combo.currentTextChanged.connect(self._qwentts_voice_combo.setToolTip)
            self._qwentts_voice_combo.currentIndexChanged.connect(self.on_qwentts_voice_changed)
            self._qwentts_refresh_button = ToolButton(icons.SystemIcon.UPDATE, card)
            self._qwentts_refresh_button.setFixedSize(QSize(33, 33))
            self._qwentts_refresh_button.setToolTip('重新连接并刷新模型与音色')
            self._qwentts_refresh_button.clicked.connect(self.test_qwentts_connection)
            controls.addWidget(self._qwentts_refresh_button)
            controls.addWidget(self._qwentts_voice_combo)
            voice_row.layout().addLayout(controls)
            self.verticalLayout_2.insertWidget(self.tail_index(), card)
            self._qwentts_card = card
        self.update_qwentts_service_card()

    def update_qwentts_service_card(self):
        if self._qwentts_card is None or self.ctx.tts_engine.get_mode() != 'qwentts':
            return
        values = self.ctx.tts_engine.get_current_option_values()
        info = self.ctx.tts_engine.get_qwentts_service_info()
        base_model = qwentts.is_base_model(values.get('model', ''))
        instruction_control = self.dynamic_option_widgets.get('instructions')
        if instruction_control is not None:
            set_enabled(instruction_control, not base_model)
        busy = self._qwentts_task is not None
        self._qwentts_test_button.setText('连接中…' if busy else '测试连接')
        voices = info['voices'] if info else []
        combo = self._qwentts_voice_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(voices or [values.get('voice') or '模型默认声音'])
        if values.get('voice') in voices:
            combo.setCurrentText(values['voice'])
        combo.blockSignals(False)
        set_enabled(combo, bool(voices) and not busy)
        set_enabled(self._qwentts_test_button, not busy)
        set_enabled(self._qwentts_refresh_button, not busy)

    def on_qwentts_voice_changed(self, index):
        if self.ctx.tts_engine.get_mode() != 'qwentts' or index < 0:
            return
        voice = self._qwentts_voice_combo.currentText()
        self.ctx.tts_engine.set_current_option('voice', voice)
        self.ctx.tts_engine.set_voice(index, voice)
        self.mark_dirty()

    def test_qwentts_connection(self):
        """后台读取服务目录，测试与刷新共用同一条只读流程。"""
        if self.ctx.tts_engine.get_mode() != 'qwentts' or self._qwentts_task is not None:
            return
        address = self.ctx.tts_engine.get_current_option_values().get('base_url', '')
        try:
            address = qwentts.normalize_base_url(address)
        except (RuntimeError, ValueError) as exc:
            self.create_error_info_bar('连接失败', str(exc))
            return
        self._qwentts_task = LocalTtsConnectTask(address, self)
        self._qwentts_task.signal_finish.connect(self.on_qwentts_connected)
        self._qwentts_task.signal_error.connect(self.on_qwentts_connection_error)
        self._qwentts_task.finished.connect(self.finish_qwentts_connection)
        self._qwentts_task.finished.connect(self._qwentts_task.deleteLater)
        self.update_qwentts_service_card()
        self._qwentts_task.start()

    def qwentts_result_is_current(self):
        if self.ctx.tts_engine.get_mode() != 'qwentts' or self._qwentts_task is None:
            return False
        address = self.ctx.tts_engine.get_current_option_values().get('base_url', '')
        try:
            return (not self._qwentts_task.isInterruptionRequested() and
                    qwentts.normalize_base_url(address) == self._qwentts_task.base_url)
        except (RuntimeError, ValueError):
            return False

    def on_qwentts_connected(self, info):
        if not self.qwentts_result_is_current():
            return
        if self.ctx.tts_engine.apply_qwentts_service_info(info):
            self.mark_dirty()
            voices = f"已读取 {len(info['voices'])} 个音色，可选择音色并试听" if info['voices'] else '将使用模型默认声音'
            self.create_success_info_bar('已连接 qwentts.cpp 服务', f"模型：{info['model']}。{voices}")

    def on_qwentts_connection_error(self, message):
        if not self.qwentts_result_is_current():
            return
        self.create_error_info_bar('连接失败', message)

    def finish_qwentts_connection(self):
        self._qwentts_task = None
        self.update_qwentts_service_card()

    def refresh_voice_catalog(self):
        """启动后台任务，强制刷新 Edge-TTS 音色目录。"""
        if self._catalog_thread is not None and self._catalog_thread.isRunning():
            return

        if self._voice_refresh_button is not None:
            set_enabled(self._voice_refresh_button, False)

        self._catalog_thread = VoiceCatalogTask(self)
        self._catalog_thread.signal_finish.connect(self.on_voice_catalog_refreshed)
        self._catalog_thread.finished.connect(self._catalog_thread.deleteLater)
        self._catalog_thread.start()

    def on_voice_catalog_refreshed(self, ok: bool, message: str):
        """处理目录刷新结果，并在成功后重建地区和音色选项。"""
        self._catalog_thread = None
        if not ok:
            if self._voice_refresh_button is not None:
                self._voice_refresh_button.setEnabled(True)
            self.create_error_info_bar('刷新失败', f'详情：{message}')
            return

        # 服务端可能新增地区，因此引擎选项和音色下拉都需重绘。
        self.apply_engine_schema_to_ui()
        self.setup_voices_list()
        self.create_success_info_bar('音色列表已更新', message)

    def _on_voice_combo_changed(self, index: int):
        """将界面音色选择同步到当前引擎并标记为未保存。"""
        if self._voice_combo is None:
            return

        if index < 0:
            return

        self.ctx.tts_engine.set_voice(index, self._voice_combo.currentText())
        if self.ctx.tts_engine.get_mode() == 'qwen_clone':
            self.ctx.tts_engine.set_current_option('voice', self._voice_combo.currentText().strip())
        self.mark_dirty()

    def setup_qwen_clone_manage_card(self):
        """为千问复刻引擎创建云端音色管理入口。"""
        if self._qwen_clone_manage_card is not None:
            self.verticalLayout_2.removeWidget(self._qwen_clone_manage_card)
            self._qwen_clone_manage_card.deleteLater()
            self._qwen_clone_manage_card = None

        card = CardWidget(self.importWidget)
        h_layout = self._make_setting_row(
            card, '复刻音色管理', '管理保存在云端的复刻音色', icons.FluentSystemIcon.VOICE,
            '可上传参考音频创建音色，或删除不再使用的音色。选中音色并设为当前后，即可用于合成。')

        open_button = PrimaryPushButton('打开音色管理', card)
        self._size_action_button(open_button)
        open_button.clicked.connect(self.open_qwen_clone_voice_dialog)
        h_layout.addWidget(open_button)

        self.verticalLayout_2.insertWidget(self.tail_index(), card)
        self._qwen_clone_manage_card = card

    def open_qwen_clone_voice_dialog(self):
        """打开千问音色管理器，并在关闭后同步可能发生的远端变化。"""
        try:
            dialog = QwenCloneVoiceDialog(self.ctx.tts_engine, self)
            if dialog.exec():
                selected_voice = dialog.get_selected_voice().strip()
                if selected_voice:
                    self.ctx.tts_engine.set_current_option('voice', selected_voice)

                # 确认选择后，重建列表并定位到返回的音色。
                self.setup_voices_list()
                self.mark_dirty()
                self.create_success_info_bar('已更新音色', '当前音色选择已同步到设置页')
            else:
                # 取消只代表不更换当前音色，创建或删除操作可能已经生效。
                self.setup_voices_list()
                self.mark_dirty()
        except Exception as e:
            self.create_error_info_bar('打开管理对话框失败', f'详情：{e}')

    def change_tts_engine(self):
        """切换当前引擎，并重建其设置项和音色列表。"""
        self.ctx.tts_engine.set_mode(self.engineSelectComboBox.currentIndex())
        self.apply_engine_schema_to_ui()
        self.setup_voices_list()
        self.mark_dirty()

    def get_update(self):
        """在后台检查新版本。"""
        self._start_update_check(automatic=False)

    def check_updates_automatically(self):
        """每次启动最多自动检查一次，源码运行时跳过。"""
        if (self._automatic_update_checked or not paths.IS_VELOPACK_INSTALL
                or not self.ctx.app_settings.get('auto_check_updates')):
            return
        if self._start_update_check(automatic=True):
            self._automatic_update_checked = True

    def _start_update_check(self, *, automatic):
        if getattr(self.window(), '_waiting_to_close', False):
            return False
        if self.update_thread is not None and self.update_thread.isRunning():
            return False

        self._update_check_automatic = automatic
        self.update_thread = UpdateCheckTask(self)
        self.update_thread.checked.connect(self.on_update_checked)
        task = self.update_thread
        task.finished.connect(lambda: self._release_update_task(task))
        self.set_update_button('正在检查…', False)
        self.update_thread.start()
        return True

    def _release_update_task(self, task):
        if self.update_thread is task:
            self.update_thread = None
        task.deleteLater()

    def set_update_button(self, text: str = '检查更新', enabled: bool = True):
        """同步版本卡片按钮的文字与可用状态。"""
        self.versionPrimaryPushButton.setText(text)
        set_enabled(self.versionPrimaryPushButton, enabled)

    def on_update_checked(self, status: str, payload):
        """自动检查仅通知，手动检查保留更新说明与确认流程。"""
        self.set_update_button()
        if getattr(self.window(), '_waiting_to_close', False):
            return
        if self._update_check_automatic:
            if not self.ctx.app_settings.get('auto_check_updates'):
                return
            if status == 'available':
                self.show_update_notice(payload)
            elif status == 'error':
                get_logger().warning('自动检查更新失败：%s', payload)
            return
        self.dismiss_update_notice()
        if status == 'unmanaged':
            self.create_warning_info_bar('无法自动更新', '仅安装版与便携版支持自动更新')
        elif status == 'latest':
            self.create_success_info_bar('已是最新版本', f'当前版本 {self.ctx.version}')
        elif status == 'error':
            self.create_error_info_bar('检查更新失败', payload)
        elif status == 'available':
            self.show_update_dialog(payload)

    def dismiss_update_notice(self):
        if self._update_bar is not None:
            self._update_bar.close()
            self._update_bar = None

    def show_update_notice(self, info):
        self.dismiss_update_notice()
        window = self.window()
        bar = InfoBar(icons.SystemIcon.UPDATE, '发现新版本', f'{info.TargetFullRelease.Version} 已发布',
                           orient=Qt.Horizontal, isClosable=True,
                           position=InfoBarPosition.TOP, duration=-1,
                           parent=getattr(window, 'stackedWidget', window))
        button = PushButton('查看更新', bar)
        button.clicked.connect(lambda: self._open_notified_update(info))
        bar.addWidget(button)
        def closed():
            if self._update_bar is bar:
                self._update_bar = None
        bar.closedSignal.connect(closed)
        self._update_bar = bar
        bar.show()

    def _open_notified_update(self, info):
        window = self.window()
        if (getattr(window, '_waiting_to_close', False)
                or active_modal_dialog(window) is not None
                or (self.update_thread is not None and self.update_thread.isRunning())):
            return
        self.dismiss_update_notice()
        self.show_update_dialog(info)

    @staticmethod
    def open_github_url():
        """在系统浏览器打开项目 GitHub 主页。"""
        webbrowser.open('https://github.com/pth2000')

    @staticmethod
    def open_gitee_url():
        """在系统浏览器打开项目 Gitee 主页。"""
        webbrowser.open('https://gitee.com/pth2000')

    def create_success_info_bar(self, title, text):
        """在设置页顶部显示短暂的成功提示。"""
        InfoBar.success(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self
        )

    def create_warning_info_bar(self, title, text):
        """在设置页顶部显示短暂的警告提示。"""
        InfoBar.warning(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self
        )

    def create_error_info_bar(self, title, text):
        """在设置页顶部显示需要手动关闭的错误提示。"""
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

    def show_update_dialog(self, info):
        """展示新版本说明，确认后开始下载。"""
        content = f'下载大小 {audio_cache.format_size(updater.download_size(info))}'
        notes = updater.release_notes(info)
        if notes:
            content += f'\n\n{notes}'
        dialog = MessageBox(f'新版本 {info.TargetFullRelease.Version}', content, self.window())
        dialog.yesButton.setText('下载并重启')
        dialog.cancelButton.setText('稍后')
        if dialog.exec():
            self.download_update(info)

    def download_update(self, info):
        """在后台下载更新包，并在按钮上显示进度。"""

        self.update_thread = UpdateDownloadTask(info, self)
        self.update_thread.progress.connect(
            lambda value: self.set_update_button(f'正在下载 {value}%', False))
        self.update_thread.downloaded.connect(self.on_update_downloaded)
        task = self.update_thread
        task.finished.connect(lambda: self._release_update_task(task))
        self.set_update_button('正在下载…', False)
        self.update_thread.start()

    def on_update_downloaded(self, info, error: str):
        """下载完成后交由 Velopack 在退出时安装，并关闭主窗口。"""
        if not error:
            try:
                updater.create_manager().wait_exit_then_apply_updates(info)
            except Exception as e:
                error = str(e)
        if error:
            self.set_update_button()
            self.create_error_info_bar('更新失败', error)
            return
        self.set_update_button('正在重启…', False)
        self.window().close()
