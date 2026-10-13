"""已有独立讲稿时，选择关联 PPT 或读取其备注。"""

from PySide6.QtWidgets import QButtonGroup, QVBoxLayout
from qfluentwidgets import BodyLabel, CardWidget, MessageBoxBase, RadioButton, SubtitleLabel


class PptImportDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewLayout.setSpacing(16)
        self.viewLayout.addWidget(SubtitleLabel('如何使用此 PPT？', self))
        self.link_radio = self._choice(
            '关联 PPT，保留当前讲稿', '保留已有配音，用此 PPT 同步放映和导出视频。')
        self.replace_radio = self._choice(
            '读取 PPT 备注作为新讲稿', '替换当前讲稿，并按当前语音设置生成配音。')
        # 两个按钮处于不同卡片中，显式分组保证互斥。
        self.choices = QButtonGroup(self)
        self.choices.addButton(self.link_radio)
        self.choices.addButton(self.replace_radio)
        self.link_radio.setChecked(True)
        self.link_radio.toggled.connect(self._refresh_action)
        self._refresh_action()
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(460)
        self.widget.setMaximumWidth(520)

    def _choice(self, title, description):
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        radio = RadioButton(title, card)
        layout.addWidget(radio)
        caption = BodyLabel(description, card)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.viewLayout.addWidget(card)
        return radio

    def _refresh_action(self):
        self.yesButton.setText('关联 PPT' if self.link_radio.isChecked() else '导入 PPT 备注')

    def selected_action(self):
        return 'link' if self.link_radio.isChecked() else 'replace'
