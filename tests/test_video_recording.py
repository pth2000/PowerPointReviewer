"""录制时间轴与 Qt 实际编码、解码回读验证。"""

from array import array
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QRect, QSizeF, QTimer, QUrl
from PySide6.QtGui import QImage, QPainter
from PySide6.QtMultimedia import QAudioDecoder, QCapturableWindow, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QApplication

from app.video_recording import RecordingAudioPreparation, VideoRecording, compose_slide_image, pcm_format
from app.video_settings import VideoRecordingOptions
from app.video_timeline import FRAMES_PER_TICK, SAMPLE_RATE, SUPPORTED_FRAME_RATES, TimelineAudio, VideoTimeline


def write_wav(path, frames, value=5000, sample_rate=SAMPLE_RATE):
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(array('h', [value] * frames).tobytes())


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_audio_crosses_clip_and_silence_boundaries(self):
        first, second = self.root / 'first.wav', self.root / 'second.wav'
        write_wav(first, 1200, 1000)
        write_wav(second, 2400, -2000)
        timeline = VideoTimeline([1, 2], [first, second], wait_ms=10)
        audio = TimelineAudio(timeline)
        try:
            whole = b''.join(audio.read(start, FRAMES_PER_TICK)
                             for start in range(0, timeline.total_frames, FRAMES_PER_TICK))
        finally:
            audio.close()
        samples = array('h', whole)
        self.assertEqual(len(samples), timeline.total_frames)
        for cue, value, count in zip(timeline.cues, (1000, -2000), (1200, 2400)):
            self.assertEqual(samples[cue.audio_frame:cue.end_frame], array('h', [value] * count))
        self.assertEqual(samples.count(0), timeline.total_frames - 3600)
        self.assertEqual(timeline.total_frames % FRAMES_PER_TICK, 0)

    def test_long_timeline_has_no_cumulative_rounding_drift(self):
        path = self.root / 'clip.wav'
        write_wav(path, 4801)
        timeline = VideoTimeline([1] * 1000, [path] * 1000, wait_ms=700)
        expected = 1000 * (4801 + 33600) + 24000
        self.assertLess(timeline.total_frames - expected, FRAMES_PER_TICK)
        self.assertGreaterEqual(timeline.total_frames, expected)

    def test_default_has_no_extra_wait_between_clicks_and_pages(self):
        path = self.root / 'clip.wav'
        write_wav(path, 4800)
        timeline = VideoTimeline([1, 1, 2], [path] * 3)
        self.assertEqual(timeline.cues[0].audio_frame, SAMPLE_RATE // 2)
        for previous, cue in zip(timeline.cues, timeline.cues[1:]):
            self.assertEqual(cue.action_frame, previous.end_frame)
            self.assertEqual(cue.audio_frame, cue.action_frame)

    def test_supported_frame_rates_preserve_audio_and_round_only_at_end(self):
        path = self.root / 'clip.wav'
        write_wav(path, 4801, value=2000)
        for fps in SUPPORTED_FRAME_RATES:
            with self.subTest(fps=fps):
                timeline = VideoTimeline([1] * 10, [path] * 10, fps=fps)
                expected = SAMPLE_RATE + 4801 * 10
                self.assertGreaterEqual(timeline.total_frames, expected)
                self.assertLess(timeline.total_frames - expected, timeline.frames_per_tick)
                audio = TimelineAudio(timeline)
                try:
                    samples = array('h', b''.join(audio.read(start, timeline.frames_per_tick)
                                    for start in range(0, timeline.total_frames, timeline.frames_per_tick)))
                finally:
                    audio.close()
                self.assertEqual(samples.count(2000), 4801 * 10)

    def test_saved_options_reject_invalid_fields_and_keep_valid_fields(self):
        options = VideoRecordingOptions.from_dict({'width': 1280, 'height': 720,
            'fps': 59, 'video_bit_rate': 12000000, 'audio_bit_rate': 'bad'})
        self.assertEqual((options.width, options.height, options.fps), (1280, 720, 30))
        self.assertEqual((options.video_bit_rate, options.audio_bit_rate), (12000000, 128000))
        self.assertEqual(VideoRecordingOptions.from_dict(options.to_dict()), options)
        self.assertEqual(VideoRecordingOptions.from_dict({'container': 'avi', 'embed_subtitles': 'false'}),
                         VideoRecordingOptions())

    def test_invalid_pages_and_audio_are_rejected(self):
        path = self.root / 'clip.wav'
        write_wav(path, 100)
        for pages, sources in (([], []), ([2, 1], [path, path]), ([0], [path]), ([1, 2], [path])):
            with self.assertRaises(ValueError):
                VideoTimeline(pages, sources)
        write_wav(path, 100, sample_rate=22050)
        with self.assertRaises(ValueError):
            VideoTimeline([1], [path])


class QtRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.loop = QEventLoop()
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.loop.quit)

    def tearDown(self):
        self.timeout.stop()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait(self):
        self.timeout.start(15000)
        self.loop.exec()
        self.timeout.stop()

    def test_widescreen_slide_fills_video_on_square_monitor_and_high_dpi(self):
        image = QImage(1280, 1024, QImage.Format.Format_RGB32)
        image.fill(0xff000000)
        painter = QPainter(image)
        painter.fillRect(QRect(0, 152, 1280, 720), 0xffcc7744)
        painter.end()
        image.setDevicePixelRatio(1.5)
        output = compose_slide_image(image, QSizeF(960, 540))
        self.assertEqual((output.width(), output.height()), (1920, 1080))
        for x, y in ((0, 0), (1919, 0), (0, 1079), (1919, 1079), (960, 540)):
            self.assertEqual(output.pixelColor(x, y).name(), '#cc7744')

    def test_standard_slide_has_only_required_side_bars(self):
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0xff000000)
        painter = QPainter(image)
        painter.fillRect(QRect(240, 0, 1440, 1080), 0xff4466cc)
        painter.end()
        output = compose_slide_image(image, QSizeF(720, 540))
        for x, y in ((240, 0), (1679, 0), (240, 1079), (1679, 1079)):
            self.assertEqual(output.pixelColor(x, y).name(), '#4466cc')
        for x, y in ((239, 540), (1680, 540)):
            self.assertEqual(output.pixelColor(x, y).name(), '#000000')

    def test_black_slide_background_is_preserved(self):
        image = QImage(1280, 1024, QImage.Format.Format_RGB32)
        image.fill(0xff000000)
        painter = QPainter(image)
        painter.fillRect(QRect(600, 472, 80, 80), 0xff4466cc)
        painter.end()
        output = compose_slide_image(image, QSizeF(960, 540))
        self.assertEqual(output.pixelColor(960, 540).name(), '#4466cc')
        for x, y in ((0, 0), (1919, 1079), (880, 540), (1040, 540)):
            self.assertEqual(output.pixelColor(x, y).name(), '#000000')

    def test_decodes_different_sample_rates(self):
        inputs = [self.root / 'one.wav', self.root / 'two.wav']
        write_wav(inputs[0], 22050, sample_rate=22050)
        write_wav(inputs[1], 24000, sample_rate=24000)
        result, errors = [], []
        preparation = RecordingAudioPreparation(inputs, self.root)
        preparation.finished.connect(lambda paths: (result.extend(paths), self.loop.quit()))
        preparation.failed.connect(lambda text: (errors.append(text), self.loop.quit()))
        QTimer.singleShot(0, preparation.start)
        self.wait()
        preparation.cancel()
        self.assertFalse(errors, errors)
        self.assertEqual(len(result), 2)
        for path in result:
            with wave.open(str(path), 'rb') as reader:
                self.assertEqual(reader.getframerate(), SAMPLE_RATE)
                self.assertAlmostEqual(reader.getnframes() / SAMPLE_RATE, 1.0, places=2)
        preparation.deleteLater()

    def test_prepares_mp3_audio(self):
        from tasks.audio_generation_task import AudioGenerationTask
        source = self.root / 'edge.mp3'
        AudioGenerationTask._write_silence(source, 'mp3', seconds=0.8)
        result, errors = [], []
        preparation = RecordingAudioPreparation([source], self.root)
        preparation.finished.connect(lambda paths: (result.extend(paths), self.loop.quit()))
        preparation.failed.connect(lambda text: (errors.append(text), self.loop.quit()))
        QTimer.singleShot(0, preparation.start)
        self.wait()
        preparation.cancel()
        self.assertFalse(errors, errors)
        self.assertEqual(len(result), 1)
        with wave.open(str(result[0]), 'rb') as reader:
            self.assertEqual(reader.getframerate(), SAMPLE_RATE)
            self.assertAlmostEqual(reader.getnframes() / SAMPLE_RATE, 0.8, delta=0.08)
        preparation.deleteLater()

    def test_records_mp4_with_audio_and_expected_clicks(self):
        self._verify_recorded_video(VideoRecordingOptions())

    def test_records_custom_resolution_and_frame_rates(self):
        options = (VideoRecordingOptions(1280, 720, 24, 3000000, 96000),
                   VideoRecordingOptions(1280, 720, 60, 3000000, 96000),
                   VideoRecordingOptions(3840, 2160, 30, 24000000, 192000))
        for option in options:
            with self.subTest(width=option.width, fps=option.fps):
                self._verify_recorded_video(option)

    def _verify_recorded_video(self, options):
        first, second = self.root / 'one.wav', self.root / 'two.wav'
        write_wav(first, 9600)
        write_wav(second, 14400, -5000)
        timeline = VideoTimeline([1, 1], [first, second], wait_ms=50, fps=options.fps)
        target = self.root / f'video-{options.fps}.mp4'

        class Presentation:
            slide_size = QSizeF(720, 540)

            def __init__(self):
                self.actions = []
                self.restored = False

            def begin(self, page):
                self.actions.append(('begin', page))

            def advance(self, page, previous):
                self.actions.append(('next', page, previous))

            def verify(self, page):
                pass

            def restore(self):
                self.restored = True

        presentation = Presentation()
        recorder = VideoRecording(timeline, target, presentation, QCapturableWindow(), options=options)
        self.assertEqual(recorder.recorder.videoBitRate(), options.video_bit_rate)
        self.assertEqual(recorder.recorder.audioBitRate(), options.audio_bit_rate)
        image = QImage(640, 480, QImage.Format.Format_RGB32)
        image.fill(0xffcc8844)
        done, errors = [], []
        recorder.finished.connect(lambda path: (done.append(path), self.loop.quit()))
        recorder.failed.connect(lambda text: (errors.append(text), self.loop.quit()))
        with patch.object(type(recorder.capture), 'start', lambda _capture: recorder._capture_frame(QVideoFrame(image))):
            QTimer.singleShot(0, recorder.start)
            self.wait()
        if recorder.state != 'done':
            recorder.cancel()
        self.assertFalse(errors, errors)
        self.assertTrue(done, f'录制没有结束：frame={recorder.frame_index}, '
                            f'state={recorder.state}, duration={recorder.recorder.duration()}, '
                            f'pending_video={recorder.pending_frame is False}')
        self.assertGreater(target.stat().st_size, 1000)
        self.assertTrue(presentation.restored)
        self.assertEqual(presentation.actions, [('begin', 1), ('next', 1, 1)])
        # 通过 Qt 解码最终 MP4 中的音频，验证复用、编码、封装并非仅生成空文件。
        decoder = QAudioDecoder()
        decoder.setAudioFormat(pcm_format())
        frames = []
        levels = []
        def read():
            buffer = decoder.read()
            frames.append(buffer.frameCount())
            levels.extend(array('h', bytes(buffer.constData())))
        decoder.bufferReady.connect(read)
        decoder.finished.connect(self.loop.quit)
        decoder.error.connect(lambda _error: (errors.append(decoder.errorString()), self.loop.quit()))
        decoder.setSource(QUrl.fromLocalFile(str(target)))
        QTimer.singleShot(0, decoder.start)
        self.wait()
        decoder.stop()
        self.assertFalse(errors, errors)
        self.assertAlmostEqual(sum(frames) / SAMPLE_RATE, timeline.duration, delta=0.1)
        self.assertTrue(any(abs(value) > 1000 for value in levels))
        # 回读视频帧尺寸和时间戳，确保参数确实写入产物而非仅显示在界面。
        player = QMediaPlayer()
        sink = QVideoSink()
        player.setVideoSink(sink)
        video_frames = []
        def read_video(frame):
            if frame.isValid():
                video_frames.append((frame.width(), frame.height(), frame.startTime()))
                if len(video_frames) >= 2:
                    self.loop.quit()
        sink.videoFrameChanged.connect(read_video)
        player.errorOccurred.connect(lambda _code, message: (errors.append(message), self.loop.quit()))
        player.setSource(QUrl.fromLocalFile(str(target)))
        QTimer.singleShot(0, player.play)
        self.wait()
        player.stop()
        player.setSource(QUrl())
        self.assertFalse(errors, errors)
        self.assertGreaterEqual(len(video_frames), 2)
        self.assertEqual(video_frames[0][:2], (options.width, options.height))
        self.assertAlmostEqual(video_frames[1][2] - video_frames[0][2], 1000000 / options.fps, delta=2)
        recorder.deleteLater()
        decoder.deleteLater()
        player.deleteLater()
        sink.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_advanced_options_restore_reset_and_lock_during_recording(self):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        settings = AppSettings()
        options = VideoRecordingOptions(2560, 1440, 50, 16000000, 192000, 'mkv', True)
        settings.set('video_recording', options.to_dict())
        ctx = SimpleNamespace(app_settings=settings)
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[]):
            dialog = VideoRecordingDialog([], [], ctx=ctx)
        self.assertTrue(dialog.advanced_panel.isHidden())
        self.assertEqual(dialog.selected_options(), options)
        dialog.advanced_button.setChecked(True)
        self.assertFalse(dialog.advanced_panel.isHidden())
        dialog._set_busy(True)
        self.assertFalse(dialog.resolution_combo.isEnabled())
        self.assertFalse(dialog.container_combo.isEnabled())
        self.assertFalse(dialog.subtitle_checkbox.isEnabled())
        dialog._set_busy(False)
        dialog.reset_options_button.click()
        self.assertEqual(dialog.selected_options(), VideoRecordingOptions())
        dialog.deleteLater()

    def test_cancelled_dialog_preserves_existing_destination(self):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        source = self.root / 'voice.wav'
        destination = self.root / 'existing.mp4'
        write_wav(source, SAMPLE_RATE * 10)
        destination.write_bytes(b'existing video must survive cancellation')

        class Presentation:
            name = 'test.pptx'
            slide_count = 1
            slide_size = QSizeF(720, 540)
            restored = False

            def matches_source(self, source):
                return True

            def capturable_window(self):
                return QCapturableWindow()

            def begin(self, page):
                pass

            def verify(self, page):
                pass

            def restore(self):
                self.restored = True

        presentation = Presentation()
        settings = AppSettings()
        ctx = SimpleNamespace(app_settings=settings, config=Mock())
        image = QImage(640, 480, QImage.Format.Format_RGB32)
        image.fill(0xff445566)
        def capture(_capture):
            dialog.recording._capture_frame(QVideoFrame(image))

        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[presentation]):
            dialog = VideoRecordingDialog([{'page': 1}], [source], ctx=ctx)
        self.assertFalse(dialog.wait_checkbox.isChecked())
        self.assertFalse(dialog.wait_spin.isEnabled())
        dialog.wait_checkbox.setChecked(True)
        self.assertTrue(dialog.wait_spin.isEnabled())
        dialog.wait_checkbox.setChecked(False)
        dialog.finished.connect(self.loop.quit)
        with patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(destination), 'MP4 视频 (*.mp4)')):
            with patch('app.video_recording.QWindowCapture.start', capture):
                QTimer.singleShot(0, dialog.start)
                def cancel_when_running():
                    if dialog.recording and dialog.recording.frame_index > 3:
                        self.assertEqual(dialog.recording.timeline.cues[0].audio_frame, SAMPLE_RATE // 2)
                        dialog.reject()
                    elif dialog.busy:
                        QTimer.singleShot(25, cancel_when_running)
                QTimer.singleShot(25, cancel_when_running)
                self.wait()
        self.assertFalse(dialog.busy)
        self.assertTrue(presentation.restored)
        self.assertEqual(settings.get('video_recording'), VideoRecordingOptions().to_dict())
        ctx.config.save_later.assert_called_once()
        self.assertEqual(destination.read_bytes(), b'existing video must survive cancellation')
        self.assertFalse(list(self.root.glob('.ppt-recording-*.mp4')))
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
