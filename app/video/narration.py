"""将已有配音按采样时间轴编码为独立音轨，支持取消和流式读取。"""

from pathlib import Path

from PySide6.QtCore import QObject, QElapsedTimer, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder

from app.video.audio import pcm_format
from app.video.timeline import SAMPLE_RATE, TimelineAudio


AUDIO_BIT_RATE = 128000


class NarrationEncoder(QObject):
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(float)

    def __init__(self, timeline, target, parent=None):
        super().__init__(parent)
        self.timeline, self.target = timeline, Path(target)
        self.audio = TimelineAudio(timeline)
        self.cursor = 0
        self.pending = None
        self.state = 'idle'
        self.outcome = ''
        self.failure = ''
        self.ticking = False
        self.session = QMediaCaptureSession(self)
        self.input = QAudioBufferInput(self)
        self.recorder = QMediaRecorder(self)
        self.session.setAudioBufferInput(self.input)
        self.session.setRecorder(self.recorder)
        media_format = QMediaFormat(QMediaFormat.FileFormat.Mpeg4Audio)
        media_format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(media_format)
        self.recorder.setAudioSampleRate(SAMPLE_RATE)
        self.recorder.setAudioChannelCount(1)
        self.recorder.setEncodingMode(QMediaRecorder.EncodingMode.AverageBitRateEncoding)
        self.recorder.setAudioBitRate(AUDIO_BIT_RATE)
        self.recorder.setAutoStop(True)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.target.resolve())))
        self.recorder.recorderStateChanged.connect(self._recorder_state)
        self.recorder.errorOccurred.connect(lambda _code, text: self._fail('配音保存失败：' + text))
        self.input.readyToSendAudioBuffer.connect(self._tick)
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda: self._fail('配音保存超时，请检查输出目录和可用空间。'))

    def start(self):
        if self.state != 'idle':
            return
        if self.target.exists() and self.target.stat().st_size:
            self._fail('目标文件已存在，请更换保存位置或文件名。')
            return
        self.state = 'encoding'
        self.watchdog.start(30000)
        self.timer.start()
        self.recorder.record()

    def _recorder_state(self, state):
        if state == QMediaRecorder.RecorderState.StoppedState:
            if self.state in ('finishing', 'stopping'):
                self._finish()
        else:
            self._tick()

    def _tick(self):
        if self.ticking or self.state != 'encoding' or self.recorder.recorderState() != QMediaRecorder.RecorderState.RecordingState:
            return
        self.ticking = True
        batch = QElapsedTimer()
        batch.start()
        try:
            for _ in range(32):
                if self.cursor >= self.timeline.total_frames:
                    self.state = 'finishing'
                    if not self.input.sendAudioBuffer(QAudioBuffer()):
                        self.state = 'encoding'
                        return
                    self.timer.stop()
                    self.audio.close()
                    if self.state != 'done':
                        self.watchdog.start(30000)
                    return
                count = min(4800, self.timeline.total_frames - self.cursor)
                if self.pending is None:
                    pcm = self.audio.read(self.cursor, count)
                    self.pending = QAudioBuffer(pcm, pcm_format(), self.cursor * 1000000 // SAMPLE_RATE)
                if not self.input.sendAudioBuffer(self.pending):
                    return
                self.pending = None
                self.cursor += count
                self.watchdog.start(30000)
                if batch.elapsed() >= 15:
                    self.progress.emit(self.cursor / self.timeline.total_frames)
                    return
        except Exception as exc:
            self._fail(str(exc))
        finally:
            self.ticking = False

    def cancel(self):
        if self.state not in ('done', 'stopping'):
            self._stop('cancelled')

    def _fail(self, text):
        if self.state not in ('done', 'stopping'):
            self.failure = text
            self._stop('failed')

    def _stop(self, outcome):
        self.outcome = outcome
        self.state = 'stopping'
        self.timer.stop()
        self.watchdog.stop()
        self.audio.close()
        self.recorder.stop()
        if self.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
            self._finish()

    def _finish(self):
        if self.state == 'done':
            return
        self.state = 'done'
        self.timer.stop()
        self.watchdog.stop()
        self.audio.close()
        if self.failure:
            self.failed.emit(self.failure)
        elif self.outcome == 'cancelled':
            self.cancelled.emit()
        elif not self.target.is_file() or not self.target.stat().st_size:
            self.failed.emit('配音文件未能保存完整，请重新导出。')
        else:
            self.finished.emit(str(self.target))
