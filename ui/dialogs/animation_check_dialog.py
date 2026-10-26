"""配音生成前，展示空讲稿及点击动画与分段的待确认页。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidgetItem
from qfluentwidgets import (
    BodyLabel, CaptionLabel, MessageBoxBase, SubtitleLabel, TableWidget,
    setCustomStyleSheet,
)


class AnimationCheckDialog(MessageBoxBase):
    def __init__(self, issues, parent=None):
        super().__init__(parent)
        self.viewLayout.setSpacing(14)
        self.viewLayout.addWidget(SubtitleLabel('检查讲稿与点击动画', self))
        description = BodyLabel('请确认以下页面的讲稿及点击动画安排。', self)
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)
        self.table = TableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['页码', '讲稿分段', 'PPT 点击', '检查结果'])
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(TableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        for column, width in enumerate((80, 86, 86)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            values = (f'第 {issue.page} 页' if issue.page is not None else '—',
                      f'{issue.segments} 段' if issue.segments is not None else '—',
                      f'{issue.clicks} 次' if issue.clicks is not None else '待确认',
                      issue.reason)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        style = '''
            TableWidget { border: none; background: transparent; }
            TableWidget::item { padding: 8px; }
            QHeaderView::section {
                background: transparent; border: none;
                border-bottom: 1px solid BORDER_COLOR; padding: 8px;
            }
        '''
        setCustomStyleSheet(self.table,
                            style.replace('BORDER_COLOR', 'rgba(0, 0, 0, 0.08)'),
                            style.replace('BORDER_COLOR', 'rgba(255, 255, 255, 0.10)'))
        self.widget.setFixedWidth(min(700, max(520, parent.width() - 40)))
        self.table.setFixedWidth(self.widget.width() - 48)
        self.viewLayout.addWidget(self.table)
        self.table.resizeRowsToContents()
        available = max(100, min(280, parent.height() - 300))
        for row in range(len(issues)):
            self.table.setRowHeight(row, max(44, self.table.rowHeight(row)))
        self.table.setFixedHeight(min(available, header.height() + 2 * self.table.frameWidth() +
                                      sum(self.table.rowHeight(row) for row in range(len(issues)))))
        hint = CaptionLabel('按已保存的 PPT 检查；每页首段直接朗读，之后每段对应一次点击。', self)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        self.yesButton.setText('继续生成')
        self.cancelButton.setText('取消')
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(self.cancelButton)
        self.buttonLayout.addWidget(self.yesButton)
        self.cancelButton.setFixedWidth(100)
        self.yesButton.setFixedWidth(120)
        self.buttonGroup.setFixedHeight(68)
        self.buttonLayout.setContentsMargins(24, 16, 24, 16)
