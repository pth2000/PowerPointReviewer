"""封装测试用的短视频素材，独立于应用的导出实现。"""
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder, QVideoFrame, QVideoFrameInput

from app.video.audio import pcm_format
from app.video.timeline import SAMPLE_RATE, TimelineAudio


class VideoFixtureEncoder(QObject):
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(float, float, int, int)

    def __init__(self, timeline, target, image, parent=None):
        super().__init__(parent)
        self.timeline, self.target, self.image = timeline, Path(target), image
        self.audio = TimelineAudio(timeline)
        self.frame_index = self.sample_index = 0
        self.video_eos = self.audio_eos = self.ticking = False
        self.state = 'idle'
        self.failure = ''
        self.session = QMediaCaptureSession(self)
        self.video = QVideoFrameInput(self)
        self.sound = QAudioBufferInput(self)
        self.recorder = QMediaRecorder(self)
        self.session.setVideoFrameInput(self.video)
        self.session.setAudioBufferInput(self.sound)
        self.session.setRecorder(self.recorder)
        media = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
        media.setVideoCodec(QMediaFormat.VideoCodec.H264)
        media.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(media)
        self.recorder.setVideoResolution(image.size())
        self.recorder.setVideoFrameRate(timeline.fps)
        self.recorder.setAudioSampleRate(SAMPLE_RATE)
        self.recorder.setAudioChannelCount(1)
        self.recorder.setAutoStop(True)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.target)))
        self.recorder.recorderStateChanged.connect(self._state_changed)
        self.recorder.errorOccurred.connect(self._error)
        self.video.readyToSendVideoFrame.connect(self._tick)
        self.sound.readyToSendAudioBuffer.connect(self._tick)
        self.timer = QTimer(self)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._tick)

    def start(self):
        self.state = 'encoding'
        self.recorder.record()
        self.timer.start()

    def _tick(self):
        if self.ticking or self.state != 'encoding' or self.recorder.recorderState() != QMediaRecorder.RecorderState.RecordingState:
            return
        self.ticking = True
        try:
            if not self.video_eos:
                if self.frame_index * self.timeline.frames_per_tick >= self.timeline.total_frames:
                    self.video_eos = self.video.sendVideoFrame(QVideoFrame())
                else:
                    frame = QVideoFrame(self.image)
                    frame.setStartTime(self.frame_index * 1000000 // self.timeline.fps)
                    frame.setEndTime((self.frame_index + 1) * 1000000 // self.timeline.fps)
                    if self.video.sendVideoFrame(frame):
                        self.frame_index += 1
            if not self.audio_eos:
                if self.sample_index >= self.timeline.total_frames:
                    self.audio_eos = self.sound.sendAudioBuffer(QAudioBuffer())
                else:
                    count = min(self.timeline.frames_per_tick, self.timeline.total_frames - self.sample_index)
                    buffer = QAudioBuffer(self.audio.read(self.sample_index, count), pcm_format(), self.sample_index * 1000000 // SAMPLE_RATE)
                    if self.sound.sendAudioBuffer(buffer):
                        self.sample_index += count
            if self.video_eos and self.audio_eos:
                self.timer.stop()
        finally:
            self.ticking = False

    def _error(self, _code, text):
        self.failure = text
        self.recorder.stop()

    def _state_changed(self, state):
        if state != QMediaRecorder.RecorderState.StoppedState:
            return
        previous = self.state
        self.state = 'done'
        self.timer.stop()
        self.audio.close()
        if self.failure:
            self.failed.emit(self.failure)
        elif previous == 'cancelled':
            self.cancelled.emit()
        else:
            self.finished.emit(str(self.target))

    def cancel(self):
        if self.state != 'done':
            self.state = 'cancelled'
            self.recorder.stop()
