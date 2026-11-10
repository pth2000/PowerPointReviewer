"""复用 Qt 解码和重采样配音，保留短句字幕的时间信息。"""

import threading
import wave
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
from shiboken6 import delete

from app.audio.captions import read_captions, transfer_captions
from app.audio.processing import _read_wav

_standalone_app = None
EDGE_SAMPLE_RATE = 24000


def decode_edge_wav(path):
    # 模块在 Edge 启用留白时加载；不需要音频设备，也不播放音频。
    global _standalone_app
    if QCoreApplication.instance() is None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('音频处理组件尚未初始化，请重启程序后重试。')
        _standalone_app = QCoreApplication([])

    loop = QEventLoop()
    decoder = QAudioDecoder(loop)
    timer = QTimer(loop)
    timer.setSingleShot(True)
    output_format = QAudioFormat()
    # Edge 服务的原始音频为 24 kHz 单声道；明确输出 PCM16。
    output_format.setSampleRate(EDGE_SAMPLE_RATE)
    output_format.setChannelCount(1)
    output_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    decoder.setAudioFormat(output_format)
    pcm = bytearray()
    errors = []

    def fail(message):
        errors.append(message)
        loop.quit()

    def receive():
        try:
            buffer = decoder.read()
            if not buffer.isValid() or buffer.format() != output_format:
                raise RuntimeError('无法处理这段配音，请重新生成后重试。')
            pcm.extend(bytes(buffer.constData()))
        except Exception as exc:
            fail(str(exc))

    def on_error(error):
        if error != QAudioDecoder.Error.NoError:
            fail(decoder.errorString())

    decoder.bufferReady.connect(receive)
    decoder.finished.connect(loop.quit)
    decoder.error.connect(on_error)
    timer.timeout.connect(lambda: fail('音频解码超时，请重试。'))
    try:
        decoder.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        timer.start(30000)
        decoder.start()
        if not errors:
            loop.exec()
        if errors:
            raise RuntimeError('Edge 配音处理失败：' + errors[0])
        if not pcm or len(pcm) % 2:
            raise RuntimeError('Edge 配音不完整，请重新生成后重试。')
        output = BytesIO()
        with wave.open(output, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(EDGE_SAMPLE_RATE)
            writer.writeframes(pcm)
        return output.getvalue()
    finally:
        timer.stop()
        decoder.stop()
        # 解码器、计时器和事件循环必须在创建它们的生成线程内销毁。
        delete(loop)


def decode_wav(source, target, cancelled=lambda: False, *, normalize=False):
    """优先直接使用 PCM WAV；其余格式交给随程序附带的 Qt 解码。"""
    source, target = Path(source), Path(target)
    if not normalize and source.suffix.lower() == '.wav':
        try:
            content = source.read_bytes()
            params, _pcm = _read_wav(content)
            if params.sampwidth == 2 and params.nchannels in (1, 2):
                target.write_bytes(content)
                return
        except (OSError, RuntimeError):
            pass
    decoder = QAudioDecoder()
    audio_format = QAudioFormat()
    audio_format.setSampleRate(48000)
    audio_format.setChannelCount(1)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    decoder.setAudioFormat(audio_format)
    loop, timer, poll = QEventLoop(), QTimer(), QTimer()
    timer.setSingleShot(True)
    errors, completed = [], []
    writer = wave.open(str(target), 'wb')
    writer.setnchannels(1)
    writer.setsampwidth(2)
    writer.setframerate(48000)

    def fail(message):
        errors.append(message)
        loop.quit()

    def read():
        buffer = decoder.read()
        if buffer.isValid() and buffer.format() == audio_format:
            writer.writeframesraw(bytes(buffer.constData()))
        else:
            fail('无法读取这段音频，请重新生成后再试。')

    def finish():
        completed.append(True)
        loop.quit()

    decoder.bufferReady.connect(read)
    decoder.finished.connect(finish)
    decoder.error.connect(lambda _code: fail(decoder.errorString()))
    timer.timeout.connect(lambda: fail('音频准备超时，请重试。'))
    poll.timeout.connect(lambda: loop.quit() if cancelled() else None)
    try:
        decoder.setSource(QUrl.fromLocalFile(str(source.resolve())))
        timer.start(60000)
        poll.start(100)
        decoder.start()
        if not errors and not completed and not cancelled():
            loop.exec()
    finally:
        timer.stop()
        poll.stop()
        decoder.stop()
        writer.close()
    if cancelled():
        raise InterruptedError('已取消')
    if errors or not completed:
        raise RuntimeError(errors[0] if errors else '无法准备这段音频。')
    content = target.read_bytes()
    _read_wav(content)
    if read_captions(source):
        target.write_bytes(transfer_captions(source, content))
