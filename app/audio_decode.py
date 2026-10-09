"""复用 Qt 解码 Edge MP3，得到可补留白、合并导出的 PCM WAV。"""

from io import BytesIO
from pathlib import Path
import threading
import wave


_standalone_app = None
EDGE_SAMPLE_RATE = 24000


def decode_edge_wav(path):
    # 只在 Edge 启用留白时加载；不需要音频设备，也不播放音频。
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
    from shiboken6 import delete

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
                raise RuntimeError('Qt 未返回所需的 PCM 音频格式。')
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
            raise RuntimeError('Edge 音频解码失败：' + errors[0])
        if not pcm or len(pcm) % 2:
            raise RuntimeError('Edge 音频解码失败：未返回完整音频。')
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
