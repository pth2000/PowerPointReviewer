"""编辑并校验页内点击分隔符。"""

from qfluentwidgets import LineEdit, MessageBoxBase, SubtitleLabel


class EditMarkMessageBox(MessageBoxBase):
    """编辑分隔符，并阻止空白值破坏讲稿切分。"""

    def __init__(self, current_mark: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel('编辑分隔符', self)
        self.urlLineEdit = LineEdit(self)

        self.urlLineEdit.setPlaceholderText('请输入页内分隔符，默认为一个实心圆点')
        self.urlLineEdit.setClearButtonEnabled(True)
        self.urlLineEdit.setText(current_mark)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.urlLineEdit)

        self.yesButton.setText('保存')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(350)

        self.urlLineEdit.textChanged.connect(self._validate_mark)
        self._validate_mark(self.urlLineEdit.text())

    def _validate_mark(self, text: str):
        """仅允许非空白分隔符，避免 ``str.split`` 异常或按空格误切讲稿。"""
        self.yesButton.setEnabled(bool(text.strip()))
