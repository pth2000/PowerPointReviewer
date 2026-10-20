"""编辑并校验页内点击分隔符。"""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, LineEdit, MessageBoxBase,
    StrongBodyLabel, SubtitleLabel, TransparentPushButton, isDarkTheme, themeColor,
)


class _ExampleCard(CardWidget):
    def _normalBackgroundColor(self):
        return QColor('#303030' if isDarkTheme() else '#F7F7F7')


class EditMarkMessageBox(MessageBoxBase):
    """编辑分隔符，并阻止空白值破坏讲稿切分。"""

    def __init__(self, current_mark: str, parent=None, *, has_script=False):
        super().__init__(parent)
        self._current_mark = current_mark
        self._has_script = has_script
        self.viewLayout.setSpacing(12)
        self.titleLabel = SubtitleLabel('编辑分隔符', self)
        self.viewLayout.addWidget(self.titleLabel)

        description = BodyLabel('在同一页备注中，用分隔符标记下一次点击后要朗读的内容。', self)
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)
        editor_row = QHBoxLayout()
        editor_row.setSpacing(8)
        field_label = StrongBodyLabel('分隔符', self)
        field_label.setFixedWidth(76)
        editor_row.addWidget(field_label)
        self.mark_edit = LineEdit(self)
        self.mark_edit.setPlaceholderText('默认：●')
        self.mark_edit.setAccessibleName('页内分隔符')
        self.mark_edit.setClearButtonEnabled(True)
        self.mark_edit.setText(current_mark)
        self.reset_button = TransparentPushButton('恢复默认', self)
        self.reset_button.clicked.connect(lambda: self.mark_edit.setText('●'))
        editor_row.addWidget(self.mark_edit, 1)
        editor_row.addWidget(self.reset_button)
        self.viewLayout.addLayout(editor_row)

        self.example_card = _ExampleCard(self)
        self.example_card.setBorderRadius(10)
        example = QVBoxLayout(self.example_card)
        example.setContentsMargins(16, 12, 16, 12)
        example.setSpacing(8)
        example.addWidget(CaptionLabel('PPT 备注示例', self.example_card))
        self.example_text = BodyLabel(self.example_card)
        self.example_text.setTextFormat(Qt.TextFormat.RichText)
        self.example_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.example_text.setWordWrap(True)
        self.example_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        example.addWidget(self.example_text)
        for number, action in (('第 1 段', '直接朗读'), ('第 2 段', '点击一次后朗读')):
            row = QHBoxLayout()
            part = StrongBodyLabel(number, self.example_card)
            part.setFixedWidth(76)
            row.addWidget(part)
            row.addWidget(BodyLabel(action, self.example_card), 1)
            example.addLayout(row)
        self.viewLayout.addWidget(self.example_card)

        self.hint_label = CaptionLabel(self)
        self.hint_label.setWordWrap(True)
        self.viewLayout.addWidget(self.hint_label)

        self.yesButton.setText('保存')
        self.cancelButton.setText('取消')
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(self.cancelButton)
        self.buttonLayout.addWidget(self.yesButton)
        self.cancelButton.setFixedWidth(96)
        self.yesButton.setFixedWidth(104)
        self.buttonGroup.setFixedHeight(68)
        self.buttonLayout.setContentsMargins(24, 16, 24, 16)
        self.widget.setFixedWidth(min(500, parent.width() - 40) if parent else 500)

        self.mark_edit.textChanged.connect(self._update_example)
        self._update_example()
        self.mark_edit.setFocus()

    def get_mark(self):
        return self.mark_edit.text().strip()

    def validate(self):
        return bool(self.get_mark())

    def _update_example(self, *_):
        mark = self.get_mark()
        self.yesButton.setEnabled(bool(mark))
        display = self.mark_edit.fontMetrics().elidedText(mark, Qt.TextElideMode.ElideMiddle, 120)
        token = escape(display or '分隔符')
        color = themeColor().name()
        first, second = '介绍研究背景。', '说明研究方法。'
        if mark and (mark in first or mark in second):
            first, second = '先讲这一部分', '再讲下一部分'
        self.example_text.setText(
            f'{first} <span style="color:{color}; font-weight:600">{token}</span> {second}')
        self.example_text.setToolTip(f'{first}{mark}{second}' if mark else '')
        if not mark:
            hint = '请输入非空白字符作为分隔符。'
        elif self._has_script and mark != self._current_mark:
            hint = '保存后，将按新分隔符重新生成当前讲稿的配音。'
        else:
            hint = '分隔符不会被朗读。没有点击动画时，不需要插入分隔符。'
        self.hint_label.setText(hint)
