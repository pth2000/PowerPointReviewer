"""离开设置时，保留保存、放弃与继续编辑三种选择。"""

from qfluentwidgets import BodyLabel, MessageBoxBase, PushButton, SubtitleLabel


class UnsavedSettingsDialog(MessageBoxBase):
    def __init__(self, parent):
        super().__init__(parent)
        self.action = 'save'
        self.viewLayout.addWidget(SubtitleLabel('未保存的设置更改', self))
        text = BodyLabel('保存当前设置，或返回继续编辑。', self)
        text.setWordWrap(True)
        self.viewLayout.addWidget(text)
        self.yesButton.setText('保存')
        self.cancelButton.setText('继续编辑')
        self.discard_button = PushButton('放弃更改', self.buttonGroup)
        self.discard_button.clicked.connect(self._discard)
        self.buttonLayout.insertWidget(1, self.discard_button, 1)
        self.widget.setMinimumWidth(460)

    def _discard(self):
        self.action = 'discard'
        self.accept()
