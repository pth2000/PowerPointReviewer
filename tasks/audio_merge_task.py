"""将不同格式的配音通过 Qt 统一为 WAV 后逐段合并。"""

from pathlib import Path
import tempfile
import wave

from PySide6.QtCore import QThread, Signal

from tasks.audio_review_task import decode_wav
from app.logging_setup import get_logger


class AudioMergeTask(QThread):
    progress = Signal(int, int)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, sources, target, parent=None):
        super().__init__(parent)
        self.sources = [Path(source) for source in sources]
        self.target = Path(target)

    def run(self):
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.audio-merge-', dir=self.target.parent) as folder:
                output = Path(folder) / 'complete.wav'
                with wave.open(str(output), 'wb') as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(48000)
                    for index, source in enumerate(self.sources):
                        if self.isInterruptionRequested():
                            return
                        decoded = Path(folder) / 'segment.wav'
                        decode_wav(source, decoded, self.isInterruptionRequested, normalize=True)
                        with wave.open(str(decoded), 'rb') as reader:
                            while pcm := reader.readframes(48000):
                                if self.isInterruptionRequested():
                                    return
                                writer.writeframesraw(pcm)
                        decoded.unlink()
                        self.progress.emit(index + 1, len(self.sources))
                if not self.isInterruptionRequested():
                    output.replace(self.target)
                    self.ready.emit()
        except InterruptedError:
            pass
        except Exception as exc:
            get_logger().exception('合并配音失败')
            self.failed.emit(str(exc))
