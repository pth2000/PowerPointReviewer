"""动态呈现 TTS 与应用设置，并处理试听、主题和更新检查。"""

import time
import webbrowser
from functools import partial

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    InfoBadge,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    SubtitleLabel,
    ToolButton,
    isDarkTheme,
)

from app import icons, paths, theme
from app.app_context import AppContext
from app.playback import AudioOutputWatcher
from settingInterface import Ui_settingInterface
from tasks.preview_task import PreviewTask
from tasks.voice_catalog_task import VoiceCatalogTask
from ui.dialogs.preview_text_dialog import PreviewTextMessageBox
from ui.dialogs.qwen_clone_voice_dialog import QwenCloneVoiceDialog


class SettingInterface(QWidget, Ui_settingInterface):
    """管理显式保存的语音设置和即时生效的外观设置。"""

    # 设置页只发出重新生成意图，主窗口负责将其转接给主页。
    regenerate_requested = Signal()

    # 这些字段与语音设置一起参加脏状态比较；其它偏好由其所属页面即时保存。
    OWNED_APP_KEYS = ('preview_text',)

    def __init__(self, context: AppContext, parent=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.setupUi(self)

        paths.ensure_runtime_dirs()

        icons.apply(self.versionIconWidget, ':/image/image/update.svg')
        icons.apply(self.copyrightIconWidget, ':/image/image/info.svg')
        self.githubButton.setIcon(FluentIcon.GITHUB)
        icons.apply(self.giteeButton, ':/image/image/gitee.svg')
        self.versionLabel.setText(self.ctx.version)
        self.bgScrollArea.enableTransparentBackground()
        # 统一旧 .ui 标题与动态新增分区的命名风格。
        self.SubtitleLabel.setText('语音引擎')

        # 引擎 schema 字段名到运行期控件的映射。
        self.dynamic_option_widgets = {}
        self.dynamic_option_cards = []
        # 音色卡片会随引擎、地区或模型变化而重建。
        self._voice_card = None
        self._voice_combo = None
        self._voice_refresh_button = None
        self._catalog_thread = None
        self._qwen_clone_manage_card = None
        self._preview_text_card = None
        self._preview_text_caption = None
        self.saved_state = None
        self._prompting_unsaved = False
        # 由主窗口注入，避免设置页直接依赖主页对象。
        self.can_regenerate_check = lambda: False

        # 语音设置显式保存；保存栏同时承担脏状态提示。
        self.saveCaptionLabel.setText('保存后生效，已生成的音频不受影响')
        self.saveCaptionLabel.setWordWrap(False)
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
        self.engineSelectComboBox.currentIndexChanged.connect(self.change_tts_engine)

        self.horizontalLayout_14.setStretch(0, 1)
        self.engineSelectCaptionLabel.setWordWrap(True)

        # ConfigStore 已在创建页面前加载数据，此处只负责渲染现有状态。
        self.engineSelectComboBox.blockSignals(True)
        self.engineSelectComboBox.setCurrentIndex(self.ctx.tts_engine.get_mode_index())
        self.engineSelectComboBox.blockSignals(False)

        # 固定尾部只创建一次；切换引擎时仅重建 schema 选项和音色卡片。
        self.apply_engine_schema_to_ui()
        self.build_persistent_tail()
        self.setup_voices_list()

        # 检查更新由用户主动触发，其依赖的 requests 导入较慢，
        # 因此线程对象也推迟到首次检查时再创建。
        self.update_thread = None

        self.preview_player = QMediaPlayer()
        self.preview_audio_output = QAudioOutput()
        self.preview_player.setAudioOutput(self.preview_audio_output)
        self.preview_device_watcher = AudioOutputWatcher(
            self.preview_player, self.preview_audio_output, '试听器', parent=self)
        self.ctx.playback_bus.stop_requested.connect(self.on_stop_requested)

        self.preview_thread = None
        self.previewButton.clicked.connect(self.preview_audio)

        # 全部控件渲染后再记录基线，避免将初值规范化误判为用户修改。
        self.capture_saved_state()

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
        self.savePushButton.setEnabled(dirty)

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

        self.apply_engine_schema_to_ui()
        self.setup_voices_list()
        self.update_dirty_indicator()

    def hideEvent(self, event):
        """用户切离设置页时安排未保存更改确认。"""
        super().hideEvent(event)
        # 最小化和关窗也会触发 hideEvent；关窗路径由主窗口单独处理。
        if event.spontaneous() or not self.window().isVisible():
            return
        if self._prompting_unsaved or not self.is_dirty():
            return
        # 将模态框推迟到下一轮事件循环，避免打断导航切换动画。
        QTimer.singleShot(0, self.prompt_unsaved_changes)

    def prompt_unsaved_changes(self, allow_regenerate: bool = True):
        """让用户保存或放弃更改，并按需衔接音频重新生成。"""
        if self._prompting_unsaved or not self.is_dirty():
            return

        self._prompting_unsaved = True
        try:
            box = MessageBox(
                '未保存的设置更改',
                '设置已修改但尚未保存，放弃后将恢复为上次保存的配置。',
                self.window(),
            )
            box.yesButton.setText('保存')
            box.cancelButton.setText('放弃更改')
            if box.exec():
                self.save_settings(allow_regenerate=allow_regenerate)
            else:
                self.discard_changes()
        finally:
            self._prompting_unsaved = False

    def on_stop_requested(self, requester):
        """响应其它页面的播放请求，停止当前试听。"""
        if requester is self:
            return
        self.preview_player.stop()

    def preview_audio(self):
        """使用当前未保存的语音设置异步生成并播放试听。"""
        try:
            if self.preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.preview_player.stop()
            self.preview_player.setSource(QUrl())

            print(
                f"Main Preview Debug: Mode={self.ctx.tts_engine.get_mode()}, "
                f"VoiceIndex={self.ctx.tts_engine.get_selected_voice_index()}"
            )

            text = str(self.ctx.app_settings.get('preview_text')).strip()
            if not text:
                self.create_warning_info_bar('试听文字为空', '请先在"试听文字"中填写要朗读的内容。')
                return
            # 试听文件必须与主页扫描的 TEMP_DIR 根目录隔离。
            paths.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            self.clean_preview_files()
            preview_ext = self.ctx.tts_engine.get_output_extension()
            preview_path = paths.PREVIEW_DIR / f'preview_{int(time.time())}.{preview_ext}'

            self.previewButton.setEnabled(False)
            self.previewButton.setText('正在生成...')

            self.preview_thread = PreviewTask(self.ctx.tts_engine, text, str(preview_path), self)
            self.preview_thread.signal_finish.connect(self.on_preview_generated)
            self.preview_thread.signal_error.connect(self.on_preview_error)
            self.preview_thread.finished.connect(self.preview_thread.deleteLater)
            self.preview_thread.start()

        except Exception as e:
            print(e)
            self.create_error_info_bar('试听启动失败', f'详情：{e}')
            self.previewButton.setEnabled(True)
            self.previewButton.setText('试听')

    @staticmethod
    def clean_preview_files():
        """删除旧试听文件，避免重复试听持续占用磁盘。"""
        for path in paths.PREVIEW_DIR.glob('preview_*'):
            try:
                path.unlink()
            except OSError as e:
                print(f'[试听] 清理旧试听文件失败：{path.name}, 原因: {e}')

    def on_preview_generated(self, path):
        """恢复按钮状态，并以互斥方式播放新试听音频。"""
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        self.ctx.playback_bus.request_stop(self)
        self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.preview_player.play()

    def on_preview_error(self, err_msg):
        """恢复按钮状态并展示试听生成错误。"""
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        self.create_error_info_bar('试听生成失败', f'详情：{err_msg}')

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

    def tail_index(self) -> int:
        """返回动态引擎卡片应插入的布局索引。

        固定尾部不会随引擎切换重建，动态卡片必须始终插入其前方。
        """
        anchor = self._preview_text_card if self._preview_text_card is not None else self.CardWidget_4
        return max(self.verticalLayout_2.indexOf(anchor), 0)

    def add_section_label(self, title: str, append: bool = False):
        """创建分区标题，并按配置生命周期选择插入位置。

        ``append=True`` 用于即时保存的外观区块，将其放在显式保存卡片之后。
        """
        label = SubtitleLabel(title, self.importWidget)
        label.setMinimumSize(QSize(0, 30))
        label.setMaximumSize(QSize(16777215, 30))
        if append:
            self.verticalLayout_2.addWidget(label)
        else:
            self.verticalLayout_2.insertWidget(
                max(self.verticalLayout_2.indexOf(self.CardWidget_4), 0), label)
        return label

    def setup_appearance_cards(self):
        """创建即时生效的主题模式和主题色卡片。"""
        mode_card = self._make_appearance_card(
            '主题模式', '界面明暗；跟随系统时随 Windows 的深色设置切换')
        self.theme_mode_combo = ComboBox(mode_card)
        self.theme_mode_combo.setMinimumSize(QSize(180, 33))
        self.theme_mode_combo.setMaximumSize(QSize(180, 33))
        self.theme_mode_combo.addItems(theme.mode_labels())
        self.theme_mode_combo.setCurrentText(
            theme.label_for_mode(str(self.ctx.app_settings.get('theme_mode'))))
        self.theme_mode_combo.currentTextChanged.connect(self.on_theme_mode_changed)
        mode_card.layout().addWidget(self.theme_mode_combo)

        color_card = self._make_appearance_card('主题色', '按钮、选中态等强调色')
        self._color_dots = []
        for value, name in theme.PRESET_COLORS:
            dot = QPushButton(color_card)
            dot.setFixedSize(QSize(22, 22))
            dot.setCursor(Qt.CursorShape.PointingHandCursor)
            dot.setToolTip(f'{name}  {value}')
            dot.clicked.connect(lambda _checked=False, c=value: self.set_theme_color(c))
            color_card.layout().addWidget(dot)
            self._color_dots.append((value, dot))

        color_card.layout().addSpacing(8)
        pick_button = PushButton('自定义', color_card)
        pick_button.setMinimumSize(QSize(90, 33))
        pick_button.setMaximumSize(QSize(90, 33))
        pick_button.clicked.connect(self.pick_theme_color)
        color_card.layout().addWidget(pick_button)

        self.refresh_color_dots()


    def _make_appearance_card(self, title: str, caption: str):
        """创建外观设置卡片骨架，并返回可追加控件的布局。"""
        card = CardWidget(self.importWidget)
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_label = theme.make_card_title(title, card)
        info_layout.addWidget(title_label)

        caption_label = CaptionLabel(card)
        caption_label.setText(caption)
        caption_label.setWordWrap(True)
        info_layout.addWidget(caption_label)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(20)

        self.verticalLayout_2.addWidget(card)
        return card

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
        self.add_section_label('外观', append=True)
        self.setup_appearance_cards()

    def setup_preview_text_card(self):
        """创建可编辑试听文字卡片。

        编辑和试听入口放在同一卡片中，使文本来源与操作保持邻近。
        """
        card = CardWidget(self.importWidget)
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_lbl = theme.make_card_title('试听文字', card)
        info_layout.addWidget(title_lbl)

        cap_lbl = CaptionLabel(card)
        cap_lbl.setText('试听时朗读的文本内容')
        cap_lbl.setWordWrap(True)
        info_layout.addWidget(cap_lbl)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(20)

        edit_button = PushButton('编辑', card)
        edit_button.setMinimumSize(QSize(90, 33))
        edit_button.setMaximumSize(QSize(90, 33))
        edit_button.clicked.connect(self.edit_preview_text)
        h_layout.addWidget(edit_button)

        # Qt 在 addWidget 时接管按钮父对象，原有信号连接不会丢失。
        self.previewButton.setMinimumSize(QSize(90, 33))
        self.previewButton.setMaximumSize(QSize(90, 33))
        h_layout.addWidget(self.previewButton)

        insert_idx = self.verticalLayout_2.indexOf(self.CardWidget_4)
        self.verticalLayout_2.insertWidget(max(insert_idx, 0), card)
        self._preview_text_card = card
        self._preview_text_caption = cap_lbl

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
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_label = theme.make_card_title(option_schema.get('label', option_schema.get('key', '配置项')), card)
        info_layout.addWidget(title_label)

        desc_label = CaptionLabel(card)
        desc_label.setText(option_schema.get('description', ''))
        desc_label.setWordWrap(True)
        info_layout.addWidget(desc_label)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(20)

        option_type = option_schema.get('type')
        default_value = option_schema.get('default')

        if option_type == 'int':
            control = SpinBox(card)
            control.setMinimumSize(QSize(180, 33))
            control.setMaximumSize(QSize(180, 33))
            control.setRange(int(option_schema.get('min', -9999)), int(option_schema.get('max', 9999)))
            control.setSingleStep(int(option_schema.get('step', 1)))
            control.setValue(int(default_value if default_value is not None else 0))
        elif option_type == 'float':
            control = DoubleSpinBox(card)
            control.setMinimumSize(QSize(180, 33))
            control.setMaximumSize(QSize(180, 33))
            control.setRange(float(option_schema.get('min', -9999.0)), float(option_schema.get('max', 9999.0)))
            control.setSingleStep(float(option_schema.get('step', 0.1)))
            control.setValue(float(default_value if default_value is not None else 0.0))
        elif option_type == 'choice':
            control = ComboBox(card)
            control.setMinimumSize(QSize(240, 0))
            control.setMaximumSize(QSize(240, 16777215))
            # choice 可由引擎在运行期提供，例如 Edge 地区目录。
            choices = self.ctx.tts_engine.get_option_choices(option_schema)
            control.addItems(choices)
            if choices and str(default_value) in choices:
                control.setCurrentIndex(choices.index(str(default_value)))
            elif choices:
                control.setCurrentIndex(0)
        elif option_type == 'password':
            control = LineEdit(card)
            control.setMinimumSize(QSize(240, 33))
            control.setEchoMode(QLineEdit.EchoMode.Password)
            control.setText(str(default_value if default_value is not None else ''))
        else:
            control = LineEdit(card)
            control.setMinimumSize(QSize(240, 33))
            control.setText(str(default_value if default_value is not None else ''))

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

        rendered = []
        for item in schema_list:
            key = item.get('key')

            # 这些千问字段由音色管理弹窗统一编辑，主设置页不重复展示。
            if mode == 'qwen_clone' and key in ('voice', 'reference_audio_path', 'audio_mime_type'):
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

    def on_option_changed(self, key, rebuild_voices, value):
        """将控件值写入当前引擎，并标记为未保存。"""
        self.ctx.tts_engine.set_current_option(key, value)
        if rebuild_voices:
            self.setup_voices_list()
        self.mark_dirty()

    def setup_voices_list(self):
        """按当前引擎、地区和模型重建音色选择卡片。"""
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
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_lbl = theme.make_card_title('发音人选择', card)
        info_layout.addWidget(title_lbl)

        cap_lbl = CaptionLabel(card)
        cap_lbl.setText('选择当前引擎的发音人')
        cap_lbl.setWordWrap(True)
        info_layout.addWidget(cap_lbl)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(20)

        combo = ComboBox(card)
        combo.setMinimumSize(QSize(240, 0))
        combo.setMaximumSize(QSize(240, 16777215))
        combo.addItems(voices_list)
        combo.currentIndexChanged.connect(self._on_voice_combo_changed)
        h_layout.addWidget(combo)

        # 只有 Edge 音色目录会由服务端持续更新，因此只为该引擎提供刷新按钮。
        self._voice_refresh_button = None
        if self.ctx.tts_engine.get_mode() == 'edge':
            refresh_button = ToolButton(FluentIcon.SYNC, card)
            refresh_button.setFixedSize(QSize(33, 33))
            refresh_button.setToolTip('联网刷新音色列表')
            refresh_button.clicked.connect(self.refresh_voice_catalog)
            h_layout.addWidget(refresh_button)
            self._voice_refresh_button = refresh_button

        self.verticalLayout_2.insertWidget(self.tail_index(), card)

        # 千问使用服务端音色 ID；其余引擎按稳定名称优先、历史索引兜底恢复。
        target_voice = ''
        if self.ctx.tts_engine.get_mode() == 'qwen_clone':
            target_voice = str(self.ctx.tts_engine.get_current_option_values().get('voice', '')).strip()

        matched_index = combo.findText(target_voice) if target_voice else -1
        combo.setCurrentIndex(
            matched_index if matched_index >= 0 else self.ctx.tts_engine.resolve_voice_index(voices_list))

        self._voice_card = card
        self._voice_combo = combo
        # 重建后索引可能数值未变而信号不触发，必须主动同步名称以保证缓存身份正确。
        self._on_voice_combo_changed(combo.currentIndex())


    def refresh_voice_catalog(self):
        """启动后台任务，强制刷新 Edge-TTS 音色目录。"""
        if self._catalog_thread is not None and self._catalog_thread.isRunning():
            return

        if self._voice_refresh_button is not None:
            self._voice_refresh_button.setEnabled(False)

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
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_lbl = theme.make_card_title('复刻音色管理', card)
        info_layout.addWidget(title_lbl)

        cap_lbl = CaptionLabel(card)
        cap_lbl.setText('创建、刷新、删除复刻音色，并指定当前使用的音色')
        cap_lbl.setWordWrap(True)
        info_layout.addWidget(cap_lbl)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(20)

        open_button = PrimaryPushButton('打开音色管理', card)
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
        except Exception as e:
            self.create_error_info_bar('打开管理对话框失败', f'详情：{e}')

    def change_tts_engine(self):
        """切换当前引擎，并重建其设置项和音色列表。"""
        self.ctx.tts_engine.set_mode(self.engineSelectComboBox.currentIndex())
        self.apply_engine_schema_to_ui()
        self.setup_voices_list()
        self.mark_dirty()

    def get_update(self):
        """启动后台发行版检查。"""
        if self.update_thread is None:
            from tasks.update_task import UpdateTask

            self.update_thread = UpdateTask(self.ctx.version, self)
            self.update_thread.signal_finish.connect(self.thread_get_update_finish)

        self.versionPrimaryPushButton.setEnabled(False)
        self.update_thread.start()

    def thread_get_update_finish(self, data_list):
        """根据统一结果码展示更新状态或下载入口。"""
        self.versionPrimaryPushButton.setEnabled(True)
        if data_list[0] == 0:
            self.create_success_info_bar(data_list[1], data_list[2])
        elif data_list[0] == 1:
            self.show_update_dialog(data_list[1], data_list[2], data_list[3])
        else:
            self.create_error_info_bar(data_list[1], data_list[2])

    @staticmethod
    def open_github_url():
        """在系统浏览器打开项目 GitHub 主页。"""
        webbrowser.open('https://github.com/pth2000')

    @staticmethod
    def open_gitee_url():
        """在系统浏览器打开项目 Gitee 主页。"""
        webbrowser.open('https://gitee.com/pth2000')

    @staticmethod
    def open_update_url(url):
        """在系统浏览器打开更新下载地址。"""
        webbrowser.open(url)

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
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self
        )

    def show_update_dialog(self, title, content, url):
        """显示新版本说明，并在确认后打开下载地址。"""
        dialog = MessageBox(title, content, self)
        dialog.yesButton.setText('获取更新')
        dialog.cancelButton.setText('取消')
        if dialog.exec():
            self.open_update_url(url)
