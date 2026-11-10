"""首页预览当前页的剩余讲稿，突出当前段并将超出可用行数的内容渐隐。"""

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QAbstractButton, QSizePolicy
from qfluentwidgets import CardWidget, isDarkTheme, qconfig

from ui.position_control import PositionControl
from ui.tooltips import TextToolTipFilter


class _ScriptToolTipFilter(TextToolTipFilter):
    def set_text(self, text):
        self.parent().setToolTip(text)
        if not text:
            self.hideToolTip()
        elif self._tooltip and self._tooltip.isVisible():
            self._tooltip.setText(text)
            self._tooltip.adjustPos(self.parent(), self.position)


class _ReviewArea(QAbstractButton):
    """正文保留完整讲稿悬停提示，同时提供审听入口。"""

    def paintEvent(self, event):
        # 正文由父卡片绘制，点击区域不另画边框或底板。
        pass

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class ManuscriptPreview(CardWidget):
    page_requested = Signal(int)
    segment_requested = Signal(int)
    navigation_error = Signal(str)
    review_requested = Signal()
    _VISIBLE_LINES = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ''
        self._following = ()
        self._page_text = ''
        self._empty_text = '本段无讲稿'
        self._playing = False
        self._countdown = False
        self._position = None
        self._image = QImage()
        self._image_key = None
        self.has_overflow = False
        self._navigation_enabled = True
        self.script_area = _ReviewArea(self)
        self.script_area.setAccessibleName('配音审听')
        self.script_area.clicked.connect(self.review_requested.emit)
        self.set_review_available(False)
        self.script_tooltip = _ScriptToolTipFilter(self.script_area)
        self.script_area.installEventFilter(self.script_tooltip)
        self.page_position = PositionControl('页码', self)
        self.segment_position = PositionControl('片段', self)
        for control in (self.page_position, self.segment_position):
            control.setFont(self._position_font())
            control.invalid_input.connect(self.navigation_error.emit)
        self.page_position.position_requested.connect(self.page_requested.emit)
        self.segment_position.position_requested.connect(self.segment_requested.emit)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName('当前讲稿')
        self.setAccessibleDescription('本段无讲稿')
        self._update_height()
        qconfig.themeChanged.connect(self._invalidate)

    def text(self):
        """返回当前段的讲稿。"""
        return self._text

    def remaining_text(self):
        return '\n\n'.join(part for part in (self._text, *self._following) if part.strip())

    def set_review_available(self, available):
        self.script_area.setEnabled(available)
        self.script_area.setFocusPolicy(Qt.FocusPolicy.TabFocus if available else Qt.FocusPolicy.NoFocus)
        self.script_area.setCursor(Qt.CursorShape.PointingHandCursor if available else Qt.CursorShape.ArrowCursor)

    def set_content(self, text, *, following=(), page_text=None,
                    playing=False, countdown=False, empty_text='本段无讲稿'):
        text = str(text)
        following = tuple(str(part) for part in following)
        page_text = '\n\n'.join((text, *following)) if page_text is None else str(page_text)
        state = text, following, page_text, bool(playing), bool(countdown), str(empty_text)
        if state == (self._text, self._following, self._page_text,
                     self._playing, self._countdown, self._empty_text):
            return
        (self._text, self._following, self._page_text,
         self._playing, self._countdown, self._empty_text) = state
        self._update_accessible_name()
        description = self.remaining_text().strip() or self._empty_text
        self.setAccessibleDescription(description)
        self.script_area.setAccessibleDescription(description)
        self.script_tooltip.set_text(self._page_text if self._page_text.strip() else '')
        self._invalidate()

    def set_position(self, *, page=0, page_count=0, segment=0, segment_count=0):
        position = (page, page_count, segment, segment_count)
        position = position if all(value > 0 for value in position) else None
        if position == self._position:
            return
        self._position = position
        page, pages, segment, segments = position or (0, 0, 0, 0)
        self.page_position.set_position(page, pages)
        self.segment_position.set_position(segment, segments)
        self.set_navigation_enabled(self._navigation_enabled)
        self._layout_controls()
        self._update_accessible_name()
        self.update()

    def set_navigation_enabled(self, enabled):
        self._navigation_enabled = bool(enabled)
        for control in (self.page_position, self.segment_position):
            control.setEnabled(self._navigation_enabled and control.total > 0)

    def cancel_navigation(self):
        self.page_position.cancel_edit()
        self.segment_position.cancel_edit()

    def progress_text(self):
        if not self._position:
            return ''
        page, pages, segment, segments = self._position
        return f'页码 {page} / {pages} · 片段 {segment} / {segments}'

    def _update_accessible_name(self):
        self.setAccessibleName('，'.join(filter(None, (self.caption(), self.progress_text()))))

    def _position_font(self, *, bold=False):
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return font

    def _progress_column_width(self):
        group_width = max(self.page_position.content_width(), self.segment_position.content_width())
        width = max(128, math.ceil(group_width * 2) + 20)
        return min(width, max(80, math.floor(self.width() * 0.45) - 40))

    def _layout_controls(self):
        column_width = self._progress_column_width()
        group_width = (column_width - 20) / 2
        for index, control in enumerate((self.page_position, self.segment_position)):
            control.setGeometry(round(14 + index * (group_width + 20)), 12,
                                round(group_width + 12), max(1, self.height() - 24))
        text_x = 20 + column_width + 28
        self.script_area.setGeometry(text_x, 0, max(1, self.width() - text_x - 20), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_controls()

    def caption(self):
        if self._countdown:
            return '即将朗读'
        return '正在朗读' if self._playing else '当前讲稿'

    def _update_height(self):
        self.setFixedHeight(max(80, math.ceil(self._line_height() * self._VISIBLE_LINES + 24)))

    def _line_height(self):
        return QFontMetricsF(self.font()).lineSpacing() + 2

    def _invalidate(self, *_):
        self._image_key = None
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            for control in (self.page_position, self.segment_position):
                control.setFont(self._position_font())
            self._update_height()
            self._layout_controls()
            self._invalidate()

    def _text_image(self, width):
        dark = isDarkTheme()
        ratio = self.devicePixelRatioF()
        key = (self._text, self._following, self._empty_text, width,
               self.font().toString(), ratio, dark, self.isEnabled())
        if self._image_key == key:
            return self._image
        font = self.font()
        line_height = self._line_height()
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        paragraphs = [(self._text, True), *((part, False) for part in self._following if part.strip())]
        layouts, bottoms = [], []
        y = 0
        for text, current in paragraphs:
            if layouts:
                y += 2
            layout = QTextLayout(' '.join(text.split()) or self._empty_text, font)
            layout.setTextOption(option)
            layout.beginLayout()
            while len(bottoms) <= self._VISIBLE_LINES:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, width))
                line.setPosition(QPointF(0, y))
                y += line_height
                bottoms.append(y)
            layout.endLayout()
            color = QColor(('#eeeeee' if dark else '#303030') if current else ('#a2a2a2' if dark else '#888888'))
            if not text.strip() or not self.isEnabled():
                color.setAlpha(140)
            layouts.append((layout, color))
            if len(bottoms) > self._VISIBLE_LINES:
                break
        self.has_overflow = len(bottoms) > self._VISIBLE_LINES
        height = math.ceil(bottoms[min(self._VISIBLE_LINES, len(bottoms)) - 1] if bottoms else line_height)
        image = QImage(max(1, math.ceil(width * ratio)), max(1, math.ceil(height * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for layout, color in layouts:
            painter.setPen(color)
            layout.draw(painter, QPointF())
        if self.has_overflow:
            # 最后一行渐隐，其余文字保持清晰。
            mask = QLinearGradient(0, 0, 0, height)
            mask.setColorAt(0, QColor(0, 0, 0, 255))
            mask.setColorAt(2 / 3, QColor(0, 0, 0, 255))
            mask.setColorAt(0.8, QColor(0, 0, 0, 205))
            mask.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.fillRect(QRectF(0, 0, width, height), mask)
        painter.end()
        self._image_key, self._image = key, image
        return image

    def sizeHint(self):
        return QSize(360, self.height())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        title_width = self._progress_column_width()
        text_x = 20 + title_width + 28
        width = max(1, self.width() - text_x - 20)
        divider = QColor(255, 255, 255, 25) if isDarkTheme() else QColor(0, 0, 0, 20)
        painter.setPen(divider)
        divider_x = 20 + title_width + 13
        painter.drawLine(QPointF(divider_x, 16), QPointF(divider_x, self.height() - 16))
        image = self._text_image(width)
        image_height = image.height() / image.devicePixelRatio()
        painter.drawImage(QPointF(text_x, (self.height() - image_height) / 2), image)
