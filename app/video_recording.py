"""使用 Qt 采集放映画面，并按统一时间轴将配音写入 MP4。"""

from pathlib import Path
import wave

from PySide6.QtCore import QObject, QElapsedTimer, QPoint, QRect, QSizeF, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtMultimedia import (
    QAudioBuffer, QAudioBufferInput, QAudioDecoder, QAudioFormat,
    QMediaCaptureSession, QMediaFormat, QMediaRecorder, QVideoFrame,
    QVideoFrameInput, QVideoSink, QWindowCapture,
)

from app.video_settings import VideoRecordingOptions
from app.video_timeline import SAMPLE_RATE, TimelineAudio, VideoTimeline


def recording_format():
    """要求标准 MP4/H.264/AAC，缺少编码器时在开始前给出明确提示。"""
    media_format = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    media_format.setVideoCodec(QMediaFormat.VideoCodec.H264)
    media_format.setAudioCodec(QMediaFormat.AudioCodec.AAC)
    if not media_format.isSupported(QMediaFormat.ConversionMode.Encode):
        raise RuntimeError('当前环境不支持 MP4（H.264/AAC）录制，请使用完整发行包并检查显卡驱动。')
    return media_format


def pcm_format():
    result = QAudioFormat()
    result.setSampleRate(SAMPLE_RATE)
    result.setChannelCount(1)
    result.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return result


def configure_video_encoder(owner):
    """放映录制与静态合成共享 Qt 的音视频输入和编码参数。"""
    owner.session = QMediaCaptureSession(owner)
    owner.video_input = QVideoFrameInput(owner)
    # 由首个缓冲区确定音频格式，兼容 Windows AAC 编码器的初始化时序。
    owner.audio_input = QAudioBufferInput(owner)
    owner.video_input.readyToSendVideoFrame.connect(owner._tick)
    owner.audio_input.readyToSendAudioBuffer.connect(owner._tick)
    owner.session.setVideoFrameInput(owner.video_input)
    owner.session.setAudioBufferInput(owner.audio_input)
    owner.recorder = QMediaRecorder(owner)
    owner.session.setRecorder(owner.recorder)
    owner.recorder.setMediaFormat(recording_format())
    owner.recorder.setVideoResolution(owner.options.width, owner.options.height)
    owner.recorder.setVideoFrameRate(owner.options.fps)
    owner.recorder.setVideoBitRate(owner.options.video_bit_rate)
    owner.recorder.setAudioBitRate(owner.options.audio_bit_rate)
    owner.recorder.setAudioSampleRate(SAMPLE_RATE)
    owner.recorder.setAudioChannelCount(1)
    owner.recorder.setEncodingMode(QMediaRecorder.EncodingMode.AverageBitRateEncoding)
    owner.recorder.setOutputLocation(QUrl.fromLocalFile(str(owner.target.resolve())))
    owner.recorder.recorderStateChanged.connect(owner._recorder_state)
    owner.recorder.errorOccurred.connect(lambda _code, text: owner._fail('视频保存失败：' + text))


