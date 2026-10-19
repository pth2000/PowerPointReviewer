"""采集线程的像素所有权、限流、错误和退出行为。"""

from collections import deque
import ctypes
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer
from PySide6.QtWidgets import QApplication

from app.video.capture import WindowCapture


class NativeCapture:
    def __init__(self, frames=()):
        self.frames = deque(frames)
        self.calls = []
        self.open_error = None
        self.frame_copied = threading.Event()

    def wc_open(self, hwnd, cursor, error, capacity):
        self.calls.append(('open', threading.get_ident(), hwnd, cursor))
        if self.open_error:
            error.value = self.open_error
            return None
        return 123

    def wc_read(self, handle, buffer, capacity, width, height, error, error_capacity):
        self.calls.append(('read', threading.get_ident()))
        if not self.frames:
            time.sleep(.005)
            return 0
        frame = self.frames[0]
        if isinstance(frame, bytes):
            error.value = self.frames.popleft()
            return -1
        w, h, bgra = frame
        width._obj.value, height._obj.value = w, h
        if capacity < w * h * 4:
            return 2
        self.frames.popleft()
        ctypes.memmove(buffer, bgra * (w * h), w * h * 4)
        self.frame_copied.set()
        return 1

    def wc_close(self, handle):
        self.calls.append(('close', threading.get_ident()))


class WindowCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.native = NativeCapture()
        self.patch = patch('app.video.capture._library', return_value=self.native)
        self.patch.start()
        self.capture = WindowCapture(456, 60)
        self.images, self.errors = [], []
        self.capture.imageReady.connect(self.images.append)
        self.capture.errorOccurred.connect(lambda code, text: self.errors.append(text))

    def tearDown(self):
        self.capture.stop()
        self.capture.deleteLater()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.patch.stop()

    def wait_for(self, condition):
        loop, timer, timeout = QEventLoop(), QTimer(), QTimer()
        timer.timeout.connect(lambda: loop.quit() if condition() else None)
        timer.start(5)
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(1500)
        if not condition():
            loop.exec()
        timer.stop()
        timeout.stop()
        self.assertTrue(condition(), (self.errors, self.native.calls[-5:]))

    def test_excludes_cursor_and_uses_same_worker_for_native_lifetime(self):
        self.native.frames.append((3, 2, b'\x11\x22\x33\xff'))
        self.capture.start()
        self.wait_for(lambda: len(self.images) == 1)
        self.capture.stop()
        self.assertEqual(self.native.calls[0][2:], (456, 0))
        self.assertEqual(self.native.calls[-1][0], 'close')
        threads = {call[1] for call in self.native.calls}
        self.assertEqual(len(threads), 1)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertEqual(self.images[0].pixelColor(0, 0).name(), '#332211')

    def test_pixels_survive_buffer_reuse_and_resize(self):
        self.native.frames.extend([(3, 2, b'\x00\x00\xff\xff'),
                                   (3, 2, b'\x00\xff\x00\xff'),
                                   (6, 5, b'\xff\x00\x00\xff')])
        self.capture.start()
        self.wait_for(lambda: len(self.images) == 3)
        self.capture.stop()
        self.assertEqual([im.size().toTuple() for im in self.images], [(3, 2), (3, 2), (6, 5)])
        self.assertEqual([im.pixelColor(0, 0).name() for im in self.images],
                         ['#ff0000', '#00ff00', '#0000ff'])

    def test_only_one_frame_is_queued_and_stop_drops_late_delivery(self):
        self.native.frames.extend([(3, 2, b'\x00\x00\xff\xff')] * 100)
        self.capture.start()
        # Keep the GUI busy: the worker must not build an unbounded signal queue.
        self.assertTrue(self.native.frame_copied.wait(1))
        time.sleep(.05)
        self.assertEqual(len(self.native.frames), 99)
        self.capture.stop()
        self.application.processEvents()
        self.assertEqual(self.images, [])

    def test_native_read_error_closes_session_and_emits_once(self):
        self.native.frames.append(b'The capture window was closed')
        self.capture.start()
        self.wait_for(lambda: bool(self.errors))
        self.capture.stop()
        self.assertEqual(self.errors, ['放映窗口已关闭，请重新开始放映后再录制。'])
        self.assertEqual(sum(call[0] == 'close' for call in self.native.calls), 1)

    def test_unsupported_cursor_exclusion_has_clear_error(self):
        self.native.open_error = b'0x80004001: Cursor exclusion is unavailable'
        self.capture.start()
        self.wait_for(lambda: bool(self.errors))
        self.assertIn('Windows 10 2004', self.errors[0])
        self.assertFalse(any(call[0] == 'close' for call in self.native.calls))

    def test_invalid_frame_size_fails_without_allocating(self):
        self.native.frames.append((2**30, 2, b'\x00' * 4))
        self.capture.start()
        self.wait_for(lambda: bool(self.errors))
        self.assertIn('画面尺寸无效', self.errors[0])

    def test_restart_after_stop_and_parent_destruction_stop_worker(self):
        import shiboken6
        self.capture.start()
        self.capture.start()
        self.wait_for(lambda: bool(self.native.calls))
        self.capture.stop()
        self.capture.start()
        self.wait_for(lambda: sum(call[0] == 'open' for call in self.native.calls) == 2)
        self.capture.stop()
        parent = QObject()
        child = WindowCapture(789, parent=parent)
        child.start()
        self.wait_for(lambda: sum(call[0] == 'open' for call in self.native.calls) == 3)
        shiboken6.delete(parent)
        self.assertEqual(sum(call[0] == 'close' for call in self.native.calls), 3)


if __name__ == '__main__':
    unittest.main()
