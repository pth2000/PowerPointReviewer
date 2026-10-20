"""复制优化任务、保存文件和导入候选结果的窗口。"""

from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, PlainTextEdit, PrimaryPushButton, PushButton, SubtitleLabel

from ui.dialogs.base import ThemedDialog


class RewriteExchangeDialog(ThemedDialog):
    """文件读写与剪贴板只由用户点击触发，导入需先校验再关闭。"""

    def __init__(self, parent=None, *, task_text='', validator=None):
        super().__init__(parent)
        self.validator = validator
        self.imported = {}
        exporting = validator is None
        title = '导出优化任务' if exporting else '导入优化结果'
        self.setWindowTitle(title)
        self.resize(720, 540)
        self.setMinimumSize(560, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel(title, self))
        hint = BodyLabel(
            '任务包含所选页面与处理要求。将完整内容提交给 AI，获得结果后返回导入。' if exporting
            else '粘贴完整的 AI 返回内容，或从文件读取。导入后可逐页对照，确认后再应用。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = PlainTextEdit(self)
        self.editor.setPlainText(task_text)
        self.editor.setReadOnly(exporting)
        if not exporting:
            self.editor.setPlaceholderText('在此粘贴完整结果…')
        layout.addWidget(self.editor, 1)
        self.message = CaptionLabel(self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        self.file_button = PushButton('保存文件' if exporting else '从文件读取', self)
        self.file_button.clicked.connect(self.save_file if exporting else self.read_file)
        actions.addWidget(self.file_button)
        actions.addStretch()
        close = PushButton('关闭' if exporting else '取消', self)
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        self.primary_button = PrimaryPushButton('复制任务' if exporting else '导入并审查', self)
        self.primary_button.clicked.connect(self.copy_task if exporting else self.import_result)
        actions.addWidget(self.primary_button)
        layout.addLayout(actions)

    def copy_task(self):
        QApplication.clipboard().setText(self.editor.toPlainText())
        self.message.setText('任务内容已复制。')

    def save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存优化任务', '讲稿优化任务.txt', '文本文件 (*.txt)')
        if not path:
            return
        try:
            Path(path).write_text(self.editor.toPlainText(), encoding='utf-8')
        except OSError as exc:
            self.message.setText(f'保存失败：{exc}')
            return
        self.message.setText('任务文件已保存。')

    def read_file(self):
        path, _ = QFileDialog.getOpenFileName(self, '读取优化结果', '', '优化结果 (*.json *.txt);;所有文件 (*)')
        if not path:
            return
        try:
            content = Path(path).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as exc:
            self.message.setText(f'文件读取失败：{exc}')
            return
        self.editor.setPlainText(content)
        self.message.clear()

    def import_result(self):
        try:
            self.imported = self.validator(self.editor.toPlainText())
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.accept()
