"""编辑分隔符弹窗"""

from PySide6.QtCore import QUrl
from qfluentwidgets import LineEdit, MessageBoxBase, SubtitleLabel


class EditMarkMessageBox(MessageBoxBase):
    """编辑分隔符界面"""

    def __init__(self, current_mark: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel('编辑分隔符', self)
        self.urlLineEdit = LineEdit(self)

        self.urlLineEdit.setPlaceholderText('请输入您的讲稿分隔符，如“●”')
        self.urlLineEdit.setClearButtonEnabled(True)
        self.urlLineEdit.setText(current_mark)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.urlLineEdit)

        self.yesButton.setText('保存')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(350)

        if not self.urlLineEdit.text():
            self.yesButton.setDisabled(True)
        self.urlLineEdit.textChanged.connect(self._validate_url)

    def _validate_url(self, text):
        """保留原有启用逻辑，避免行为变化"""
        self.yesButton.setEnabled(QUrl(text).isValid())
