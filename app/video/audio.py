"""为视频导出逐段准备统一格式的配音。"""

from pathlib import Path
import wave

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

from app.video.timeline import SAMPLE_RATE
from app.logging_setup import log_error


def pcm_format():
    result = QAudioFormat()
    result.setSampleRate(SAMPLE_RATE)
    result.setChannelCount(1)
    result.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return result


class RecordingAudioPreparation(QObject):
    """用 Qt 逐段解码 WAV/MP3，统一采样率，支持取消和超时。"""

    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, sources, directory, parent=None):
        super().__init__(parent)
        self.sources = [Path(path) for path in sources]
        self.directory = Path(directory)
        self.outputs = []
        self.decoder = QAudioDecoder(self)
        self.decoder.setAudioFormat(pcm_format())
        self.decoder.bufferReady.connect(self._read)
        self.decoder.finished.connect(self._schedule_next)
        self.decoder.error.connect(lambda _error: self._fail(self.decoder.errorString()))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fail('音频解码超时，请检查音频文件。'))
        self.writer = None
        self.active = False
        self.decoding = False
        self.finishing_decode = False

    def start(self):
        self.active = True
        self._open_next()

    def _open_next(self):
        if not self.active:
            return
        index = len(self.outputs)
        if index == len(self.sources):
            self.active = False
            self.finished.emit(self.outputs)
            return
        try:
            source = self.sources[index]
            if not source.is_file() or source.stat().st_size == 0:
                raise RuntimeError(f'音频文件缺失：{source.name}')
            self.writer = wave.open(str(self.directory / f'{index}.wav'), 'wb')
            self.writer.setnchannels(1)
            self.writer.setsampwidth(2)
            self.writer.setframerate(SAMPLE_RATE)
            self.decoder.setSource(QUrl.fromLocalFile(str(source.resolve())))
            self.timer.start(60000)
            self.decoding = True
            self.decoder.start()
            self.progress.emit(index, len(self.sources))
        except Exception as exc:
            self._fail(str(exc))

    def _read(self):
        if not self.active or not self.decoding:
            return
        try:
            buffer = self.decoder.read()
            if not buffer.isValid() or buffer.format() != pcm_format():
                raise RuntimeError('无法准备视频配音，请重新生成配音后重试。')
            self.writer.writeframesraw(bytes(buffer.constData()))
        except Exception as exc:
            self._fail(str(exc))

    def _schedule_next(self):
        # read() 取走最后一个缓冲区时也可能发出 finished，延迟关闭 WAV，
        # 并忽略 stop()/setSource() 对已经结束的解码器产生的重复通知。
        if self.active and self.decoding and not self.finishing_decode:
            self.finishing_decode = True
            QTimer.singleShot(0, self._next)

    def _next(self):
        if not self.active or not self.decoding:
            return
        self.decoding = False
        self.timer.stop()
        self.writer.close()
        self.writer = None
        self.decoder.stop()
        self.finishing_decode = False
        self.outputs.append(self.directory / f'{len(self.outputs)}.wav')
        QTimer.singleShot(0, self._open_next)

    def cancel(self):
        self.active = False
        self.decoding = False
        self.timer.stop()
        self.decoder.stop()
        if self.writer is not None:
            self.writer.close()
            self.writer = None

    def _fail(self, message):
        if not self.active:
            return
        index = min(len(self.outputs), len(self.sources) - 1)
        source = self.sources[index].name if self.sources else '无音频'
        log_error(f'视频配音准备失败：第 {index + 1} 段（{source}）', message)
        self.cancel()
        self.failed.emit(message)
