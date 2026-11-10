"""设置分区在单列和双列之间切换，保留控件与编辑状态。"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget


class SettingsColumns(QWidget):
    BREAKPOINT = 1240
    COLUMN_SPACING = 24

    def __init__(self, voice, about, parent=None):
        super().__init__(parent)
        self.voice = voice
        self.sections = [voice, about]
        self.columns = 1
        self._assignment = None
        self._reflowing = False
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(self.COLUMN_SPACING)
        self.panes = []
        for _ in range(2):
            widget = QWidget(self)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(20)
            layout.addStretch()
            self.panes.append(widget)
            self.box.addWidget(widget, 1, Qt.AlignmentFlag.AlignTop)
        self._update_columns()

    def set_sections(self, sections):
        self.sections = list(sections)
        self._update_columns()

    @staticmethod
    def _section_height(section, width):
        layout = section.layout()
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else -1
        return max(height, layout.minimumSize().height(), layout.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_columns()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange) and hasattr(self, 'box'):
            self._update_columns()
        return result

    def _update_columns(self):
        if self._reflowing:
            return
        minimum = max(section.minimumSizeHint().width() for section in self.sections)
        threshold = max(self.BREAKPOINT, 2 * minimum + self.COLUMN_SPACING)
        if self.columns == 2:
            threshold -= self.COLUMN_SPACING
        columns = 2 if self.width() >= threshold else 1
        split = len(self.sections)
        if columns == 2:
            width = (self.width() - self.COLUMN_SPACING) // 2
            heights = [self._section_height(section, width) + 20 for section in self.sections]
            total = sum(heights)
            split = min(range(1, len(heights)), key=lambda index: abs(2 * sum(heights[:index]) - total))
        assignment = (columns, split, tuple(self.sections))
        if assignment == self._assignment:
            return
        self._reflowing = True
        focus = QApplication.focusWidget()
        self.columns = columns
        self._assignment = assignment
        for index, section in enumerate(self.sections):
            pane = self.panes[int(index >= split)]
            if section.parentWidget() is not pane:
                pane.layout().insertWidget(pane.layout().count() - 1, section)
                section.show()
        # 跨列移动后按原阅读顺序重排，控件及未保存的值保持不变。
        for pane in self.panes:
            members = self.sections[:split] if pane is self.panes[0] else self.sections[split:]
            for index, section in enumerate(members):
                pane.layout().removeWidget(section)
                pane.layout().insertWidget(index, section)
        self.panes[1].setVisible(columns == 2)
        if focus is not None and self.isAncestorOf(focus):
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
        self._reflowing = False
        self.updateGeometry()
