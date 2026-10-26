"""编辑并测试 AI 改写使用的接口参数。"""

from PySide6.QtCore import QSize, QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QGridLayout, QLineEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    EditableComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
    SpinBox,
    SubtitleLabel,
)

from tasks.rewrite_task import LLMModelsTask, LLMTestTask
from ui.dialogs.base import ThemedDialog


class LLMConfigMessageBox(MessageBoxBase):
    """编辑 OpenAI 兼容接口参数，并在后台执行连通性测试。

    字段先在弹窗内暂存，只有确认后才写回应用偏好。
    """

    def __init__(self, app_settings, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.test_thread = None
        self.models_thread = None
        self._pending_result = None

        self.titleLabel = SubtitleLabel('接口配置', self)
        self.hintLabel = CaptionLabel(
            '支持 OpenAI 兼容接口，可获取模型列表或手动填写。', self)
        self.hintLabel.setWordWrap(True)

        self.base_url_edit = LineEdit(self)
        self.base_url_edit.setPlaceholderText('https://api.openai.com/v1')
        self.base_url_edit.setText(str(app_settings.get('llm_base_url') or ''))

        self.api_key_edit = LineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText('sk-…（本地服务可留空）')
        self.api_key_edit.setText(str(app_settings.get('llm_api_key') or ''))

        self.model_edit = EditableComboBox(self)
        self.model_edit.setPlaceholderText('选择或输入模型名称')
        self.model_edit.setText(str(app_settings.get('llm_model') or ''))
        self.model_names = QStringListModel(self)
        completer = QCompleter(self.model_names, self.model_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.model_edit.setCompleter(completer)
        self.base_url_edit.textChanged.connect(self._clear_models)
        self.api_key_edit.textChanged.connect(self._clear_models)

        self.timeout_spin = SpinBox(self)
        self.timeout_spin.setRange(30, 600)
        self.timeout_spin.setValue(int(app_settings.get('llm_timeout') or 120))

        self.test_button = PushButton('测试连接', self)
        self.test_button.setMinimumSize(QSize(110, 33))
        self.test_button.clicked.connect(self.test_connection)
        self.models_button = PushButton('获取模型', self)
        self.models_button.setMinimumSize(QSize(110, 33))
        self.models_button.clicked.connect(self.fetch_models)

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
        grid.addWidget(self.models_button, 2, 2)
        grid.addWidget(BodyLabel('请求超时（秒）', form), 3, 0)
        grid.addWidget(self.timeout_spin, 3, 1)
        grid.addWidget(self.test_button, 3, 2)
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
        if self._busy() or self._pending_result is not None:
            return
        config = self.current_config()
        if not config['base_url'] or not config['model']:
            self._info('warning', '配置不完整', '填写服务地址与模型名称')
            return

        self.test_button.setEnabled(False)
        self.test_button.setText('测试中…')

        self.test_thread = LLMTestTask(config, self)
        self.yesButton.setEnabled(False)
        self.models_button.setEnabled(False)
        self.test_thread.signal_finish.connect(self.on_test_finish)
        self.test_thread.finished.connect(lambda task=self.test_thread: self._worker_finished(task))
        self.test_thread.finished.connect(self.test_thread.deleteLater)
        self.test_thread.start()

    def on_test_finish(self, ok: bool, message: str):
        """展示当前配置的模型回复或错误。"""
        if self._pending_result is not None:
            return
        if self.test_thread is not None and self.current_config() != self.test_thread.llm_config:
            return
        if ok:
            self._info('success', '连接正常', f'模型回复：{message}')
        else:
            self._show_failure('连接失败', message)

    def _busy(self):
        return self.test_thread is not None or self.models_thread is not None

    def _clear_models(self, *_):
        text = self.model_edit.text()
        self.model_edit.clear()
        self.model_edit.setText(text)
        self.model_names.setStringList([])

    def fetch_models(self):
        if self._busy() or self._pending_result is not None:
            return
        config = self.current_config()
        if not config['base_url']:
            self._info('warning', '请填写服务地址', '填写后可获取模型列表。')
            return
        self.models_thread = LLMModelsTask(config, self)
        self.models_button.setText('获取中…')
        self.models_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.yesButton.setEnabled(False)
        self.models_thread.signal_finish.connect(self.on_models_finish)
        self.models_thread.finished.connect(lambda task=self.models_thread: self._worker_finished(task))
        self.models_thread.finished.connect(self.models_thread.deleteLater)
        self.models_thread.start()

    def on_models_finish(self, ok, result):
        if self._pending_result is not None:
            return
        task = self.models_thread
        if task is not None and any(self.current_config()[key] != task.llm_config.get(key, '')
                                    for key in ('base_url', 'api_key')):
            return
        if not ok:
            self._show_failure('获取模型失败', str(result))
            return
        text = self.model_edit.text()
        self.model_edit.clear()
        self.model_edit.addItems(result)
        self.model_edit.setText(text)
        self.model_names.setStringList(result)
        if result:
            self._info('success', '模型列表已更新', f'共 {len(result)} 个模型，可选择或输入名称。')
        else:
            self._info('warning', '模型列表为空', '可手动填写模型名称，或检查账号的模型权限。')

    def _worker_finished(self, task):
        if task is self.test_thread:
            self.test_thread = None
        if task is self.models_thread:
            self.models_thread = None
        if self._pending_result is not None and not self._busy():
            super().done(self._pending_result)
            return
        self.test_button.setEnabled(True)
        self.test_button.setText('测试连接')
        self.models_button.setEnabled(True)
        self.models_button.setText('获取模型')
        self.yesButton.setEnabled(True)

    def done(self, code):
        if self._busy():
            self._pending_result = code
            self.hintLabel.setText('正在结束请求，请稍候…')
            self.cancelButton.setEnabled(False)
            for task in (self.test_thread, self.models_thread):
                if task is not None:
                    task.requestInterruption()
            return
        super().done(code)

    def _show_failure(self, title, message):
        bar = self._info('error', title, message.split('\n\n', 1)[0])
        if '\n\n' in message:
            button = PushButton('查看详情', bar)
            button.clicked.connect(lambda: self._show_error_details(message))
            bar.addWidget(button)

    def _show_error_details(self, message: str):
        dialog = ThemedDialog(self)
        dialog.setWindowTitle('错误详情')
        dialog.resize(680, 440)
        layout = QVBoxLayout(dialog)
        detail = PlainTextEdit(dialog)
        detail.setReadOnly(True)
        detail.setPlainText(message)
        layout.addWidget(detail)
        close_button = PushButton('关闭', dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
        dialog.deleteLater()

    def _info(self, level: str, title: str, text: str):
        if level == 'error':
            from app.logging_setup import log_error
            log_error(title, text)
        factory = {'success': InfoBar.success, 'warning': InfoBar.warning, 'error': InfoBar.error}[level]
        return factory(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=-1 if level == 'error' else 4000,
                parent=self)
