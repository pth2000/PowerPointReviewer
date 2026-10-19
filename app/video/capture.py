"""在工作线程采集 Windows 窗口；录制帧不包含系统鼠标指针。"""

import ctypes
from functools import lru_cache
import time
import threading

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QImage

from app.paths import resource_root


@lru_cache(maxsize=1)
def _library():
    path = resource_root() / 'native/window_capture/WindowCapture_x64.dll'
    if not path.is_file():
        raise RuntimeError('录制组件缺失，请重新下载完整的软件包。')
    try:
        library = ctypes.CDLL(str(path))
    except OSError as exc:
        raise RuntimeError('无法加载录制组件，请重新下载完整的软件包。') from exc
    library.wc_open.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    library.wc_open.restype = ctypes.c_void_p
    library.wc_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                ctypes.c_char_p, ctypes.c_int]
    library.wc_read.restype = ctypes.c_int
    library.wc_close.argtypes = [ctypes.c_void_p]
    library.wc_close.restype = None
    return library


def _error(buffer):
    text = buffer.value.decode('utf-8', errors='replace')
    if text.startswith('0x80004001'):
        return '当前系统不支持此录制方式，请使用 Windows 10 2004 或更新版本。'
    if text in ('The capture window was closed', 'The capture window no longer exists'):
        return '放映窗口已关闭，请重新开始放映后再录制。'
    return text or '无法读取放映窗口画面。'


class _CaptureWorker(QThread):
    imageReady = Signal(QImage)
    failed = Signal(str)

    def __init__(self, hwnd, fps, parent=None):
        super().__init__(parent)
        self.hwnd, self.fps = int(hwnd), int(fps)
        self.delivery_ready = threading.Event()
        self.delivery_ready.set()

    def run(self):
        handle = None
        library = None
        try:
            if self.isInterruptionRequested():
                return
            library = _library()
            error = ctypes.create_string_buffer(2048)
            # The desktop cursor stays visible. Only this capture session excludes it.
            handle = library.wc_open(self.hwnd, 0, error, len(error))
            if not handle:
                raise RuntimeError(_error(error))
            width, height = ctypes.c_int(), ctypes.c_int()
            buffer, capacity = None, 0
            deadline = time.monotonic()
            while not self.isInterruptionRequested():
                if not self.delivery_ready.is_set():
                    self.msleep(5)
                    continue
                delay = deadline - time.monotonic()
                if delay > 0:
                    self.msleep(max(1, min(10, round(delay * 1000))))
                    continue
                result = library.wc_read(handle, buffer, capacity, ctypes.byref(width),
                                         ctypes.byref(height), error, len(error))
                if result < 0:
                    raise RuntimeError(_error(error))
                if result == 2:
                    capacity = width.value * height.value * 4
                    if not 0 < capacity <= 2**31 - 1:
                        raise RuntimeError('放映画面尺寸无效，请重新开始放映。')
                    buffer = (ctypes.c_uint8 * capacity)()
                    continue
                if result == 1:
                    # Own the pixels before native storage is reused on the next read.
                    image = QImage(buffer, width.value, height.value, width.value * 4,
                                   QImage.Format.Format_RGB32).copy()
                    self.delivery_ready.clear()
                    self.imageReady.emit(image)
                    deadline = max(deadline + 1 / self.fps, time.monotonic())
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
        finally:
            if handle:
                library.wc_close(handle)


class WindowCapture(QObject):
    imageReady = Signal(QImage)
    errorOccurred = Signal(int, str)

    def __init__(self, hwnd, fps=30, parent=None):
        super().__init__(parent)
        if not 1 <= int(fps) <= 120:
            raise ValueError('录制帧率无效。')
        self.hwnd, self.fps = int(hwnd), int(fps)
        self._worker = None
        self._active = False
        self._destroy_cleanup = None

    def start(self):
        if self._worker is not None:
            return
        self._active = True
        self._worker = _CaptureWorker(self.hwnd, self.fps, self)
        worker = self._worker
        def cleanup():
            worker.requestInterruption()
            worker.wait()
        self._destroy_cleanup = cleanup
        self.destroyed.connect(cleanup)
        self._worker.imageReady.connect(self._image, Qt.ConnectionType.QueuedConnection)
        self._worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._worker.start()

    @Slot(QImage)
    def _image(self, image):
        worker = self.sender()
        if worker is self._worker:
            try:
                if self._active:
                    self.imageReady.emit(image)
            finally:
                worker.delivery_ready.set()

    @Slot(str)
    def _failed(self, text):
        if self._active and self.sender() is self._worker:
            self.errorOccurred.emit(1, text)

    def stop(self):
        self._active = False
        worker, self._worker = self._worker, None
        if worker is not None:
            if self._destroy_cleanup is not None:
                self.destroyed.disconnect(self._destroy_cleanup)
                self._destroy_cleanup = None
            worker.requestInterruption()
            worker.wait()
            worker.deleteLater()
