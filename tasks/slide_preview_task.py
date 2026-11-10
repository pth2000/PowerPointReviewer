"""在同一个 COM 线程中复用文稿连接，按需处理最新的页面请求。"""

from contextlib import ExitStack
from pathlib import Path
from threading import Condition

from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from PySide6.QtGui import QImage

from app import paths
from app.logging_setup import get_logger
from app.presentation.images import SlideRenderCancelled, SlideSourceChanged, open_slide_images
from app.workspaces import TemporaryWorkspace


def document_key(key):
    return (*key[:3], key[4])


class _PreviewRequests:
    def __init__(self, key):
        self.condition = Condition()
        self.latest = key
        self.pending = True
        self.stopped = False

    def request(self, key):
        with self.condition:
            self.latest = key
            self.pending = True
            self.condition.notify()

    def take(self, *, wait=True):
        with self.condition:
            if wait:
                self.condition.wait_for(lambda: self.pending or self.stopped)
            if self.stopped or not self.pending:
                return None
            self.pending = False
            return self.latest

    def cancelled(self, source_key=None):
        with self.condition:
            return self.stopped or (source_key is not None and document_key(self.latest) != source_key)

    def cancel(self):
        with self.condition:
            self.stopped = True
            self.condition.notify()

    def retry(self, key):
        with self.condition:
            if not self.pending and not self.stopped:
                self.latest = key
                self.pending = True
                self.condition.notify()


class _PreviewSignals(QObject):
    ready = Signal(object, QImage, str)
    phase = Signal(object, str)
    finished = Signal()

    def __init__(self, requests):
        super().__init__()
        self.requests = requests

    @Slot()
    def cancel(self):
        self.requests.cancel()


class SlidePreviewTask(QRunnable):
    def __init__(self, key):
        super().__init__()
        self.requests = _PreviewRequests(key)
        self.signals = _PreviewSignals(self.requests)

    def request(self, key):
        self.requests.request(key)

    def cancel(self):
        self.requests.cancel()

    def is_cancelled(self):
        return self.requests.cancelled()

    def run(self):
        resources = ExitStack()
        reader = source_key = None
        retried_key = None

        def close_source():
            try:
                resources.close()
            except Exception:
                get_logger().exception('PPT 页面预览临时文件清理失败')

        try:
            while (key := self.requests.take()) is not None:
                try:
                    if source_key != document_key(key):
                        close_source()
                        reader = source_key = None
                        self.signals.phase.emit(key, 'opening')
                        paths.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
                        folder = resources.enter_context(TemporaryWorkspace(
                            prefix='slide-', dir=paths.PREVIEW_DIR))
                        opening_key = document_key(key)
                        reader = resources.enter_context(open_slide_images(
                            Path(key[0]), folder, 1280, 720, preference=key[4],
                            cancelled=lambda opened=opening_key: self.requests.cancelled(opened),
                        ))
                        source_key = opening_key
                        # 打开大文稿期间可能已经换页，直接处理最后选择的页面。
                        key = self.requests.take(wait=False) or key
                        if document_key(key) != source_key:
                            self.requests.request(key)
                            continue
                    self.signals.phase.emit(key, 'rendering')
                    image = QImage(str(reader.export(key[3])))
                    if image.isNull():
                        raise RuntimeError('无法读取页面预览，请重试。')
                    if not self.requests.cancelled(source_key):
                        self.signals.ready.emit(key, image, '')
                        retried_key = None
                except SlideSourceChanged:
                    close_source()
                    reader = source_key = None
                    if retried_key != key:
                        retried_key = key
                        self.requests.retry(key)
                    else:
                        self.signals.ready.emit(key, QImage(), '文稿正在更新，请稍后单击重试。')
                except SlideRenderCancelled:
                    close_source()
                    reader = source_key = None
                except Exception:
                    get_logger().exception('PPT 页面预览生成失败')
                    close_source()
                    reader = source_key = None
                    self.signals.ready.emit(key, QImage(), '暂时无法预览，请确认演示软件能打开此文件。单击重试。')
        finally:
            close_source()
            self.signals.finished.emit()
            self.signals.deleteLater()
