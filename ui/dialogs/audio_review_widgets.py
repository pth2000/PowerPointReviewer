"""配音审听使用的片段列表与轻量状态提示。"""

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTextLayout
from PySide6.QtWidgets import QSizePolicy, QStyle, QWidget
from qfluentwidgets import (
    CaptionLabel,
    SegmentedWidget,
    isDarkTheme,
    setCustomStyleSheet,
    themeColor,
)
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from app.documents.statistics import format_duration
from ui.icons import SystemIcon

SEGMENT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def playback_timestamp(seconds):
    """播放器使用紧凑时间格式，短片段保留百分之一秒。"""
    seconds = max(0, seconds)
    total, fraction = (int(seconds + 0.5), 0) if seconds >= 60 else divmod(round(seconds * 100), 100)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    text = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
    return f'{text}.{fraction:02d}' if fraction else text


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
    """按可用宽度完整显示讲稿，选择片段时保持行高稳定。"""

    PLAY_COLUMN_WIDTH = 56
    TEXT_LEFT = 8
    TEXT_RIGHT = 18
    play_requested = Signal(int)

    @staticmethod
    def play_rect(rect):
        result = QRectF(0, 0, 34, 34)
        result.moveCenter(QRectF(rect).center())
        return result

    def cancel_play_press(self):
        self._pressed_play = None
        self.parent().viewport().update()

    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
                and event.button() == Qt.MouseButton.LeftButton):
            inside = index.column() == 0 and self.play_rect(option.rect).contains(event.position())
            if event.type() == QEvent.Type.MouseButtonPress:
                self._pressed_play = index.data(Qt.ItemDataRole.UserRole) if inside else None
            else:
                pressed = getattr(self, '_pressed_play', None)
                self._pressed_play = None
                if inside and pressed == index.data(Qt.ItemDataRole.UserRole):
                    self.parent().viewport().update()
                    self.play_requested.emit(index.data(Qt.ItemDataRole.UserRole))
                    return True
            self.parent().viewport().update()
        return super().editorEvent(event, model, option, index)

    @staticmethod
    def body_font(font):
        font = QFont(font)
        font.setPointSizeF(11)
        return font

    @classmethod
    def row_height(cls, text, width, font):
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
        background = QColor(accent)
        background.setAlpha(18 if dark else 9)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(row, 8, 8)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(4, row.center().y() - 12, 3, 24), 1.5, 1.5)
        elif self.hoverRow == index.row():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 9) if dark else QColor(0, 0, 0, 5))
            painter.drawRoundedRect(row, 8, 8)

        if index.column() == 0:
            button = self.play_rect(option.rect)
            hovered = self.hoverRow == index.row()
            pressed = getattr(self, '_pressed_play', None) == index.data(Qt.ItemDataRole.UserRole)
            fill = QColor(255, 255, 255, 10) if dark else QColor(0, 0, 0, 5)
            border = QColor(255, 255, 255, 20) if dark else QColor(0, 0, 0, 12)
            color = QColor('#eeeeee' if dark else '#444444')
            if selected:
                fill, border = QColor(accent), QColor(accent)
                fill.setAlpha(52 if dark else 18)
                border.setAlpha(65 if dark else 32)
                color = accent.lighter(150) if dark else accent
            if hovered or pressed:
                fill.setAlpha(min(255, fill.alpha() + (24 if pressed else 12)))
            painter.setPen(QPen(border, 1))
            painter.setBrush(fill)
            painter.drawEllipse(button.adjusted(.5, .5, -.5, -.5))
            icon = QRectF(0, 0, 16, 16)
            icon.moveCenter(button.center() + QPointF(1, 0))
            SystemIcon.PLAY.render(painter, icon, fill=color.name())
        elif index.column() == 2:
            data = index.data(SEGMENT_ROLE) or {}
            rect = QRectF(option.rect).adjusted(self.TEXT_LEFT, 14, -self.TEXT_RIGHT, -12)
            font = QFont(option.font)
            font.setPointSizeF(9)
            painter.setFont(font)
            painter.setPen(QColor('#b9b9b9' if dark else '#626262'))
            metrics = QFontMetricsF(font)
            duration = data.get('duration', '')
            if data.get('sentences'):
                duration = f'{len(data["sentences"])} 句 · {duration}'
            duration_width = min(rect.width() * 0.45, metrics.horizontalAdvance(duration))
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
                             metrics.elidedText(duration, Qt.TextElideMode.ElideRight, duration_width))
            font = self.body_font(option.font)
            painter.setFont(font)
            painter.setPen(QColor('#f0f0f0' if dark else '#252525'))
            text = ' '.join(str(data.get('text', '')).split()) or '（空白片段）'
            layout = QTextLayout(text, font)
            layout.beginLayout()
            y = rect.y() + 26
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, rect.width()))
                line.draw(painter, QPointF(rect.x(), y))
                y += QFontMetricsF(font).height() + 4
            layout.endLayout()
        painter.restore()


