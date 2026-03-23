"""设置页：负责 TTS 配置、试听与更新检查"""

import json
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    SpinBox,
)

from app.app_context import AppContext
from settingInterface import Ui_settingInterface
from tasks.preview_task import PreviewTask
from tasks.update_task import UpdateTask


class SettingInterface(QWidget, Ui_settingInterface):
    """设置页"""

    def __init__(self, context: AppContext, parent=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.setupUi(self)

        self.settings_file_path = Path('./config/settings.json').resolve()
        self.settings_file_path.parent.mkdir(parents=True, exist_ok=True)

        self.versionIconWidget.setIcon(QIcon(':/image/image/update.svg'))
        self.copyrightIconWidget.setIcon(QIcon(':/image/image/info.svg'))
        self.githubButton.setIcon(FluentIcon.GITHUB)
        self.giteeButton.setIcon(QIcon(':/image/image/gitee.svg'))
        self.versionLabel.setText(self.ctx.version)
        self.bgScrollArea.enableTransparentBackground()

        # 运行期动态控件缓存：key -> widget
        self.dynamic_option_widgets = {}
        self.dynamic_option_cards = []
        # 动态创建的音色卡片，随模型切换重建
        self._voice_card = None
        self._voice_combo = None

        self.savePushButton.clicked.connect(self.save_setting)
        self.versionPrimaryPushButton.clicked.connect(self.get_update)
        self.githubButton.clicked.connect(self.open_github_url)
        self.giteeButton.clicked.connect(self.open_gitee_url)

        self.engineSelectComboBox.clear()
        self.engineSelectComboBox.addItems(self.ctx.tts_engine.get_engine_names())
        self.engineSelectComboBox.currentIndexChanged.connect(self.change_tts_engine)

        self.horizontalLayout_14.setStretch(0, 1)
        self.engineSelectCaptionLabel.setWordWrap(True)

        self.load_persistent_settings()

        self.engineSelectComboBox.blockSignals(True)
        self.engineSelectComboBox.setCurrentIndex(self.ctx.tts_engine.get_mode_index())
        self.engineSelectComboBox.blockSignals(False)

        self.apply_engine_schema_to_ui()
        self.setup_voices_list()

        self.update_thread = UpdateTask(self.ctx.version, self)
        self.update_thread.signal_finish.connect(self.thread_get_update_finish)

        self.preview_player = QMediaPlayer()
        self.preview_audio_output = QAudioOutput()
        self.preview_player.setAudioOutput(self.preview_audio_output)

        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self.handle_audio_device_change)

        self.current_preview_device_id = QMediaDevices.defaultAudioOutput().id()
        self.preview_device_check_timer = QTimer(self)
        self.preview_device_check_timer.timeout.connect(self.check_default_audio_device)
        self.preview_device_check_timer.start(1000)

        self.previewButton.clicked.connect(self.preview_audio)

    def handle_audio_device_change(self):
        """处理设备物理插拔的自适应切换"""
        self.check_default_audio_device()

    def check_default_audio_device(self):
        """发现默认设备变化时切换"""
        default_device = QMediaDevices.defaultAudioOutput()
        if default_device.id() != self.current_preview_device_id:
            self.current_preview_device_id = default_device.id()
            self.preview_audio_output.setDevice(default_device)
            if self.preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                pos = self.preview_player.position()
                self.preview_player.pause()
                self.preview_player.play()
                self.preview_player.setPosition(pos)
            print(f'[试听器] 音频输出设备已自适应切换至: {default_device.description()}')

    def preview_audio(self):
        """试听当前设置"""
        try:
            self.apply_ui_settings_to_engine()

            if self.preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.preview_player.stop()
            self.preview_player.setSource(QUrl())

            print(
                f"Main Preview Debug: Mode={self.ctx.tts_engine.get_mode()}, "
                f"VoiceIndex={self.ctx.tts_engine.get_selected_voice_index()}"
            )

            text = '这是一个试听音频，用于测试当前的语音设置'
            temp_dir = Path('./temp').resolve()
            temp_dir.mkdir(parents=True, exist_ok=True)
            preview_path = temp_dir / f'preview_{int(time.time())}.wav'

            self.previewButton.setEnabled(False)
            self.previewButton.setText('正在生成...')

            self.preview_thread = PreviewTask(self.ctx.tts_engine, text, str(preview_path), self)
            self.preview_thread.signal_finish.connect(self.on_preview_generated)
            self.preview_thread.signal_error.connect(self.on_preview_error)
            self.preview_thread.start()

        except Exception as e:
            print(e)
            self.create_error_info_bar('试听启动失败', f'详情：{e}')
            self.previewButton.setEnabled(True)
            self.previewButton.setText('试听')

    def on_preview_generated(self, path):
        """试听音频生成完毕的回调"""
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.preview_player.play()

    def on_preview_error(self, err_msg):
        """试听音频生成失败的回调"""
        self.previewButton.setEnabled(True)
        self.previewButton.setText('试听')
        self.create_error_info_bar('试听生成失败', f'详情：{err_msg}')

    def clear_dynamic_option_cards(self):
        """清理运行期动态创建的引擎配置卡片"""
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

    def create_dynamic_option_card(self, option_schema):
        """根据 schema 创建一张配置卡片"""
        card = CardWidget(self.importWidget)
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_label = QLabel(card)
        title_label.setText(option_schema.get('label', option_schema.get('key', '配置项')))
        title_label.setStyleSheet("font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")
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
            choices = option_schema.get('choices', [])
            control.addItems([str(item) for item in choices])
            if choices and default_value in choices:
                control.setCurrentIndex(choices.index(default_value))
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

        insert_index = max(self.verticalLayout_2.indexOf(self.CardWidget_4), 0)
        self.verticalLayout_2.insertWidget(insert_index, card)

        option_key = option_schema.get('key')
        if option_key:
            self.dynamic_option_widgets[option_key] = control
        self.dynamic_option_cards.append(card)

    def apply_engine_schema_to_ui(self):
        """按当前引擎 schema 动态渲染设置项"""
        self.clear_dynamic_option_cards()
        current_values = self.ctx.tts_engine.get_current_option_values()
        schema_list = self.ctx.tts_engine.get_current_options_schema()

        engine_def = self.ctx.tts_engine.get_current_engine_definition()
        self.engineSelectCaptionLabel.setText(engine_def.get('description', ''))

        for item in schema_list:
            key = item.get('key')
            self.create_dynamic_option_card(item)
            control = self.dynamic_option_widgets.get(key)
            if control is None:
                continue

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

        model_widget = self.dynamic_option_widgets.get('model')
        if isinstance(model_widget, ComboBox):
            model_widget.currentTextChanged.connect(self._on_model_changed)

    def apply_ui_settings_to_engine(self):
        """读取当前 UI 控件值并写回当前引擎配置"""
        option_values = {}
        for key, control in self.dynamic_option_widgets.items():
            if isinstance(control, SpinBox):
                option_values[key] = control.value()
            elif isinstance(control, DoubleSpinBox):
                option_values[key] = control.value()
            elif isinstance(control, ComboBox):
                option_values[key] = control.currentText()
            elif isinstance(control, LineEdit):
                option_values[key] = control.text()

        self.ctx.tts_engine.apply_current_options(option_values)
        if self._voice_combo is not None:
            self.ctx.tts_engine.set_voice(self._voice_combo.currentIndex())

    def setup_voices_list(self):
        """按当前引擎重建发音人选择卡片"""
        if self._voice_card is not None:
            self.verticalLayout_2.removeWidget(self._voice_card)
            self._voice_card.deleteLater()
            self._voice_card = None
            self._voice_combo = None

        voices_list = self.ctx.tts_engine.get_voices_list()
        if not voices_list:
            return

        card = CardWidget(self.importWidget)
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 20, 20, 20)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        title_lbl = QLabel(card)
        title_lbl.setText('发音人选择')
        title_lbl.setStyleSheet("font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")
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
        h_layout.addWidget(combo)

        insert_idx = self.verticalLayout_2.indexOf(self.CardWidget_4)
        self.verticalLayout_2.insertWidget(insert_idx, card)

        saved_index = self.ctx.tts_engine.get_selected_voice_index()
        if 0 <= saved_index < combo.count():
            combo.setCurrentIndex(saved_index)

        self._voice_card = card
        self._voice_combo = combo

    def change_tts_engine(self):
        """切换引擎后重绘可调节项"""
        self.ctx.tts_engine.set_mode(self.engineSelectComboBox.currentIndex())
        self.apply_engine_schema_to_ui()
        self.setup_voices_list()

    def _on_model_changed(self, model_name: str):
        """百炼模型切换时同步引擎配置并刷新音色列表"""
        self.ctx.tts_engine.set_current_option('model', model_name)
        self.setup_voices_list()

    def load_persistent_settings(self):
        """从磁盘读取配置并恢复到 TTS 管理器"""
        if not self.settings_file_path.exists():
            return False
        try:
            with self.settings_file_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            ok = self.ctx.tts_engine.import_persistent_state(data)
            print(f'[设置] 已加载持久化配置：{self.settings_file_path}')
            return ok
        except Exception as e:
            print(f'[设置] 读取持久化配置失败：{e}')
            return False

    def save_persistent_settings(self):
        """将当前配置写入磁盘"""
        data = self.ctx.tts_engine.export_persistent_state()
        with self.settings_file_path.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'[设置] 已保存持久化配置：{self.settings_file_path}')

    def save_setting(self):
        try:
            self.apply_ui_settings_to_engine()
            self.save_persistent_settings()
        except Exception as e:
            print(e)
            self.create_error_info_bar('保存设置错误', f'详情：{e}')
        else:
            self.create_success_info_bar('保存设置成功', '重新导入文件后生效。')

    def get_update(self):
        """获取版本更新"""
        self.versionPrimaryPushButton.setEnabled(False)
        self.update_thread.start()

    def thread_get_update_finish(self, data_list):
        """更新检查完成回调"""
        self.versionPrimaryPushButton.setEnabled(True)
        if data_list[0] == 0:
            self.create_success_info_bar(data_list[1], data_list[2])
        elif data_list[0] == 1:
            self.show_update_dialog(data_list[1], data_list[2], data_list[3])
        else:
            self.create_error_info_bar(data_list[1], data_list[2])

    @staticmethod
    def open_github_url():
        """打开 GitHub 主页"""
        webbrowser.open('https://github.com/pth2000')

    @staticmethod
    def open_gitee_url():
        """打开 Gitee 主页"""
        webbrowser.open('https://gitee.com/pth2000')

    @staticmethod
    def open_update_url(url):
        """打开更新下载地址"""
        webbrowser.open(url)

    def create_success_info_bar(self, title, text):
        """成功消息框"""
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
        """警告消息框"""
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
        """错误消息框"""
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
        """显示更新弹窗"""
        dialog = MessageBox(title, content, self)
        dialog.yesButton.setText('获取更新')
        dialog.cancelButton.setText('取消')
        if dialog.exec():
            self.open_update_url(url)