def compose_slide_image(image, slide_size, width=1920, height=1080):
    """按文稿尺寸去除全屏放映的留边，再保持页面比例写入视频画布。"""
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.black)
    # PowerPoint 把完整幻灯片居中适配全屏窗口，窗口的其余部分不是页面。
    # 根据页面比例确定区域，不按黑色像素裁切，保留文稿自身的黑色背景。
    source_size = slide_size.scaled(QSizeF(image.size()), Qt.AspectRatioMode.KeepAspectRatio).toSize()
    source = QRect(QPoint((image.width() - source_size.width()) // 2,
                         (image.height() - source_size.height()) // 2), source_size)
    target_size = slide_size.scaled(QSizeF(canvas.size()), Qt.AspectRatioMode.KeepAspectRatio).toSize()
    target = QRect(QPoint((canvas.width() - target_size.width()) // 2,
                         (canvas.height() - target_size.height()) // 2), target_size)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # 显式使用像素区域，避免高 DPI 图像的 devicePixelRatio 再次缩小画面。
    painter.drawImage(target, image, source)
    painter.end()
    return canvas


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
                raise RuntimeError('录制音频未能转换为所需格式。')
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
        self.cancel()
        self.failed.emit(message)


class VideoRecording(QObject):
    """实时采集最新画面，以选定帧率统一时间戳驱动画面、配音与 COM。"""

    progress = Signal(float, float, int, int)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, timeline, target, presentation, capture_window, parent=None, options=None):
        super().__init__(parent)
        self.timeline = timeline
        self.options = options or VideoRecordingOptions(fps=timeline.fps)
        if self.options.fps != timeline.fps:
            raise ValueError('视频帧率与录制时间轴不一致。')
        self.status_interval = max(1, round(timeline.fps / 5))
        self.target = Path(target)
        self.presentation = presentation
        self.audio = TimelineAudio(timeline)
        self.state = 'idle'
        self.failure = ''
        self.frame_index = 0
        self.cue_index = -1
        self.pending_audio = None
        self.latest_image = QImage()
        self.pending_frame = None
        self.clock = QElapsedTimer()
        self.capture = QWindowCapture(self)
        self.capture.setWindow(capture_window)
        self.sink = QVideoSink(self)
        self.capture_session = QMediaCaptureSession(self)
        self.capture_session.setWindowCapture(self.capture)
        self.capture_session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._capture_frame)
        self.capture.errorOccurred.connect(lambda _code, text: self._fail('画面采集失败：' + text))
        configure_video_encoder(self)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(lambda: self._fail('录制启动超时，请确认放映窗口正常显示。'))

    def start(self):
        if self.state != 'idle':
            return
        self.state = 'starting'
        try:
            self.presentation.begin(self.timeline.cues[0].page)
            self.capture.start()
            self.startup_timer.start(15000)
        except Exception as exc:
            self._fail(str(exc))

    def _capture_frame(self, frame):
        if self.state not in ('starting', 'recording') or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.latest_image = image
        if self.state == 'starting' and self.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
            self.recorder.record()

    def _recorder_state(self, state):
        if state == QMediaRecorder.RecorderState.RecordingState and self.state == 'starting':
            self.state = 'recording'
            self.timer.start()
        elif state == QMediaRecorder.RecorderState.StoppedState:
            if self.state == 'stopping':
                self._finish()
            elif self.state == 'recording':
                self._fail('录像意外停止，请检查输出目录和可用空间。')

    def _compose_frame(self):
        canvas = compose_slide_image(self.latest_image, self.presentation.slide_size,
                                     self.options.width, self.options.height)
        frame = QVideoFrame(canvas)
        frame.setStartTime(self.frame_index * 1000000 // self.timeline.fps)
        frame.setEndTime((self.frame_index + 1) * 1000000 // self.timeline.fps)
        return frame

    def _tick(self):
        if self.state != 'recording':
            return
        try:
            sample = self.frame_index * self.timeline.frames_per_tick
            if sample >= self.timeline.total_frames:
                self._stop('complete')
                return
            elapsed = self.clock.elapsed() if self.clock.isValid() else 0
            expected_ms = self.frame_index * 1000 / self.timeline.fps
            if elapsed < expected_ms:
                return
            if elapsed - expected_ms > 1000:
                raise RuntimeError('录制处理速度不足，音画同步已中断，请关闭其他高负载程序后重试。')
            while (self.cue_index + 1 < len(self.timeline.cues)
                   and self.timeline.cues[self.cue_index + 1].action_frame <= sample):
                previous = self.timeline.cues[self.cue_index] if self.cue_index >= 0 else None
                self.cue_index += 1
                cue = self.timeline.cues[self.cue_index]
                if previous is not None:
                    self.presentation.advance(cue.page, previous.page)
            cue = self.timeline.cues[max(0, self.cue_index)]
            if self.frame_index % self.status_interval == 0:
                self.presentation.verify(cue.page)
            if self.pending_frame is None:
                self.pending_frame = self._compose_frame()
            if self.pending_audio is None:
                pcm = self.audio.read(sample, self.timeline.frames_per_tick)
                self.pending_audio = QAudioBuffer(pcm, pcm_format(),
                                                  self.frame_index * 1000000 // self.timeline.fps)
            if self.pending_frame is not False:
                if not self.video_input.sendVideoFrame(self.pending_frame):
                    return
                self.pending_frame = False
            if not self.audio_input.sendAudioBuffer(self.pending_audio):
                return
            if not self.clock.isValid():
                self.clock.start()
                self.startup_timer.stop()
            self.pending_frame = None
            self.pending_audio = None
            self.frame_index += 1
            if self.frame_index % self.status_interval == 0:
                self.progress.emit(sample / SAMPLE_RATE, self.timeline.duration,
                                   cue.page, self.cue_index + 1)
        except Exception as exc:
            self._fail(str(exc))

    def cancel(self):
        if self.state in ('starting', 'recording'):
            self._stop('cancelled')

    def _fail(self, message):
        if self.state in ('done', 'stopping'):
            return
        self.failure = message
        self._stop('failed')

    def _stop(self, outcome):
        if self.state in ('done', 'stopping'):
            return
        self.outcome = outcome
        self.state = 'stopping'
        self.timer.stop()
        self.startup_timer.stop()
        self.capture.stop()
        self.audio.close()
        self.presentation.restore()
        self.recorder.stop()
        if self.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState and self.state == 'stopping':
            self._finish()

    def _finish(self):
        self.state = 'done'
        if self.outcome == 'complete' and not self.failure:
            if self.target.is_file() and self.target.stat().st_size > 0:
                self.finished.emit(str(self.target))
            else:
                self.failed.emit('录制结束但没有生成有效的视频文件。')
        elif self.failure:
            self.failed.emit(self.failure)
        else:
            self.cancelled.emit()
