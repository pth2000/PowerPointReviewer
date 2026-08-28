"""编辑并测试 AI 改写使用的接口参数。"""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QGridLayout, QLineEdit, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SpinBox,
    SubtitleLabel,
)
from PySide6.QtCore import Qt

from tasks.rewrite_task import LLMTestTask


class LLMConfigMessageBox(MessageBoxBase):
    """编辑 OpenAI 兼容接口参数，并在后台执行连通性测试。

    字段先在弹窗内暂存，只有确认后才写回应用偏好。
    """

    def __init__(self, app_settings, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.test_thread = None

        self.titleLabel = SubtitleLabel('接口配置', self)
        self.hintLabel = CaptionLabel(
            '适用于 OpenAI 兼容接口，例如 OpenAI、DeepSeek或本地 Ollama 等', self)
        self.hintLabel.setWordWrap(True)

        self.base_url_edit = LineEdit(self)
        self.base_url_edit.setPlaceholderText('https://api.openai.com/v1')
        self.base_url_edit.setText(str(app_settings.get('llm_base_url') or ''))

        self.api_key_edit = LineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText('sk-...，本地部署的服务可留空')
        self.api_key_edit.setText(str(app_settings.get('llm_api_key') or ''))

        self.model_edit = LineEdit(self)
        self.model_edit.setPlaceholderText('gpt-4o-mini')
        self.model_edit.setText(str(app_settings.get('llm_model') or ''))

        self.timeout_spin = SpinBox(self)
        self.timeout_spin.setRange(30, 600)
        self.timeout_spin.setValue(int(app_settings.get('llm_timeout') or 120))

        self.test_button = PushButton('测试连接', self)
        self.test_button.setMinimumSize(QSize(110, 33))
        self.test_button.clicked.connect(self.test_connection)

        form = QWidget(self)
        grid = QGridLayout(form)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(BodyLabel('服务地址', form), 0, 0)
        grid.addWidget(self.base_url_edit, 0, 1, 1, 2)
        grid.addWidget(BodyLabel('API Key', form), 1, 0)
        grid.addWidget(self.api_key_edit, 1, 1, 1, 2)
        grid.addWidget(BodyLabel('模型', form), 2, 0)
        grid.addWidget(self.model_edit, 2, 1)
        grid.addWidget(self.test_button, 2, 2)
        grid.addWidget(BodyLabel('请求超时(秒)', form), 3, 0)
        grid.addWidget(self.timeout_spin, 3, 1)
        grid.setColumnStretch(1, 1)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.hintLabel)
        self.viewLayout.addWidget(form)

        self.yesButton.setText('确定')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(560)

    def current_config(self) -> dict:
        """返回弹窗当前值组成的客户端配置。"""
        return {
            'base_url': self.base_url_edit.text().strip(),
            'api_key': self.api_key_edit.text(),
            'model': self.model_edit.text().strip(),
            'timeout': int(self.timeout_spin.value()),
        }

    def apply_to_settings(self):
        """将当前连接参数写入应用偏好。"""
        config = self.current_config()
        self.app_settings.set('llm_base_url', config['base_url'])
        self.app_settings.set('llm_api_key', config['api_key'])
        self.app_settings.set('llm_model', config['model'])
        self.app_settings.set('llm_timeout', config['timeout'])

    def test_connection(self):
        """校验必填项并启动后台连通性测试。"""
        config = self.current_config()
        if not config['base_url'] or not config['model']:
            self._info('warning', '配置不完整', '请先填写服务地址与模型名称。')
            return

        self.test_button.setEnabled(False)
        self.test_button.setText('测试中...')

        self.test_thread = LLMTestTask(config, self)
        self.test_thread.signal_finish.connect(self.on_test_finish)
        self.test_thread.finished.connect(self.test_thread.deleteLater)
        self.test_thread.start()

    def on_test_finish(self, ok: bool, message: str):
        """恢复测试按钮，并展示模型回复或错误。"""
        self.test_thread = None
        self.test_button.setEnabled(True)
        self.test_button.setText('测试连接')
        if ok:
            self._info('success', '连接正常', f'模型回复：{message}')
        else:
            self._info('error', '连接失败', f'详情：{message}')

    def _info(self, level: str, title: str, text: str):
        factory = {'success': InfoBar.success, 'warning': InfoBar.warning, 'error': InfoBar.error}[level]
        factory(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=-1 if level == 'error' else 4000,
                parent=self)