class ReviewStatusLabel(CaptionLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class SentenceTranscript(QWidget):
    """在讲稿区域逐句阅读和定位，段落选择与配音调整保持独立。"""

    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sentences = ()
        self.current_number = None
        self._hover = None
        self._focused = 1
        self._pressed = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setAccessibleName('分句讲稿')

    def _rows(self, width):
        rows, y = [], 0
        for sentence in self.sentences:
            layout = QTextLayout(' '.join(sentence.text.split()), self.font())
            layout.beginLayout()
            offset = 0
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, width - 92))
                line.setPosition(QPointF(0, offset))
                offset += QFontMetricsF(self.font()).height() + 4
            layout.endLayout()
            height = math.ceil(offset + 12)
            rows.append((QRectF(0, y, width, height), sentence, layout))
            y += height + 6
        return rows

    def sentence_rect(self, number):
        return next((rect for rect, sentence, _ in self._rows(self.width())
                     if sentence.number == number), QRectF())

    def set_sentences(self, sentences):
        sentences = tuple(sentences)
        if sentences != self.sentences:
            self.sentences = sentences
            self._hover = self._pressed = None
            self._focused = 1
            self.setAccessibleDescription(f'共 {len(sentences)} 句。上下键选择句子，回车试听。')
            self._resize_content()
            self.update()

    def set_current(self, number):
        if number != self.current_number:
            self.current_number = number
            self.update()

    def _resize_content(self):
        self.setMinimumHeight(self.heightForWidth(self.width()))
        self.updateGeometry()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        rows = self._rows(width)
        return math.ceil(rows[-1][0].bottom()) if rows else 0

    def sizeHint(self):
        return QSize(240, self.heightForWidth(max(160, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_content()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (hasattr(self, 'sentences')
                and event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange)):
            self._resize_content()
            self.update()

    def _hit(self, point):
        return next((sentence.number for rect, sentence, _ in self._rows(self.width())
                     if rect.contains(point)), None)

    def mouseMoveEvent(self, event):
        self._hover = self._hit(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hover else Qt.CursorShape.ArrowCursor)
        self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = (self._hit(event.position()), event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        number = self._hit(event.position())
        if (event.button() == Qt.MouseButton.LeftButton and pressed and number is not None
                and number == pressed[0] and (event.position() - pressed[1]).manhattanLength() < 6):
            self._focused = number
            self.activated.emit(number)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.sentences:
            offset = -1 if event.key() == Qt.Key.Key_Up else 1
            self._focused = min(max(1, self._focused + offset), len(self.sentences))
            self.update()
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.sentences:
            self.activated.emit(self._focused)
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark, accent = isDarkTheme(), themeColor()
        secondary = QColor('#b9b9b9' if dark else '#626262')
        small = QFont(self.font())
        small.setPointSizeF(max(8, small.pointSizeF() - 1))
        for rect, sentence, layout in self._rows(self.width()):
            active = sentence.number == self.current_number
            focused = self.hasFocus() and sentence.number == self._focused
            if active or sentence.number == self._hover or focused:
                fill = QColor(accent) if active else QColor('#ffffff' if dark else '#000000')
                fill.setAlpha(22 if active and dark else 12 if active else 10 if dark else 5)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(rect, 5, 5)
            painter.setFont(small)
            painter.setPen(accent.lighter(130) if active and dark else accent if active else secondary)
            painter.drawText(rect.adjusted(4, 5, -4, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             f'{sentence.number:02d}')
            painter.setFont(self.font())
            painter.setPen(QColor('#f0f0f0' if dark else '#252525'))
            layout.draw(painter, QPointF(30, rect.y() + 4))
            painter.setFont(small)
            painter.setPen(secondary)
            painter.drawText(rect.adjusted(0, 5, -8, 0),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                             format_duration(sentence.end - sentence.start))
