"""将静态幻灯片与讲稿配音离线编码为视频，无需窗口采集或实时播放。"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QElapsedTimer, QSizeF, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioBuffer, QMediaRecorder, QVideoFrame

from app.video_recording import compose_slide_image, configure_video_encoder, pcm_format
from app.video_pixels import PreparedVideoFrame
from app.video_settings import VideoRecordingOptions
from app.video_timeline import SAMPLE_RATE, TimelineAudio


class StaticVideoComposition(QObject):
    """按编码器的可用队列送入帧；每次短批量处理，保持界面可响应取消。"""

    progress = Signal(float, float, int, int)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, timeline, target, images, parent=None, options=None):
        super().__init__(parent)
        self.timeline = timeline
        self.options = options or VideoRecordingOptions(fps=timeline.fps)
        if self.options.fps != timeline.fps:
            raise ValueError('视频帧率与合成时间轴不一致。')
        self.images = {int(page): Path(path) for page, path in images.items()}
        if any(cue.page not in self.images for cue in timeline.cues):
            raise ValueError('讲稿页码没有对应的 PPT 画面。')
        self.target = Path(target)
        self.audio = TimelineAudio(timeline)
        self.state = 'idle'
        self.outcome = ''
        self.failure = ''
        self.frame_index = 0
        self.cue_index = 0
        self.image_page = None
        self.canvas = QImage()
        self.prepared_frame = None
        self.prepare_pixels = True
        self.pending_frame = None
        self.pending_audio = None
        self.video_ended = False
        self.audio_ended = False
        self.ticking = False
        self.progress_clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda: self._fail('视频编码超时，请检查输出目录和可用空间。'))
        configure_video_encoder(self)
        self.recorder.setAutoStop(True)

    def start(self):
        if self.state != 'idle':
            return
        self.state = 'starting'
        self.watchdog.start(30000)
        self.progress_clock.start()
        self.recorder.record()

    def _recorder_state(self, state):
        if state == QMediaRecorder.RecorderState.RecordingState and self.state == 'starting':
            self.state = 'encoding'
            self.timer.start()
        elif state == QMediaRecorder.RecorderState.StoppedState:
            if self.state == 'finishing':
                self.outcome = 'complete'
                self._finish()
            elif self.state == 'stopping':
                self._finish()
            elif self.state in ('starting', 'encoding'):
                self._fail('视频编码意外停止，请检查输出目录和可用空间。')

    def _frame(self, page):
        if self.image_page != page:
            image = QImage(str(self.images[page]))
            if image.isNull():
                raise RuntimeError(f'第 {page} 页的 PPT 画面无法读取。')
            self.canvas = compose_slide_image(image, QSizeF(image.size()),
                                             self.options.width, self.options.height)
            self.prepared_frame = None
            if self.prepare_pixels:
                try:
                    self.prepared_frame = PreparedVideoFrame(self.canvas, self.timeline.fps)
                except (OSError, AttributeError, RuntimeError):
                    self.prepare_pixels = False
                    logging.getLogger(__name__).warning('NV12 页面预转换不可用，改用 Qt 图像转换', exc_info=True)
            self.image_page = page
        frame = self.prepared_frame.frame() if self.prepared_frame else QVideoFrame(self.canvas)
        frame.setStartTime(self.frame_index * 1000000 // self.timeline.fps)
        frame.setEndTime((self.frame_index + 1) * 1000000 // self.timeline.fps)
        return frame

    def _tick(self):
        if self.state != 'encoding' or self.ticking:
            return
        self.ticking = True
        batch = QElapsedTimer()
        batch.start()
        try:
            for _ in range(16):
                sample = self.frame_index * self.timeline.frames_per_tick
                if sample >= self.timeline.total_frames:
                    self._end_streams()
                    return
                while (self.cue_index + 1 < len(self.timeline.cues)
                       and self.timeline.cues[self.cue_index + 1].action_frame <= sample):
                    self.cue_index += 1
                cue = self.timeline.cues[self.cue_index]
                if self.pending_frame is None:
                    self.pending_frame = self._frame(cue.page)
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
                self.pending_frame = None
                self.pending_audio = None
                self.frame_index += 1
                self.watchdog.start(30000)
                if self.progress_clock.elapsed() >= 100:
                    self.progress_clock.restart()
                    self.progress.emit(sample / SAMPLE_RATE, self.timeline.duration,
                                       cue.page, self.cue_index + 1)
                if batch.elapsed() >= 15:
                    return
        except Exception as exc:
            self._fail(str(exc))
        finally:
            self.ticking = False

    def _end_streams(self):
        # 空缓冲区标记两条流结束，等待编码器排空队列后再发布完整文件。
        if not self.video_ended:
            self.video_ended = self.video_input.sendVideoFrame(QVideoFrame())
        if not self.audio_ended:
            self.audio_ended = self.audio_input.sendAudioBuffer(QAudioBuffer())
        if self.video_ended and self.audio_ended:
            self.state = 'finishing'
            self.timer.stop()
            self.audio.close()
            cue = self.timeline.cues[-1]
            self.progress.emit(self.timeline.duration, self.timeline.duration,
                               cue.page, len(self.timeline.cues))
            self.watchdog.start(30000)

    def cancel(self):
        if self.state not in ('done', 'stopping', 'idle'):
            self._stop('cancelled')

    def _fail(self, message):
        if self.state not in ('done', 'stopping'):
            self.failure = message
            self._stop('failed')

    def _stop(self, outcome):
        self.outcome = outcome
        self.state = 'stopping'
        self.timer.stop()
        self.watchdog.stop()
        self.audio.close()
        self.recorder.stop()
        if self.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState and self.state == 'stopping':
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
        elif self.outcome == 'complete':
            if self.target.is_file() and self.target.stat().st_size > 0:
                self.finished.emit(str(self.target))
            else:
                self.failed.emit('合成结束但没有生成有效的视频文件。')
        else:
            self.cancelled.emit()
