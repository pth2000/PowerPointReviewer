"""为当前讲稿选择对应的 PPT，保留原讲稿与已生成音频。"""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QSizePolicy
from qfluentwidgets import (
    BodyLabel, ComboBox, MessageBox, PrimaryPushButton, PushButton, SubtitleLabel,
)

from app.presentation import link as presentation_link, slideshow
from ui.dialogs.base import ThemedDialog


class PresentationLinkDialog(ThemedDialog):
    def __init__(self, link, pages, parent=None):
        super().__init__(parent)
        self.link, self.pages = dict(link or {}), list(pages)
        self.selected = None
        self.setWindowTitle('关联 PPT')
        self.setMinimumWidth(520)
        layout = self.create_form_layout(520, (24, 24, 24, 24), 16)
        layout.addWidget(SubtitleLabel('关联 PPT', self))
        description = BodyLabel('选择与当前讲稿对应的 PPT，用于同步翻页和视频导出。', self)
        description.setWordWrap(True)
        layout.addWidget(description)
        row = QHBoxLayout()
        self.open_combo = ComboBox(self)
        self.open_combo.setMinimumWidth(0)
        self.open_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.open_combo.setPlaceholderText('尚未找到已打开的文稿')
        self.opened = slideshow.list_open_presentations()
        for item in self.opened:
            self.open_combo.addItem(f'{item["name"]} · {item["provider"]}')
        if self.opened:
            self.open_combo.setCurrentIndex(0)
            self.open_combo.setToolTip(self.opened[0]['path'])
        self.open_combo.currentIndexChanged.connect(
            lambda index: self.open_combo.setToolTip(self.opened[index]['path'])
            if 0 <= index < len(self.opened) else None)
        row.addWidget(self.open_combo, 1)
        self.use_open_button = PushButton('使用此文稿', self)
        self.use_open_button.setEnabled(bool(self.opened))
        self.use_open_button.clicked.connect(self.choose_open)
        row.addWidget(self.use_open_button)
        layout.addLayout(row)
        layout.addStretch(1)
        self.error_label = BodyLabel('', self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch()
        cancel = PushButton('取消', self)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        browse = PrimaryPushButton('选择本地文件', self)
        browse.clicked.connect(self.choose_file)
        row.addWidget(browse)
        layout.addLayout(row)

    def choose_open(self):
        index = self.open_combo.currentIndex()
        if 0 <= index < len(self.opened):
            self.select_path(self.opened[index]['path'])

    def choose_file(self):
        selected, _filter = QFileDialog.getOpenFileName(
            self, '选择对应的 PPT', str(self.link.get('path') or Path.home()),
            'PowerPoint 文稿 (*.pptx *.ppt)')
        if selected:
            self.select_path(selected)

    def select_path(self, path):
        try:
            selected = presentation_link.validate(path, self.pages)
            expected = self.link.get('sha256')
            if expected and selected['sha256'] != expected:
                confirm = MessageBox('确认关联文稿',
                                     '所选文件与之前的 PPT 内容不同。请确认页面及动画与当前讲稿对应。', self)
                confirm.yesButton.setText('使用此文稿')
                confirm.cancelButton.setText('重新选择')
                if not confirm.exec():
                    return
            self.selected = selected
            self.accept()
        except Exception as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
