"""在独立任务中准备片段候选配音，支持 Qt 解码与本地效果处理。"""

from pathlib import Path
import uuid
import wave

from PySide6.QtCore import QEventLoop, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

from app.audio import segments as segment_audio
from tasks.audio_generation_task import AudioGenerationTask


def decode_wav(source, target, cancelled=lambda: False, *, normalize=False):
    """优先直接使用 PCM WAV；其余格式交给随程序附带的 Qt 解码。"""
    source, target = Path(source), Path(target)
    if not normalize and source.suffix.lower() == '.wav':
        try:
            content = source.read_bytes()
            from app.audio.processing import _read_wav
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
    from app.audio.processing import _read_wav
    _read_wav(target.read_bytes())


class AudioReviewTask(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, jobs, directory, *, engine=None, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.directory = Path(directory)
        self.engine = engine.snapshot() if engine is not None else None

    def run(self):
        try:
            results = {}
            for count, job in enumerate(self.jobs, 1):
                if self.isInterruptionRequested():
                    return
                prefix = self.directory / uuid.uuid4().hex
                base = Path(job['base_path'])
                engine = job.get('engine') or self.engine
                if engine is not None:
                    engine.validate_generation_settings()
                    base = prefix.with_suffix('.source.' + engine.get_output_extension())
                    text = str(job['spoken_text'])
                    if text.strip():
                        engine.save_file(text, str(base))
                    else:
                        AudioGenerationTask._write_silence(base, base.suffix.lstrip('.'))
                decoded = prefix.with_suffix('.decoded.wav')
                decode_wav(base, decoded, self.isInterruptionRequested)
                if self.isInterruptionRequested():
                    return
                target = prefix.with_suffix('.processed.wav')
                target.write_bytes(segment_audio.process_wav(decoded.read_bytes(), job['settings']))
                duration = AudioGenerationTask.get_audio_duration(target)
                if duration <= 0:
                    raise RuntimeError('这段配音为空，请重试。')
                results[job['index']] = {**{key: value for key, value in job.items() if key != 'engine'},
                                         'path': target, 'base_path': base,
                                         'duration': duration,
                                         'base_duration': AudioGenerationTask.get_audio_duration(decoded)}
                self.progress.emit(count, len(self.jobs))
            if not self.isInterruptionRequested():
                self.ready.emit(results)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))
