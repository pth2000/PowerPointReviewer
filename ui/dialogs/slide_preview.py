"""配音审听中的按页预览，缓存少量页面并合并快速切换请求。"""

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QRectF, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import IndeterminateProgressBar, isDarkTheme

from tasks.slide_preview_task import SlidePreviewTask, document_key
from ui.dialogs.base import ThemedDialog


class _SlideCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.caption = ''
        self.message = ''
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#252525' if isDarkTheme() else '#ededee'))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        bounds = self.rect().adjusted(8, 8, -8, -28)
        if not self.image.isNull():
            size = self.image.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(QRectF(bounds).center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)
        else:
            painter.setPen(QColor('#b9b9b9' if isDarkTheme() else '#626262'))
            painter.drawText(bounds.adjusted(12, 0, -12, 0),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        painter.setPen(QColor('#b9b9b9' if isDarkTheme() else '#626262'))
        painter.drawText(self.rect().adjusted(12, 0, -12, -6),
                         Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self.caption)


class SlidePreview(_SlideCanvas):
    CACHE_SIZE = 6

    def __init__(self, parent=None, *, keep_session_on_hide=False):
        super().__init__(parent)
        self.setFixedHeight(210)
        self._keep_session_on_hide = keep_session_on_hide
        self.setToolTip('显示已保存的幻灯片，单击放大。')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._source = None
        self._page = None
        self._preference = 'auto'
        self._desired = None
        self._task = None
        self._submitted = None
        self._opening_document = None
        self._loading = False
        self._shutting_down = False
        self._cache = OrderedDict()
        self._watched_source = None
        self._source_watcher = QFileSystemWatcher(self)
        self._source_watcher.fileChanged.connect(self._schedule_source_refresh)
        self._source_watcher.directoryChanged.connect(self._schedule_source_refresh)
        self._source_refresh = QTimer(self)
        self._source_refresh.setSingleShot(True)
        self._source_refresh.setInterval(300)
        self._source_refresh.timeout.connect(self._refresh_source)
        self.progress = IndeterminateProgressBar(self)
        self.progress.setFixedHeight(2)
        self.progress.hide()
        QApplication.instance().aboutToQuit.connect(self._shutdown)

    def set_page(self, source, page, preference='auto'):
        self._source, self._page, self._preference = source, page, preference
        self._watch_source()
        self._request()

    def _watch_source(self):
        path = Path(self._source).resolve() if self._source else None
        if path != self._watched_source:
            self._source_refresh.stop()
            watched = self._source_watcher.files() + self._source_watcher.directories()
            if watched:
                self._source_watcher.removePaths(watched)
            self._watched_source = path
        if path is not None:
            # Office 保存可能替换原文件，目录监听负责恢复失效的文件监听。
            if path.parent.is_dir() and str(path.parent) not in self._source_watcher.directories():
                self._source_watcher.addPath(str(path.parent))
            if path.is_file() and str(path) not in self._source_watcher.files():
                self._source_watcher.addPath(str(path))

    def _schedule_source_refresh(self, _path):
        self._source_refresh.start()

    def _refresh_source(self):
        self._watch_source()
        if self._key() != self._desired:
            self._request()

    def _key(self):
        if not self._source or self._page is None:
            return None
        try:
            path = Path(self._source).resolve()
            stat = path.stat()
            if not path.is_file():
                return None
            return (str(path), stat.st_mtime_ns, stat.st_size, self._page, self._preference)
        except OSError:
            return None

    def _request(self):
        if self._shutting_down:
            return
        key = self._key()
        if key != self._desired:
            self._desired = key
            self.image = QImage()
            if key is not None:
                self._cache = OrderedDict((cached, image) for cached, image in self._cache.items()
                                          if document_key(cached) == document_key(key))
        self.caption = f'第 {self._page} 页' if self._page is not None else ''
        self.setAccessibleName(f'页面预览 {self.caption}')
        if key is None:
            self.message = '关联 PPT 后可查看页面预览。' if self._page is not None else '选择一个片段查看页面。'
            self._set_loading(False)
            if self._task is not None:
                self._task.cancel()
        elif key in self._cache:
            self.image = self._cache[key]
            self.message = ''
            self._cache.move_to_end(key)
            self._set_loading(False)
        else:
            if self.isVisible():
                if self._task is None:
                    self.message = '正在打开演示文稿…'
                    self._opening_document = document_key(key)
                    task = self._task = SlidePreviewTask(key)
                    task.signals.ready.connect(self._ready)
                    task.signals.phase.connect(self._phase)
                    task.signals.finished.connect(self._finished)
                    self.destroyed.connect(task.signals.cancel)
                    QApplication.instance().aboutToQuit.connect(task.signals.cancel)
                    self._submitted = key
                    self._set_loading(True)
                    QThreadPool.globalInstance().start(task)
                elif not self._task.is_cancelled() and (key != self._submitted or not self._loading):
                    self.message = ('正在打开演示文稿…' if self._opening_document == document_key(key)
                                    else '正在加载页面…')
                    self._submitted = key
                    self._set_loading(True)
                    self._task.request(key)
        self.update()

    @Slot(object, QImage, str)
    def _ready(self, key, image, error):
        if self._task is None or self._task.is_cancelled() or key != self._desired:
            return
        if key != self._key():
            if self.isVisible():
                self._request()
            return
        if not image.isNull():
            self._cache[key] = image
            self._cache.move_to_end(key)
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
            self.image = image
            self.message = ''
        else:
            self.message = error or '页面预览已取消，单击重试。'
        self._set_loading(False)
        self.update()

    @Slot(object, str)
    def _phase(self, key, phase):
        if (self._loading and self._desired is not None
                and document_key(key) == document_key(self._desired)):
            if phase == 'opening':
                self._opening_document = document_key(key)
                self.message = '正在打开演示文稿…'
            elif key == self._desired:
                self._opening_document = None
                self.message = f'正在加载第 {key[3]} 页…'
            self.setAccessibleDescription(self.message)
            self.update()

    @Slot()
    def _finished(self):
        self._task = None
        self._submitted = None
        self._opening_document = None
        if self.isVisible():
            self._request()

    def _set_loading(self, loading):
        self._loading = loading
        self.progress.setVisible(loading)
        self.setCursor(Qt.CursorShape.BusyCursor if loading else Qt.CursorShape.PointingHandCursor)
        self.setAccessibleDescription(self.message)

    @Slot()
    def _shutdown(self):
        self._shutting_down = True
        self._source_refresh.stop()
        if self._task is not None:
            self._task.cancel()
        self._set_loading(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.progress.setGeometry(6, self.height() - 2, max(1, self.width() - 12), 2)

    def showEvent(self, event):
        super().showEvent(event)
        self.progress.setVisible(self._loading)
        QTimer.singleShot(0, self, self._request)

    def hideEvent(self, event):
        if self._keep_session_on_hide:
            self.progress.hide()
        else:
            self.release()
        super().hideEvent(event)

    def release(self):
        """结束预览会话；短暂切换内容时可保留会话至所属窗口关闭。"""
        if self._task is not None:
            self._task.cancel()
        self._set_loading(False)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._activate()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            event.accept()
            return
        super().keyPressEvent(event)

    def _activate(self):
        if self._loading:
            return
        if self.image.isNull():
            self._request()
            return
        dialog = ThemedDialog(self)
        dialog.setWindowTitle(f'页面预览 · {self.caption}')
        dialog.resize(1000, 700)
        dialog.setMinimumSize(480, 320)
        layout = QVBoxLayout(dialog)
        canvas = _SlideCanvas(dialog)
        canvas.image, canvas.caption = self.image, self.caption
        layout.addWidget(canvas)
        dialog.exec()
        dialog.deleteLater()
