"""确认历史记录及其独占音频的删除范围。"""

from qfluentwidgets import BodyLabel, CheckBox, MessageBoxBase, SubtitleLabel


class DeleteRecordMessageBox(MessageBoxBase):
    """确认删除记录，并可选清理只被目标记录引用的音频。

    共享音频不计入统计，也不会被删除，因此不会破坏保留的其它记录。
    """

    def __init__(self, target_text: str, audio_count: int, audio_size: str, parent=None):
        super().__init__(parent)

        self.titleLabel = SubtitleLabel('删除历史记录', self)
        self.infoLabel = BodyLabel(f'确定删除{target_text}吗？此操作不可恢复。', self)
        self.infoLabel.setWordWrap(True)

        self.audioCheckBox = CheckBox(self)
        self.audioCheckBox.setMinimumHeight(24)
        if audio_count > 0:
            self.audioCheckBox.setText(f'同时删除独占的音频缓存（{audio_count} 条 / {audio_size}）')
            self.audioCheckBox.setChecked(True)
        else:
            self.audioCheckBox.setText('无可一并删除的音频：相关音频仍被其它记录引用')
            self.audioCheckBox.setEnabled(False)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.infoLabel)
        self.viewLayout.addWidget(self.audioCheckBox)

        self.yesButton.setText('删除')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(420)

    def should_delete_audio(self) -> bool:
        """返回用户是否选择同时删除独占音频。"""
        return self.audioCheckBox.isEnabled() and self.audioCheckBox.isChecked()
