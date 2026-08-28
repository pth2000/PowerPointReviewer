"""编辑设置页语音试听所使用的文本。"""

from PySide6.QtCore import QSize
from qfluentwidgets import CaptionLabel, MessageBoxBase, PlainTextEdit, SubtitleLabel


class PreviewTextMessageBox(MessageBoxBase):
    """在独立多行编辑器中修改试听文本。

    试听内容可能是完整段落，不适合放进设置卡片的单行控件。
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)

        self.titleLabel = SubtitleLabel('试听文字', self)
        self.hintLabel = CaptionLabel(
            '使用自定义文字，进行音色、语速等配置的微调', self)
        self.hintLabel.setWordWrap(True)

        self.textEdit = PlainTextEdit(self)
        self.textEdit.setMinimumSize(QSize(0, 140))
        self.textEdit.setPlainText(str(text or ''))

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.hintLabel)
        self.viewLayout.addWidget(self.textEdit)

        self.yesButton.setText('确定')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(520)

    def get_text(self) -> str:
        """返回去除首尾空白的试听文本。"""
        return self.textEdit.toPlainText().strip()
