"""用 Qt 采集选中窗口的一帧，预览完成后立即释放采集资源。"""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QWindow
from PySide6.QtMultimedia import QCapturableWindow, QMediaCaptureSession, QVideoSink, QWindowCapture
from PySide6.QtWidgets import QLabel, QSizePolicy

from app import window_target


class WindowPreview(QLabel):
    readyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(180)
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.image = QImage()
        self.capture = self.session = self.sink = self.foreign_window = None
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fail('预览超时，请确认放映窗口正常显示。'))
        self.setText('选择窗口后查看预览')

    def show_target(self, target):
        self.stop()
        generation = self.generation
        try:
            hwnd = int(target.get('hwnd') or 0)
            actual = window_target.window_info(hwnd)
            if (not actual or any(target.get(key) and target[key] != actual[key]
                                  for key in ('pid', 'class_name'))):
                raise RuntimeError('窗口已关闭或改变，请刷新列表。')
            self.foreign_window = QWindow.fromWinId(hwnd)
            if self.foreign_window is None:
                raise RuntimeError('无法预览此窗口，请选择其他放映窗口。')
            window = QCapturableWindow(self.foreign_window)
            if not window.isValid():
                raise RuntimeError('此窗口无法采集，请重新启动放映。')
            self.session = QMediaCaptureSession(self)
            self.capture = QWindowCapture(self)
            self.sink = QVideoSink(self)
            self.session.setWindowCapture(self.capture)
            self.session.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(lambda frame: self._frame(frame, generation))
            self.capture.errorOccurred.connect(lambda code, message: self._error(message, generation))
            self.capture.setWindow(window)
            self.setText('正在获取窗口预览…')
            self.timer.start(8000)
            self.capture.start()
        except Exception as exc:
            self._fail(str(exc))

    def _frame(self, frame, generation):
        if generation != self.generation or self.capture is None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.image = image.copy()
        self._release()
        self._render()
        self.readyChanged.emit(True)

    def _error(self, message, generation):
        if generation == self.generation and self.capture is not None:
            self._fail('无法预览：' + message)

    def _fail(self, message):
        self.stop(message)

    def _release(self):
        self.timer.stop()
        capture, session, sink = self.capture, self.session, self.sink
        self.capture = self.session = self.sink = None
        if capture is not None:
            capture.stop()
            capture.setWindow(QCapturableWindow())
        if session is not None:
            session.setWindowCapture(None)
            session.setVideoSink(None)
        self.foreign_window = None
        for obj in (capture, session, sink):
            if obj is not None:
                obj.deleteLater()

    def stop(self, message='选择窗口后查看预览'):
        self.generation += 1
        self._release()
        self.image = QImage()
        self.clear()
        self.setText(message)
        self.readyChanged.emit(False)

    def _render(self):
        if not self.image.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = QPixmap.fromImage(self.image).scaled(
                self.size() * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()
