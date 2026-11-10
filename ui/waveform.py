"""审听用波形进度条，按需读取当前音频并保留有限的内存摘要。"""

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractSlider, QApplication, QSizePolicy
from qfluentwidgets import isDarkTheme, qconfig, themeColor

from app.documents.statistics import format_duration
from tasks.waveform_task import WaveformTask
from ui.tooltips import TextToolTipFilter


class WaveformSlider(QAbstractSlider):
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.loading = False
        self._desired = self._failed = self._task = None
        self._cache = OrderedDict()
        self._path = QPainterPath()
        self._path_key = None
        self._markers = ()
        self._show_focus = False
        self.setOrientation(Qt.Orientation.Horizontal)
        self.setFixedHeight(48)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('配音波形与播放位置')
        self.setToolTip('点击或拖动波形定位播放位置')
        self.installEventFilter(TextToolTipFilter(self))
        self.setMouseTracking(True)
        self.setRange(0, 0)
        self.setSingleStep(100)
        self.setPageStep(1000)
        self.valueChanged.connect(self.update)
        self.rangeChanged.connect(self._range_changed)
        qconfig.themeChanged.connect(self.update)
        qconfig.themeColorChanged.connect(self.update)
        QApplication.instance().aboutToQuit.connect(self.release)

    def sizeHint(self):
        return QSize(500, 48)

    def set_source(self, path, duration=0):
        try:
            source = Path(path).resolve() if path is not None else None
            stat = source.stat() if source else None
            key = (str(source), stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            key = None
        if key == self._desired:
            return
        self._desired, self._failed = key, None
        if self._task is not None:
            self._task.cancel()
        self.data = None
        self._path_key = None
        self._markers = ()
        self.loading = key is not None
        self.setRange(0, max(0, round(duration)))
        self.setValue(0)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._ready(key, self._cache[key])
        else:
            self._start()
        self.update()

    def set_markers(self, markers):
        markers = tuple(markers)
        if markers != self._markers:
            self._markers = markers
            self.update()

    @Slot()
    def release(self):
        self.set_source(None)
        self._cache.clear()

    def _start(self):
        if self._task is not None or self._desired is None or not self.isVisible():
            return
        task = self._task = WaveformTask(self._desired)
        task.signals.ready.connect(self._ready)
        task.signals.failed.connect(self._error)
        task.signals.finished.connect(self._finished)
        self.destroyed.connect(task.signals.cancel)
        QThreadPool.globalInstance().start(task)

    @Slot(object, object)
    def _ready(self, key, data):
        if key != self._desired:
            return
        self.data, self.loading = data, False
        self._cache[key] = data
        self._cache.move_to_end(key)
        while len(self._cache) > 12:
            self._cache.popitem(last=False)
        if self.maximum() <= 0:
            self.setRange(0, data.duration_ms)
        self._path_key = None
        self.update()

    @Slot(object, str)
    def _error(self, key, message):
        if key == self._desired:
            self.loading, self._failed = False, key
            self.update()
            self.failed.emit(message)

    @Slot()
    def _finished(self):
        task, self._task = self._task, None
        if task is not None:
            self.destroyed.disconnect(task.signals.cancel)
        if self.data is None and self._desired != self._failed:
            self._start()

    def _range_changed(self, minimum, maximum):
        self.setEnabled(maximum > minimum)
        self.update()

    def _plot_rect(self):
        return QRectF(8, 6, max(1, self.width() - 16), self.height() - 12)

    def _position(self, x):
        rect = self._plot_rect()
        fraction = min(1, max(0, (x - rect.left()) / rect.width()))
        return round(self.minimum() + fraction * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.setSliderDown(True)
            self.setSliderPosition(self._position(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        position = self._position(event.position().x())
        self.setToolTip(format_duration(position / 1000))
        if self.isSliderDown():
            self.setSliderPosition(position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderPosition(self._position(event.position().x()))
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        positions = {
            Qt.Key.Key_Left: self.value() - self.singleStep(),
            Qt.Key.Key_Right: self.value() + self.singleStep(),
            Qt.Key.Key_PageUp: self.value() + self.pageStep(),
            Qt.Key.Key_PageDown: self.value() - self.pageStep(),
            Qt.Key.Key_Home: self.minimum(), Qt.Key.Key_End: self.maximum(),
        }
        if event.key() in positions and self.maximum() > self.minimum():
            self.setSliderDown(True)
            self.setSliderPosition(positions[event.key()])
            self.setSliderDown(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        event.ignore()

    def focusInEvent(self, event):
        self._show_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self._show_focus = False
        super().focusOutEvent(event)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.data is None and self._desired != self._failed:
            self._start()

    def hideEvent(self, event):
        self.setSliderDown(False)
        self.release()
        super().hideEvent(event)

    def _wave_path(self, rect):
        key = (id(self.data), rect.width(), rect.height())
        if key == self._path_key:
            return self._path
        path = QPainterPath()
        peaks = self.data.peaks
        count = min(len(peaks), max(1, int(rect.width() / 2)))
        for column in range(count):
            group = peaks[column * len(peaks) // count:(column + 1) * len(peaks) // count]
            low, high = min(item[0] for item in group), max(item[1] for item in group)
            x = rect.left() + (column + 0.5) * rect.width() / count
            top = rect.center().y() - max(1, high / 32768 * rect.height() / 2)
            bottom = rect.center().y() - min(-1, low / 32768 * rect.height() / 2)
            path.moveTo(x, top)
            path.lineTo(x, bottom)
        self._path_key, self._path = key, path
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        dark, accent = isDarkTheme(), themeColor()
        muted = QColor('#777777' if dark else '#b7b7b7')
        if self.data is not None:
            total = max(1, self.data.duration_ms)
            shade = QColor(accent)
            shade.setAlpha(25 if dark else 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            for start, end in ((0, self.data.leading_ms),
                               (total - self.data.trailing_ms, total)):
                if end > start:
                    painter.drawRoundedRect(QRectF(rect.left() + start / total * rect.width(), rect.top(),
                                                   (end - start) / total * rect.width(), rect.height()), 3, 3)
            path = self._wave_path(rect)
            painter.setPen(QPen(muted, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)
            fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
            x = rect.left() + fraction * rect.width()
            painter.save()
            painter.setClipRect(QRectF(rect.left(), rect.top(), max(0, x - rect.left()), rect.height()))
            painter.setPen(QPen(accent, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)
            painter.restore()
        else:
            painter.setPen(QPen(muted, 1))
            painter.drawLine(QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y()))
            if self.loading:
                painter.fillRect(rect, self.palette().window())
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, '正在读取波形…')
        if self.maximum() > 0:
            x = rect.left() + self.value() / self.maximum() * rect.width()
            painter.setPen(QPen(accent, 1.5))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(QPen(muted, 1))
            for marker in self._markers:
                x = rect.left() + marker / self.maximum() * rect.width()
                painter.drawLine(QPointF(x, rect.bottom() - 4), QPointF(x, rect.bottom()))
        if self._show_focus and self.hasFocus() and self.isEnabled():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
