"""以页码摘要确认 PPT 讲稿更新。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, InfoBadge,
    MessageBoxBase, StrongBodyLabel, SubtitleLabel, ToolTipFilter, isDarkTheme,
    setCustomStyleSheet,
)

from ui import theme
from app.presentation.monitor import changed_pages


def page_ranges(pages):
    """连续页码合并为范围，摘要不随页数无限增长。"""
    ranges = []
    for page in sorted(pages):
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    return '、'.join(str(start) if start == end else f'{start}–{end}'
                    for start, end in ranges)


class _ElidedCaption(CaptionLabel):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full_text = str(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextColor('#686868', '#b9b9b9')
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.installEventFilter(ToolTipFilter(self))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideMiddle, self.width()))
        self.setToolTip(self.full_text if self.text() != self.full_text else '')


class _SummaryCard(CardWidget):
    def _normalBackgroundColor(self):
        return QColor('#303030' if isDarkTheme() else '#f7f7f7')


class PptUpdateDialog(MessageBoxBase):
    def __init__(self, name, before, after, edited, voice, parent=None):
        super().__init__(parent)
        pages = changed_pages(before, after)
        self.viewLayout.setSpacing(16)
        title_row, self.help_icon = theme.make_title_row(
            SubtitleLabel('更新 PPT 讲稿', self),
            '更新后按当前语音设置生成配音。\n'
            'PPT 备注会替换当前讲稿中的编辑或 AI 改写内容，生成成功后更新当前历史记录。')
        if edited:
            badge = InfoBadge.warning('已编辑讲稿', self)
            badge.setCustomBackgroundColor('#fff3db', '#44381f')
            setCustomStyleSheet(badge, 'InfoBadge { color: #86590b; }',
                               'InfoBadge { color: #efcd8e; }')
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            badge.setToolTip('更新将替换当前讲稿中的编辑或改写内容，生成成功后更新当前历史记录。')
            badge.installEventFilter(ToolTipFilter(badge))
            title_row.addWidget(badge)
        self.viewLayout.addLayout(title_row)

        self.summary_card = _SummaryCard(self)
        self.summary_card.setBorderRadius(8)
        summary = QVBoxLayout(self.summary_card)
        summary.setContentsMargins(16, 14, 16, 14)
        summary.setSpacing(12)
        self.summary_label = StrongBodyLabel(f'{len(pages)} 页讲稿有变化', self.summary_card)
        summary.addWidget(self.summary_label)
        self.page_labels = []
        for status, selected in (
            ('修改', [page for page in pages if page in before and page in after]),
            ('新增', [page for page in pages if page not in before]),
            ('删除', [page for page in pages if page not in after]),
        ):
            if not selected:
                continue
            row = QHBoxLayout()
            row.setSpacing(12)
            category = StrongBodyLabel(status, self.summary_card)
            category.setFixedWidth(40)
            row.addWidget(category)
            full_text = f'第 {page_ranges(selected)} 页'
            text = full_text
            if len(full_text) > 48:
                shortened = page_ranges(selected).split('、')
                while len('、'.join(shortened)) > 40:
                    shortened.pop()
                text = f'第 {"、".join(shortened)}… 页'
            label = BodyLabel(text, self.summary_card)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setToolTip(full_text if text != full_text else '')
            label.installEventFilter(ToolTipFilter(label))
            row.addWidget(label, 1)
            count = CaptionLabel(f'{len(selected)} 页', self.summary_card)
            count.setTextColor('#686868', '#b9b9b9')
            row.addWidget(count, 0, Qt.AlignmentFlag.AlignTop)
            self.page_labels.append(label)
            summary.addLayout(row)
        self.viewLayout.addWidget(self.summary_card)

        self.voice_label = _ElidedCaption(f'配音 · {voice or "当前发音人"}', self)
        self.viewLayout.addWidget(self.voice_label)
        self.yesButton.setText('更新并生成')
        self.cancelButton.setText('暂不更新')
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(self.cancelButton)
        self.buttonLayout.addWidget(self.yesButton)
        self.cancelButton.setFixedWidth(104)
        self.yesButton.setFixedWidth(132)
        self.buttonGroup.setFixedHeight(68)
        self.buttonLayout.setContentsMargins(24, 16, 24, 16)
        self.widget.setMinimumWidth(460)
        self.widget.setMaximumWidth(520)
