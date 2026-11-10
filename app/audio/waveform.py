"""分块提取音频振幅摘要，不保留完整 PCM，也不生成临时音频。"""

import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
from shiboken6 import delete

from app.audio.processing import SILENCE_THRESHOLD


@dataclass(frozen=True)
class WaveformData:
    peaks: tuple[tuple[int, int], ...]
    duration_ms: int
    leading_ms: int
    trailing_ms: int


class _Envelope:
    def __init__(self, rate, channels, cancelled, limit=2048):
        self.rate, self.channels = rate, channels
        self.cancelled, self.limit = cancelled, limit
        self.peaks = []
        self.frames = self.used = 0
        self.bucket = 256
        self.low, self.high = 32767, -32768
        self.first = self.last = None

    def feed(self, content):
        if len(content) % (2 * self.channels):
            raise ValueError('音频采样不完整。')
        samples = array('h', content)
        if sys.byteorder != 'little':
            samples.byteswap()
        offset, frames = 0, len(samples) // self.channels
        while offset < frames:
            if self.cancelled():
                raise InterruptedError('已取消波形读取')
            count = min(frames - offset, self.bucket - self.used)
            block = samples[offset * self.channels:(offset + count) * self.channels]
            low, high = min(block), max(block)
            self.low, self.high = min(self.low, low), max(self.high, high)
            if low < -SILENCE_THRESHOLD or high > SILENCE_THRESHOLD:
                if self.first is None:
                    first = next(i for i, value in enumerate(block) if abs(value) > SILENCE_THRESHOLD)
                    self.first = self.frames + first // self.channels
                last = next(i for i, value in enumerate(reversed(block)) if abs(value) > SILENCE_THRESHOLD)
                self.last = self.frames + count - 1 - last // self.channels
            self.frames += count
            self.used += count
            offset += count
            if self.used == self.bucket:
                self.peaks.append((self.low, self.high))
                self.low, self.high, self.used = 32767, -32768, 0
                if len(self.peaks) == 2 * self.limit:
                    self.peaks = [(min(a[0], b[0]), max(a[1], b[1]))
                                  for a, b in zip(self.peaks[::2], self.peaks[1::2])]
                    self.bucket *= 2

    def finish(self):
        if self.cancelled():
            raise InterruptedError('已取消波形读取')
        if not self.frames:
            raise ValueError('这段音频为空。')
        if self.used:
            self.peaks.append((self.low, self.high))
        leading = self.frames if self.first is None else self.first
        trailing = 0 if self.last is None else self.frames - self.last - 1
        return WaveformData(tuple(self.peaks), round(self.frames * 1000 / self.rate),
                            round(leading * 1000 / self.rate), round(trailing * 1000 / self.rate))


def read_waveform(source, cancelled=lambda: False):
    """PCM16 WAV 直接读取，其余格式使用项目自带的 Qt 解码能力。"""
    source = Path(source)
    try:
        reader = wave.open(str(source), 'rb')
    except (wave.Error, EOFError):
        reader = None
    if reader is not None:
        with reader:
            if reader.getsampwidth() == 2 and reader.getnchannels() in (1, 2):
                result = _Envelope(reader.getframerate(), reader.getnchannels(), cancelled)
                while chunk := reader.readframes(16384):
                    result.feed(chunk)
                if result.frames != reader.getnframes():
                    raise ValueError('音频文件不完整。')
                return result.finish()
    return _decode_waveform(source, cancelled)


def _decode_waveform(source, cancelled):
    decoder = QAudioDecoder()
    audio_format = QAudioFormat()
    audio_format.setSampleRate(24000)
    audio_format.setChannelCount(2)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    decoder.setAudioFormat(audio_format)
    result = _Envelope(24000, 2, cancelled)
    loop, timeout, poll = QEventLoop(), QTimer(), QTimer()
    timeout.setSingleShot(True)
    errors, completed = [], []

    def fail(message):
        errors.append(message)
        loop.quit()

    def receive():
        try:
            buffer = decoder.read()
            if not buffer.isValid() or buffer.format() != audio_format:
                raise ValueError('无法读取音频采样。')
            result.feed(bytes(buffer.constData()))
        except Exception as exc:
            fail(str(exc))

    def finish():
        completed.append(True)
        loop.quit()

    decoder.bufferReady.connect(receive)
    decoder.finished.connect(finish)
    decoder.error.connect(lambda _code: fail(decoder.errorString()))
    timeout.timeout.connect(lambda: fail('波形读取超时。'))
    poll.timeout.connect(lambda: loop.quit() if cancelled() else None)
    try:
        if cancelled():
            raise InterruptedError('已取消波形读取')
        decoder.setSource(QUrl.fromLocalFile(str(source.resolve())))
        timeout.start(30000)
        poll.start(50)
        decoder.start()
        if not errors and not completed and not cancelled():
            loop.exec()
    finally:
        timeout.stop()
        poll.stop()
        decoder.stop()
        delete(decoder)
    if cancelled():
        raise InterruptedError('已取消波形读取')
    if errors or not completed:
        raise RuntimeError(errors[0] if errors else '无法读取音频波形。')
    return result.finish()
