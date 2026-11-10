"""在线程池中读取波形，取消后及时释放源文件。"""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from app.audio.waveform import read_waveform
from app.logging_setup import get_logger


class _WaveformSignals(QObject):
    ready = Signal(object, object)
    failed = Signal(object, str)
    finished = Signal()

    def __init__(self, cancelled):
        super().__init__()
        self.cancelled = cancelled

    @Slot()
    def cancel(self):
        self.cancelled.set()


class WaveformTask(QRunnable):
    def __init__(self, key):
        super().__init__()
        self.key = key
        self.cancelled = Event()
        self.signals = _WaveformSignals(self.cancelled)

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            data = read_waveform(self.key[0], self.cancelled.is_set)
            stat = Path(self.key[0]).stat()
            if (stat.st_mtime_ns, stat.st_size) != self.key[1:]:
                raise RuntimeError('配音文件已更新，请重新选择片段。')
            if not self.cancelled.is_set():
                self.signals.ready.emit(self.key, data)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.cancelled.is_set():
                get_logger().exception('读取音频波形失败：%s', self.key[0])
                self.signals.failed.emit(self.key, str(exc))
        finally:
            self.signals.finished.emit()
