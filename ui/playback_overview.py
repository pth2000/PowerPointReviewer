"""首页的讲稿统计与逐页配音时长分布。"""

import re
from itertools import accumulate

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    Action,
    CardWidget,
    FluentIcon,
    RoundMenu,
    ToolTipFilter,
    isDarkTheme,
    themeColor,
)

from app.documents.statistics import build_statistics, format_duration


def _display_duration(seconds):
    return format_duration(None if seconds is None else int(max(0, seconds) + 0.5))


def _font(widget, size):
    font = QFont(widget.font())
    font.setPixelSize(size)
    return font


def _colors():
    return (QColor('#eeeeee' if isDarkTheme() else '#202020'),
            QColor('#b4b4b4' if isDarkTheme() else '#666666'))


def _page_groups(pages, width):
    """按可见宽度归组窄页，并为零时长页保留可点击的空间。"""
    gap, minimum = 3, min(6.0, width)
    limit = max(1, int((width + gap) // (minimum + gap)))
    total = sum(page.duration for page in pages)
    weights = [page.duration if total else 1.0 for page in pages]
    content = width - gap * (min(len(pages), limit) - 1)
    scale = content / sum(weights)
    groups = []

    def append_remainder(start, end, weight, first_short_group):
        if start == end:
            return
        if len(groups) > first_short_group:
            previous = groups[-1]
            groups[-1] = (previous[0], end, previous[2] + weight)
        else:
            groups.append((start, end, weight))

    start, pending, first_short_group = 0, 0.0, 0
    for index, weight in enumerate(weights):
        if weight * scale >= minimum:
            append_remainder(start, index, pending, first_short_group)
            groups.append((index, index + 1, weight))
            start, pending, first_short_group = index + 1, 0.0, len(groups)
        else:
            pending += weight
            if pending * scale >= minimum:
                groups.append((start, index + 1, pending))
                start, pending = index + 1, 0.0
    append_remainder(start, len(pages), pending, first_short_group)

    # 大量长短页交错时，最小点击宽度仍可能超出空间；优先合并较短的相邻组。
    while len(groups) > limit:
        index = min(range(len(groups) - 1), key=lambda i: groups[i][2] + groups[i + 1][2])
        first, second = groups[index:index + 2]
        groups[index:index + 2] = [(first[0], second[1], first[2] + second[2])]

    remaining = width - gap * (len(groups) - 1)
    minimum = min(minimum, remaining / len(groups))
    sizes = [0.0] * len(groups)
    active = list(range(len(groups)))
    # 仅托住不足最小宽度的组，其余空间按实际时长分配，避免压平长短差异。
    while active:
        weight = sum(groups[index][2] for index in active)
        if not weight:
            for index in active:
                sizes[index] = remaining / len(active)
            break
        small = [index for index in active if groups[index][2] * remaining / weight < minimum]
        if not small:
            for index in active:
                sizes[index] = groups[index][2] * remaining / weight
            break
        for index in small:
            sizes[index] = minimum
            remaining -= minimum
        small = set(small)
        active = [index for index in active if index not in small]
    return [(pages[start:end], size) for (start, end, _), size in zip(groups, sizes)]


class _RegionToolTipFilter(ToolTipFilter):
    """为自绘分段复用 Fluent 提示，位置锚定到分段而非整个图表。"""

    def __init__(self, parent):
        super().__init__(parent)
        self._anchor = QWidget(parent)
        self._anchor.hide()

    def set_region(self, text='', rect=None):
        parent = self.parent()
        parent.setToolTip(text)
        if not text or rect is None:
            self.hideToolTip()
            return
        self._anchor.setGeometry(rect.toAlignedRect())
        self.isEnter = True
        if self._tooltip is None:
            self._tooltip = self._createToolTip()
            self._tooltip.label.setTextFormat(Qt.TextFormat.PlainText)
            self._tooltip.setDuration(-1)
        if self._tooltip.isVisible():
            self.showToolTip()
        else:
            self.timer.start(self._tooltipDelay)

    def showToolTip(self):
        if not self.isEnter or not self._canShowToolTip() or not self.parent().isVisible():
            return
        self._tooltip.setText(self.parent().toolTip())
        self._tooltip.adjustPos(self._anchor, self.position)
        if not self._tooltip.isVisible():
            self._tooltip.show()


class _StatisticsButton(QAbstractButton):
    """整个时长摘要可打开统计，支持鼠标与键盘。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.characters = 0
        self.has_notes = False
        self.duration = self.remaining = None
        self._show_focus = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip('查看讲稿统计')
        self.installEventFilter(ToolTipFilter(self))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def refresh(self, characters, duration, remaining):
        self.characters, self.duration, self.remaining = characters, duration, remaining
        self.setAccessibleName(f'讲稿统计，{characters:,} 字，配音时长 {_display_duration(duration)}')
        self.update()

    def footer(self):
        if not self.has_notes:
            return '尚未导入讲稿'
        text = f'{self.characters:,} 字'
        if self.remaining is not None:
            text += f' · 剩余 {_display_duration(self.remaining)}'
        return text

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary, secondary = _colors()
        if not self.isEnabled():
            painter.setOpacity(0.45)
        if self._show_focus:
            painter.setPen(QPen(themeColor(), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 5, 5)
        painter.setPen(secondary)
        painter.setFont(_font(self, 13))
        painter.drawText(QRectF(22, 16, self.width() - 58, 22), Qt.AlignmentFlag.AlignVCenter, '配音总时长')
        FluentIcon.CHEVRON_RIGHT.render(painter, QRectF(self.width() - 28, 22, 10, 10))
        value = _display_duration(self.duration) if self.duration is not None else ('待生成' if self.has_notes else '—')
        parts = re.findall(r'\d+(?:\.\d+)?|[^\d]+', value)
        size = 28 if self.duration is not None else 22
        while size > 17:
            width = 0
            for part in parts:
                painter.setFont(_font(self, size if part[0].isdigit() else 13))
                width += painter.fontMetrics().horizontalAdvance(part)
            if width <= self.width() - 44:
                break
            size -= 1
        painter.setPen(primary if self.duration is not None else secondary)
        x = 22
        for part in parts:
            painter.setFont(_font(self, size if part[0].isdigit() else (13 if self.duration is not None else 22)))
            painter.drawText(QPointF(x, 75), part)
            x += painter.fontMetrics().horizontalAdvance(part)
        painter.setFont(_font(self, 12))
        painter.setPen(secondary)
        footer = painter.fontMetrics().elidedText(self.footer(), Qt.TextElideMode.ElideRight, self.width() - 40)
        painter.drawText(QRectF(22, 94, self.width() - 40, 20), Qt.AlignmentFlag.AlignVCenter, footer)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        self._show_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._show_focus = False
        self.update()
        super().focusOutEvent(event)


class _PageDurations(QWidget):
    page_requested = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.pages = ()
        self.current_page = None
        self._bins = []
        self._hovered = None
        self._pressed = None
        self._keyboard_index = None
        self._navigation_enabled = False
        self._page_menu = None
        self.bar_tooltip = _RegionToolTipFilter(self)
        self.installEventFilter(self.bar_tooltip)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_pages(self, pages):
        if self._page_menu is not None:
            self._page_menu.close()
        self.pages = pages
        self._layout_bins()
        self.update()

    def set_current_page(self, page):
        if page != self.current_page:
            self.current_page = page
            self.update()

    def _layout_bins(self):
        self.bar_tooltip.set_region()
        self._bins = []
        self._hovered = None
        self._pressed = None
        self._keyboard_index = None
        width = self.width() - 44
        if not self.pages or width <= 0 or any(page.duration is None for page in self.pages):
            self._refresh_interaction()
            return
        x = 22.0
        for group, size in _page_groups(self.pages, width):
            self._bins.append((QRectF(x, 55, size, 22), group))
            x += size + 3
        self._refresh_interaction()

    def set_navigation_enabled(self, enabled):
        self._navigation_enabled = bool(enabled)
        if not enabled:
            self._pressed = None
            self._keyboard_index = None
            if self._page_menu is not None:
                self._page_menu.close()
        self._refresh_interaction()
        self.update()

    def _refresh_interaction(self):
        available = self._navigation_enabled and bool(self._bins)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if available else Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor if available and self._hovered is not None
                       else Qt.CursorShape.ArrowCursor)

    def _hit_bin(self, point):
        return next((index for index, (rect, _) in enumerate(self._bins)
                     if rect.adjusted(-1, -6, 1, 6).contains(point)), None)

    def _activate_bin(self, index):
        if not self._navigation_enabled or not 0 <= index < len(self._bins):
            return
        rect, group = self._bins[index]
        self._keyboard_index = None
        self.bar_tooltip.hideToolTip()
        if len(group) == 1:
            self.page_requested.emit(group[0].page)
            return
        if self._page_menu is not None:
            self._page_menu.close()
        menu = RoundMenu(parent=self)
        menu.setMaxVisibleItems(8)
        for row in group:
            action = Action(f'第 {row.page} 页 · {row.characters:,} 字 · {_display_duration(row.duration)}', menu)
            action.triggered.connect(lambda _checked=False, page=row.page: self._request_page(page))
            menu.addAction(action)

        def closed():
            if self._page_menu is menu:
                self._page_menu = None
            menu.deleteLater()

        menu.closedSignal.connect(closed)
        self._page_menu = menu
        menu.exec(self.mapToGlobal(QPointF(rect.left(), rect.bottom() + 8).toPoint()))

    def _request_page(self, page):
        if self._navigation_enabled:
            self.page_requested.emit(page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_bins()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary, secondary = _colors()
        painter.setFont(_font(self, 13))
        painter.setPen(primary)
        painter.drawText(QRectF(22, 16, 76, 22), Qt.AlignmentFlag.AlignVCenter, '逐页时长')
        row = next((row for row in self.pages if row.page == self.current_page), None)
        if row is not None:
            detail = f'当前页 {row.characters:,} 字 · {_display_duration(row.duration)}'
            if self._keyboard_index is None:
                self.setAccessibleDescription(f'第 {row.page} 页，{detail}')
            painter.setFont(_font(self, 12))
            painter.setPen(secondary)
            detail = painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideLeft, max(1, self.width() - 128))
            painter.drawText(QRectF(108, 16, max(1, self.width() - 130), 22),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, detail)
        if not self._bins:
            painter.setFont(_font(self, 12))
            painter.setPen(secondary)
            message = '生成配音后显示逐页时长' if self.pages else '导入讲稿后查看时长分布'
            painter.drawText(QRectF(22, 51, max(1, self.width() - 44), 32), Qt.AlignmentFlag.AlignCenter, message)
            return
        accent = themeColor()
        if isDarkTheme():
            accent = QColor.fromRgbF(*(channel * 0.7 + 0.3 for channel in
                                       (accent.redF(), accent.greenF(), accent.blueF())))
        active = None
        pending = self.pages[self._keyboard_index].page if self._keyboard_index is not None else None
        pending_center = None
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (rect, group) in enumerate(self._bins):
            selected = any(page.page == self.current_page for page in group)
            if selected:
                halo = QColor(accent)
                halo.setAlpha(20 if isDarkTheme() else 15)
                painter.setBrush(halo)
                painter.drawRoundedRect(rect.adjusted(-3, -6, 3, 6), 5, 5)
            fill = QColor('#454545' if isDarkTheme() else '#e5e5e5')
            if self._navigation_enabled and self._hovered == index:
                fill = QColor('#626262' if isDarkTheme() else '#cdcdcd')
            color = QColor(accent if selected else fill)
            if pending is not None and any(row.page == pending for row in group):
                pending_center = rect.center().x()
                if not selected:
                    color = QColor(accent)
                    color.setAlpha(115)
            if self._pressed == index and self._hovered == index:
                color = color.darker(112)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 2.5, 2.5)
            if selected:
                active = rect.center().x()
        marker = pending_center if pending_center is not None else active
        if marker is not None:
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(marker, 87), 1.8, 1.8)
        painter.setFont(_font(self, 12))
        first, last = self.pages[0].page, self.pages[-1].page
        labels = []
        if marker is not None:
            title = f'第 {pending} 页 · 回车定位' if pending is not None else f'第 {self.current_page} 页'
            width = painter.fontMetrics().horizontalAdvance(title) + 4
            x = min(max(marker - width / 2, 22), self.width() - 22 - width)
            labels.append((QRectF(x, 95, width, 20), title, accent))
        for number, right in ((first, False), (last, True)):
            if number == (pending if pending is not None else self.current_page) or (number == first and right):
                continue
            title = f'第 {number} 页'
            width = painter.fontMetrics().horizontalAdvance(title) + 4
            rect = QRectF(self.width() - 22 - width if right else 22, 95, width, 20)
            if not any(rect.adjusted(-5, 0, 5, 0).intersects(label[0]) for label in labels):
                labels.append((rect, title, secondary))
        for rect, title, color in labels:
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, title)

    def _set_hovered(self, hovered):
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._refresh_interaction()
        if hovered is None:
            self.bar_tooltip.set_region()
        else:
            rect, group = self._bins[hovered]
            title = f'第 {group[0].page} 页' if len(group) == 1 else f'第 {group[0].page}–{group[-1].page} 页'
            text = f'{title} · {sum(row.characters for row in group):,} 字 · {_display_duration(sum(row.duration for row in group))}'
            self.bar_tooltip.set_region(text, rect)
        self.update()

    def enterEvent(self, event):
        self._set_hovered(self._hit_bin(event.position()))
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        self._set_hovered(self._hit_bin(event.position()))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._navigation_enabled:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._keyboard_index = None
            self._pressed = self._hit_bin(event.position())
            self._hovered = self._pressed
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pressed, self._pressed = self._pressed, None
            self.update()
            if pressed is not None and pressed == self._hit_bin(event.position()):
                self._activate_bin(pressed)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._navigation_enabled and self._bins:
            current = self._keyboard_index
            if current is None:
                current = next((index for index, row in enumerate(self.pages)
                                if row.page == self.current_page), 0)
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self.bar_tooltip.hideToolTip()
                offset = -1 if event.key() == Qt.Key.Key_Left else 1
                self._keyboard_index = min(max(current + offset, 0), len(self.pages) - 1)
                target = self.pages[self._keyboard_index]
                self.setAccessibleDescription(f'待定位第 {target.page} 页，按回车定位')
                self.update()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.bar_tooltip.hideToolTip()
                page = self.pages[current].page
                self._keyboard_index = None
                self.update()
                self._request_page(page)
                event.accept()
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_index = None
        self.update()
        super().focusOutEvent(event)

    def leaveEvent(self, event):
        self._hovered = None
        self._refresh_interaction()
        self.bar_tooltip.set_region()
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.bar_tooltip.set_region()
        if self._page_menu is not None:
            self._page_menu.close()
        self._pressed = None
        super().hideEvent(event)


class PlaybackOverview(CardWidget):
    """统计使用已记录的时长；播放进度更新不重读讲稿或音频文件。"""

    page_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(132)
        self.statistics_button = _StatisticsButton(self)
        self.chart = _PageDurations(self)
        self.chart.page_requested.connect(self.page_requested.emit)
        self.chart.setAccessibleName('逐页配音时长')
        self._statistics = build_statistics({}, [])
        self._durations = ()
        self._prefix = (0,)
        self._segment_pages = ()
        self.current_page = None
        self.remaining = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.statistics_button)
        layout.addWidget(self.chart, 1)
        self.set_statistics(self._statistics)

    def sizeHint(self):
        return QSize(640, 132)

    def set_statistics(self, statistics, segments=(), durations=()):
        self._statistics = statistics
        self.statistics_button.has_notes = bool(statistics.pages)
        self._segment_pages = tuple(segment['page'] for segment in segments)
        self._durations = tuple(float(value) for value in durations) if statistics.duration is not None else ()
        self._prefix = (0, *accumulate(self._durations))
        self.chart.set_pages(statistics.pages)
        self.remaining = statistics.duration
        self.statistics_button.refresh(statistics.characters, statistics.duration, self.remaining)
        self._layout_summary()

    def set_available(self, available):
        self.statistics_button.setEnabled(available)
        self.statistics_button.setCursor(Qt.CursorShape.PointingHandCursor if available else Qt.CursorShape.ArrowCursor)
        self.statistics_button.update()

    def set_navigation_enabled(self, enabled):
        self.chart.set_navigation_enabled(enabled)

    def set_position(self, index, position_ms=0):
        self.current_page = (self._segment_pages[min(max(index, 0), len(self._segment_pages) - 1)]
                             if self._segment_pages else None)
        if self._statistics.duration is not None and 0 <= index < len(self._durations):
            elapsed = min(max(position_ms / 1000, 0), self._durations[index])
            self.remaining = max(0, self._statistics.duration - self._prefix[index] - elapsed)
        else:
            self.remaining = self._statistics.duration
        self.chart.set_current_page(self.current_page)
        self.statistics_button.refresh(self._statistics.characters, self._statistics.duration, self.remaining)

    def _layout_summary(self):
        font = _font(self.statistics_button, 12)
        self.statistics_button.setFont(font)
        # 长时讲稿适当拓宽摘要，给右侧分布保留至少一半宽度。
        width = self.statistics_button.fontMetrics().horizontalAdvance(self.statistics_button.footer()) + 44
        self.statistics_button.setFixedWidth(min(max(248, width), max(248, self.width() // 2), 340))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_summary()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor('#454545' if isDarkTheme() else '#e6e6e6'))
        x = self.statistics_button.width()
        painter.drawLine(QPointF(x, 20), QPointF(x, self.height() - 20))
