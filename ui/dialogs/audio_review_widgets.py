"""配音审听使用的片段列表与轻量状态提示。"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QTextLayout
from PySide6.QtWidgets import QSizePolicy, QStyle, QWidget
from qfluentwidgets import CaptionLabel, SegmentedWidget, isDarkTheme, setCustomStyleSheet, themeColor
from qfluentwidgets.components.widgets.table_view import TableItemDelegate


SEGMENT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class AudioSourceSwitch(SegmentedWidget):
    """紧凑的配音来源切换，选中底板与文字共享相同的内边距。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout.setContentsMargins(3, 3, 3, 3)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def insertItem(self, index, routeKey, text, onClick=None, icon=None):
        item = super().insertItem(index, routeKey, text, onClick, icon)
        if item is not None:
            style = '''SegmentedItem[isSelected=true], SegmentedItem[isSelected=false] {
                padding: 6px 12px; margin: 0px; border: none; border-radius: 4px;
            }'''
            setCustomStyleSheet(item, style, style)
        return item

    def paintEvent(self, event):
        QWidget.paintEvent(self, event)
        item = self.currentItem()
        if item is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(255, 255, 255, 20) if isDarkTheme() else QColor(0, 0, 0, 22))
        painter.setBrush(QColor('#414141') if isDarkTheme() else QColor('#ffffff'))
        painter.drawRoundedRect(QRectF(item.geometry()).adjusted(.5, .5, -.5, -.5), 4, 4)


class SegmentDelegate(TableItemDelegate):
    """以讲稿正文为主，选中段落完整展开供对照审听。"""

    def updateEditorGeometry(self, editor, option, index):
        if index.column() == 0:
            # 按钮容器填满当前单元格，避免基类按旧高度偏移位置并裁去左侧空间。
            editor.setGeometry(option.rect)
        else:
            super().updateEditorGeometry(editor, option, index)

    @staticmethod
    def body_font(font):
        font = QFont(font)
        font.setPointSizeF(11)
        return font

    @classmethod
    def row_height(cls, text, width, font, expanded):
        font = cls.body_font(font)
        layout = QTextLayout(' '.join(str(text).split()) or '（空白片段）', font)
        layout.beginLayout()
        lines = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            lines += 1
            if not expanded and lines >= 3:
                break
        layout.endLayout()
        return math.ceil(50 + max(1, lines) * (QFontMetricsF(font).height() + 4))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.rect)
        dark = isDarkTheme()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        row = QRectF(4, option.rect.y() + 3, self.parent().viewport().width() - 8,
                     option.rect.height() - 6)
        accent = themeColor()
        background = QColor(255, 255, 255, 13) if dark else QColor(0, 0, 0, 5)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(row, 8, 8)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(4, row.y() + 18, 3, row.height() - 36), 1.5, 1.5)
        elif self.hoverRow == index.row():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 9) if dark else QColor(0, 0, 0, 5))
            painter.drawRoundedRect(row, 8, 8)

        if index.column() == 2:
            data = index.data(SEGMENT_ROLE) or {}
            rect = QRectF(option.rect).adjusted(8, 14, -18, -12)
            font = QFont(option.font)
            font.setPointSizeF(9)
            painter.setFont(font)
            painter.setPen(QColor('#b9b9b9' if dark else '#626262'))
            metrics = QFontMetricsF(font)
            duration_width = min(rect.width() * 0.45, metrics.horizontalAdvance(data.get('duration', '')))
            status = data.get('status', '')
            badge_width = metrics.horizontalAdvance(status) + 14 if status else 0
            heading_width = max(1, rect.width() - duration_width - 16 - (badge_width + 8 if status else 0))
            heading = data.get('heading', '')
            if self.parent().property('playingIndex') == index.data(Qt.ItemDataRole.UserRole):
                heading += ' · ' + str(self.parent().property('playingText') or '')
                painter.setPen(accent.lighter(130) if dark else accent)
            heading = metrics.elidedText(heading, Qt.TextElideMode.ElideRight, heading_width)
            painter.drawText(QRectF(rect.x(), rect.y(), heading_width, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             heading)
            if status:
                badge = QRectF(rect.x() + metrics.horizontalAdvance(heading) + 8, rect.y() - 1, badge_width, 20)
                color = QColor('#54bf8b' if dark else '#26724b') if status == '已调整' else accent
                fill = QColor(color)
                fill.setAlpha(28 if dark else 17)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(badge, 4, 4)
                painter.setPen(color.lighter(125) if dark and status != '已调整' else color)
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, status)
            painter.setPen(QColor('#b9b9b9' if dark else '#626262'))
            painter.drawText(QRectF(rect.right() - duration_width, rect.y(), duration_width, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(data.get('duration', ''), Qt.TextElideMode.ElideRight, duration_width))
            font = self.body_font(option.font)
            painter.setFont(font)
            painter.setPen(QColor('#f0f0f0' if dark else '#252525'))
            text = ' '.join(str(data.get('text', '')).split()) or '（空白片段）'
            layout = QTextLayout(text, font)
            layout.beginLayout()
            y = rect.y() + 26
            number = 0
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, rect.width()))
                if number == 2 and not data.get('expanded'):
                    remaining = text[line.textStart():]
                    painter.drawText(QRectF(rect.x(), y, rect.width(), line.height()),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     QFontMetricsF(font).elidedText(
                                         remaining, Qt.TextElideMode.ElideRight, rect.width()))
                    break
                else:
                    line.draw(painter, QPointF(rect.x(), y))
                y += QFontMetricsF(font).height() + 4
                number += 1
            layout.endLayout()
        painter.restore()


class ReviewStatusLabel(CaptionLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))
