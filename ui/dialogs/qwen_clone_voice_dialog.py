"""千问复刻音色管理对话框"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)


class QwenCloneVoiceDialog(QDialog):
    """集中管理千问复刻音色：选择参考音频、创建、刷新、删除、选择当前音色。"""

    def __init__(self, tts_engine, parent=None):
        super().__init__(parent)
        self.tts_engine = tts_engine
        self.selected_voice = ''

        self.setWindowTitle('复刻音色管理')
        self.setMinimumSize(860, 600)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        self.tip_label = BodyLabel('说明：创建音色后会保存在云端，后续直接复用，无需重复上传参考音频。', self)
        main_layout.addWidget(self.tip_label)

        # 参考音频区域
        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel('参考音频', self))
        self.reference_audio_edit = LineEdit(self)
        self.reference_audio_edit.setPlaceholderText('请选择 .mp3/.wav/.m4a 音频文件')
        ref_row.addWidget(self.reference_audio_edit, 1)
        self.choose_audio_button = PushButton('选择文件', self)
        self.choose_audio_button.clicked.connect(self.choose_reference_audio)
        ref_row.addWidget(self.choose_audio_button)
        main_layout.addLayout(ref_row)

        # 参数区域
        param_row = QHBoxLayout()
        param_row.addWidget(QLabel('音色名称', self))
        self.preferred_name_edit = LineEdit(self)
        self.preferred_name_edit.setPlaceholderText('建议字母/数字/下划线，最长16字符')
        param_row.addWidget(self.preferred_name_edit)

        param_row.addWidget(QLabel('参考音频MIME', self))
        self.mime_combo = ComboBox(self)
        self.mime_combo.addItems(['audio/mpeg', 'audio/wav', 'audio/mp4'])
        param_row.addWidget(self.mime_combo)
        main_layout.addLayout(param_row)

        # 列表区域
        self.table = TableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['音色', '模型', '创建时间'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        # 列宽策略：音色名拉伸，其它列按内容宽度，避免被截断且保留可读性
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.table.itemDoubleClicked.connect(self.use_selected_voice_and_close)
        main_layout.addWidget(self.table, 1)

        # 操作按钮
        action_row = QHBoxLayout()
        self.refresh_button = PushButton('刷新列表', self)
        self.refresh_button.clicked.connect(self.refresh_list)
        action_row.addWidget(self.refresh_button)

        self.create_button = PrimaryPushButton('创建音色', self)
        self.create_button.clicked.connect(self.create_voice)
        action_row.addWidget(self.create_button)

        self.delete_button = PushButton('删除选中音色', self)
        self.delete_button.clicked.connect(self.delete_selected_voice)
        action_row.addWidget(self.delete_button)

        action_row.addStretch(1)

        self.use_button = PrimaryPushButton('设为当前音色', self)
        self.use_button.clicked.connect(self.use_selected_voice)
        action_row.addWidget(self.use_button)

        self.cancel_button = PushButton('关闭', self)
        self.cancel_button.clicked.connect(self.reject)
        action_row.addWidget(self.cancel_button)

        main_layout.addLayout(action_row)

        self._load_current_settings()
        self.refresh_list(select_voice=self.selected_voice)

    def _load_current_settings(self):
        settings = self.tts_engine.get_current_option_values()
        self.reference_audio_edit.setText(str(settings.get('reference_audio_path', '')))
        self.preferred_name_edit.setText(str(settings.get('preferred_name', 'ppt_reviewer')))

        mime = str(settings.get('audio_mime_type', 'audio/mpeg'))
        mime_index = self.mime_combo.findText(mime)
        if mime_index >= 0:
            self.mime_combo.setCurrentIndex(mime_index)

        self.selected_voice = str(settings.get('voice', '')).strip()

    @staticmethod
    def _infer_mime(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == '.mp3':
            return 'audio/mpeg'
        if suffix == '.wav':
            return 'audio/wav'
        if suffix == '.m4a':
            return 'audio/mp4'
        return ''

    def choose_reference_audio(self):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            '选择参考音频',
            '',
            'Audio Files (*.mp3 *.wav *.m4a)'
        )
        if not selected:
            return

        self.reference_audio_edit.setText(selected)

        inferred = self._infer_mime(selected)
        if inferred:
            idx = self.mime_combo.findText(inferred)
            if idx >= 0:
                self.mime_combo.setCurrentIndex(idx)
            self._info_success('已识别音频格式', f'MIME 已自动设置为 {inferred}')
        else:
            self._info_warning('无法自动识别', '请手动确认 MIME 类型')

    def _current_row_voice(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ''
        item = self.table.item(row, 0)
        if item is None:
            return ''
        return str(item.data(Qt.ItemDataRole.UserRole) or '').strip()

    def refresh_list(self, select_voice: str = ''):
        self.table.setRowCount(0)

        try:
            voice_items = self.tts_engine.list_qwen_clone_voice_items()
        except Exception as e:
            self._info_error('刷新失败', str(e))
            return

        for info in voice_items:
            row = self.table.rowCount()
            self.table.insertRow(row)

            voice = str(info.get('voice', '')).strip()
            model = str(info.get('target_model', '')).strip()
            created = str(info.get('gmt_create', '')).strip()

            col_values = [voice, model, created]
            for col, val in enumerate(col_values):
                cell = QTableWidgetItem(val)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, voice)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, cell)

        if self.table.rowCount() == 0:
            self._info_warning('列表为空', '当前账号下没有可用复刻音色')
            return

        target = (select_voice or self.selected_voice or '').strip()
        if target:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item and str(item.text()).strip() == target:
                    self.table.selectRow(row)
                    return

        self.table.selectRow(0)

    def create_voice(self):
        reference_path = self.reference_audio_edit.text().strip()
        preferred_name = self.preferred_name_edit.text().strip() or 'ppt_reviewer'
        mime = self.mime_combo.currentText().strip() or 'audio/mpeg'

        if not reference_path:
            self._info_warning('缺少参考音频', '请先选择参考音频文件')
            return

        if not Path(reference_path).exists():
            self._info_warning('文件不存在', '请选择有效的参考音频文件')
            return

        # 先把表单写回配置，保证后续保存设置时一致
        self.tts_engine.set_current_option('reference_audio_path', reference_path)
        self.tts_engine.set_current_option('preferred_name', preferred_name)
        self.tts_engine.set_current_option('audio_mime_type', mime)

        try:
            voice = self.tts_engine.create_qwen_clone_voice(reference_path, preferred_name=preferred_name)
        except Exception as e:
            self._info_error('创建失败', str(e))
            return

        self.selected_voice = voice
        self.tts_engine.set_current_option('voice', voice)
        self.refresh_list(select_voice=voice)
        self._info_success('创建成功', f'已创建音色：{voice}')

    def delete_selected_voice(self):
        voice = self._current_row_voice()
        if not voice:
            self._info_warning('未选择音色', '请先在列表中选中一个音色')
            return

        try:
            self.tts_engine.delete_qwen_clone_voice(voice)
        except Exception as e:
            self._info_error('删除失败', str(e))
            return

        if str(self.tts_engine.get_current_option_values().get('voice', '')).strip() == voice:
            self.tts_engine.set_current_option('voice', '')
            self.selected_voice = ''

        self.refresh_list()

        still_exists = False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text().strip() == voice:
                still_exists = True
                break

        if still_exists:
            self._info_warning('删除已提交', '音色仍在列表中，可能是云端同步延迟，稍后再刷新')
        else:
            self._info_success('删除成功', f'已删除音色：{voice}')

    def use_selected_voice(self):
        voice = self._current_row_voice()
        if not voice:
            self._info_warning('未选择音色', '请先在列表中选中一个音色')
            return

        self.selected_voice = voice
        self.tts_engine.set_current_option('voice', voice)
        self._info_success('已设置当前音色', f'当前音色：{voice}')

    def use_selected_voice_and_close(self, *_):
        self.use_selected_voice()
        if self.selected_voice:
            self.accept()

    def get_selected_voice(self) -> str:
        return self.selected_voice

    def _info_success(self, title: str, text: str):
        InfoBar.success(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2200,
            parent=self,
        )

    def _info_warning(self, title: str, text: str):
        InfoBar.warning(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _info_error(self, title: str, text: str):
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self,
        )
