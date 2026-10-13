"""以概览和逐页明细展示讲稿统计。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QHeaderView, QHBoxLayout, QTableWidgetItem, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CardWidget, MessageBoxBase, StrongBodyLabel,
    SubtitleLabel, TableWidget, isDarkTheme, setCustomStyleSheet, setFont,
)

from app import theme
from app.script_statistics import format_duration


class _OverviewCard(CardWidget):
    def _normalBackgroundColor(self):
        return QColor('#303030' if isDarkTheme() else '#f7f7f7')


def _divider(parent):
    line = QFrame(parent)
    line.setFixedSize(1, 48)
    line.setStyleSheet('background: rgba(128, 128, 128, 0.20); border: none;')
    return line


class ScriptStatisticsDialog(MessageBoxBase):
    def __init__(self, statistics, parent=None):
        super().__init__(parent)
        self.viewLayout.setSpacing(18)
        title, _ = theme.make_title_row(
            SubtitleLabel('信息统计', self),
            '字数按字符计算，包含标点及英文字符，不含空白和分隔符。\n'
            '配音时长包含音频头尾留白。')
        self.viewLayout.addLayout(title)

        self.summary_values = {}
        self.overview_card = _OverviewCard(self)
        self.overview_card.setBorderRadius(8)
        overview = QHBoxLayout(self.overview_card)
        overview.setContentsMargins(18, 16, 18, 16)
        overview.setSpacing(18)
        duration_column = QVBoxLayout()
        duration_column.setSpacing(4)
        duration_column.setAlignment(Qt.AlignmentFlag.AlignTop)
        duration_column.addWidget(BodyLabel('配音总时长', self.overview_card))
        duration = format_duration(statistics.duration)
        duration_label = StrongBodyLabel(duration, self.overview_card)
        duration_label.setWordWrap(True)
        if statistics.duration is None:
            setFont(duration_label, 22)
        else:
            setFont(duration_label, 30 if len(duration) <= 8 else 24, QFont.Weight.DemiBold)
        duration_width = max(120, (min(680, parent.width() - 40) - 157) // 3)
        duration_height = max(duration_label.fontMetrics().height(), duration_label.heightForWidth(duration_width))
        duration_label.setMinimumHeight(duration_height)
        duration_column.addWidget(duration_label)
        self.summary_values['配音总时长'] = duration_label
        overview.addLayout(duration_column, 3)
        overview.addWidget(_divider(self.overview_card))
        for name, value in (
            ('讲稿页数', str(len(statistics.pages))),
            ('讲稿字数', f'{statistics.characters:,}'),
            ('配音段数', str(statistics.segments)),
        ):
            layout = QVBoxLayout()
            layout.setSpacing(4)
            layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            layout.addWidget(BodyLabel(name, self.overview_card))
            label = StrongBodyLabel(value, self.overview_card)
            setFont(label, 24, QFont.Weight.DemiBold)
            layout.addWidget(label)
            self.summary_values[name] = label
            overview.addLayout(layout, 2)
        self.viewLayout.addWidget(self.overview_card)

        details = QVBoxLayout()
        details.setSpacing(8)
        details.addWidget(StrongBodyLabel('逐页明细', self))
        self.table = TableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['页码', '字数', '段数', '配音时长'])
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(TableWidget.SelectionMode.NoSelection)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(36)
        header = self.table.horizontalHeader()
        for column, width in enumerate((142, 130, 110)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(4):
            align = Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignRight
            self.table.horizontalHeaderItem(column).setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        table_style = '''
            TableWidget { border: none; background: transparent; }
            TableWidget::item { padding-left: 14px; padding-right: 14px; }
            QHeaderView::section {
                background: transparent; border: none;
                border-bottom: 1px solid BORDER_COLOR;
                padding-left: 14px; padding-right: 14px;
            }
        '''
        setCustomStyleSheet(self.table,
                            table_style.replace('BORDER_COLOR', 'rgba(0, 0, 0, 0.08)'),
                            table_style.replace('BORDER_COLOR', 'rgba(255, 255, 255, 0.10)'))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setRowCount(len(statistics.pages))
        for row, data in enumerate(statistics.pages):
            for column, text in enumerate((f'第 {data.page} 页', f'{data.characters:,}',
                                           str(data.segments), format_duration(data.duration))):
                item = QTableWidgetItem(text)
                align = Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignRight
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setMinimumHeight(96)
        available = parent.height() - (410 if statistics.longest is not None else 350)
        available -= max(0, duration_height - duration_label.fontMetrics().height())
        if statistics.longest is not None and len(format_duration(statistics.longest.duration)) > 15:
            available -= 28
        self.table.setFixedHeight(min(260, max(96, available), 38 + 36 * max(1, len(statistics.pages))))
        details.addWidget(self.table)
        self.viewLayout.addLayout(details)

        self.extreme_labels = []
        if statistics.longest is not None:
            row = QHBoxLayout()
            row.setSpacing(24)
            for name, data in (('最长段', statistics.longest), ('最短段', statistics.shortest)):
                column = QVBoxLayout()
                column.setSpacing(4)
                heading = QHBoxLayout()
                heading.addWidget(StrongBodyLabel(name, self))
                heading.addStretch(1)
                value = StrongBodyLabel(format_duration(data.duration), self)
                setFont(value, 20, QFont.Weight.DemiBold)
                value.setWordWrap(True)
                heading.addWidget(value)
                column.addLayout(heading)
                label = BodyLabel(f'第 {data.page} 页 · 第 {data.number} 段', self)
                self.extreme_labels.append(label)
                column.addWidget(label)
                if row.count():
                    row.addWidget(_divider(self))
                row.addLayout(column, 1)
            self.viewLayout.addLayout(row)

        self.yesButton.setText('关闭')
        self.cancelButton.hide()
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(self.yesButton)
        self.yesButton.setFixedWidth(100)
        self.buttonGroup.setFixedHeight(68)
        self.buttonLayout.setContentsMargins(24, 16, 24, 16)
        self.widget.setFixedWidth(min(680, parent.width() - 40))
