"""静态视频的实际音画回读、文稿副本隔离及取消验证。"""

from array import array
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
from PySide6.QtGui import QImage, QPainter
from PySide6.QtMultimedia import QAudioDecoder, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QApplication

from app.slide_images import SlideRenderCancelled, export_slide_images
from app.video_composition import StaticVideoComposition
from app.video_mux import build_video_captions
from app.video_pixels import PreparedVideoFrame
from app.video_recording import pcm_format
from app.video_settings import VideoRecordingOptions
from app.video_timeline import SAMPLE_RATE, TimelineAudio, VideoTimeline


def write_audio(path, seconds, value=4000):
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(array('h', [value] * round(seconds * SAMPLE_RATE)).tobytes())


class StaticVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.loop = QEventLoop()
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.loop.quit)
        self.images = {}
        for page, color in ((1, 0xffcc3322), (3, 0xff2266cc)):
            image = QImage(640, 480, QImage.Format.Format_RGB32)
            image.fill(color)
            path = self.root / f'{page}.png'
            image.save(str(path))
            self.images[page] = path
        self.wavs = [self.root / f'{i}.wav' for i in range(3)]
        for path, seconds, level in zip(self.wavs, (.31, .37, .29), (4000, -6000, 8000)):
            write_audio(path, seconds, level)

    def tearDown(self):
        self.timeout.stop()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait(self):
        self.timeout.start(15000)
        self.loop.exec()
        self.timeout.stop()

    def timeline(self):
        return VideoTimeline([1, 1, 3], self.wavs, lead_ms=0, tail_ms=0)

    def test_static_timeline_has_no_animation_wait_and_preserves_segment_subtitles(self):
        timeline = self.timeline()
        self.assertEqual(timeline.cues[0].audio_frame, 0)
        for first, second in zip(timeline.cues, timeline.cues[1:]):
            self.assertEqual(second.audio_frame, first.end_frame)
        self.assertLess(timeline.duration - .97, 1 / timeline.fps)
        captions = build_video_captions([{'page': page, 'text': text}
            for page, text in zip((1, 1, 3), ('第一段', '第二段', '第三页'))], timeline)
        self.assertEqual([(c.start_ms, c.end_ms) for c in captions], [(0, 310), (310, 680), (680, 970)])
        audio = TimelineAudio(timeline)
        try:
            whole = array('h', b''.join(audio.read(i, timeline.frames_per_tick)
                for i in range(0, timeline.total_frames, timeline.frames_per_tick)))
        finally:
            audio.close()
        self.assertEqual(whole[0], 4000)
        self.assertEqual(whole[round(.31 * SAMPLE_RATE)], -6000)
        self.assertEqual(whole[round(.68 * SAMPLE_RATE)], 8000)

    def test_prepared_pixels_preserve_colors_and_independent_frame_timestamps(self):
        image = QImage(1280, 720, QImage.Format.Format_RGB32)
        image.fill(0xff000000)
        painter = QPainter(image)
        for x, color in ((0, 0xffffffff), (320, 0xffff0000), (640, 0xff00ff00), (960, 0xff0000ff)):
            painter.fillRect(x, 0, 320, 360, color)
        painter.end()
        prepared = PreparedVideoFrame(image, 30)
        first, second = prepared.frame(), prepared.frame()
        first.setStartTime(0)
        first.setEndTime(33333)
        second.setStartTime(33333)
        second.setEndTime(66666)
        self.assertEqual((first.startTime(), first.endTime()), (0, 33333))
        self.assertEqual((second.startTime(), second.endTime()), (33333, 66666))
        result = first.toImage()
        for x, y in ((160, 180), (480, 180), (800, 180), (1120, 180), (160, 540)):
            source, decoded = image.pixelColor(x, y), result.pixelColor(x, y)
            for channel in ('red', 'green', 'blue'):
                self.assertAlmostEqual(getattr(source, channel)(), getattr(decoded, channel)(), delta=3)
        # 修改后续帧的像素也不能改变已排队的帧。
        self.assertTrue(second.map(QVideoFrame.MapMode.WriteOnly))
        second.bits(0)[0] = 99
        second.unmap()
        self.assertTrue(first.map(QVideoFrame.MapMode.ReadOnly))
        self.assertEqual(first.bits(0)[0], 235)
        first.unmap()

    def test_missing_pixel_converter_falls_back_to_qt_images(self):
        encoder = StaticVideoComposition(self.timeline(), self.root / 'fallback.mp4', self.images,
                                         options=VideoRecordingOptions(1280, 720))
        with patch('app.video_composition.PreparedVideoFrame', side_effect=OSError('Library unavailable')) as prepare, \
             self.assertLogs('app.video_composition', level='WARNING'):
            first = encoder._frame(1)
            encoder.frame_index = 1
            second = encoder._frame(3)
        prepare.assert_called_once()
        self.assertTrue(first.isValid() and second.isValid())
        self.assertGreater(first.toImage().pixelColor(640, 360).red(), 150)
        self.assertGreater(second.toImage().pixelColor(640, 360).blue(), 150)
        encoder.audio.close()
        encoder.deleteLater()

    def test_offline_video_decodes_with_page_changes_and_aligned_audio(self):
        timeline = self.timeline()
        target = self.root / '静态.mp4'
        encoder = StaticVideoComposition(timeline, target, self.images,
                                         options=VideoRecordingOptions(1280, 720, 30, 3000000))
        done, errors = [], []
        encoder.finished.connect(lambda path: (done.append(path), self.loop.quit()))
        encoder.failed.connect(lambda message: (errors.append(message), self.loop.quit()))
        QTimer.singleShot(0, encoder.start)
        self.wait()
        if encoder.state != 'done':
            encoder.cancel()
        self.assertFalse(errors, errors)
        self.assertTrue(done, encoder.state)
        self.assertGreater(target.stat().st_size, 1000)
        encoder.deleteLater()

        decoder = QAudioDecoder()
        decoder.setAudioFormat(pcm_format())
        samples = array('h')
        decoder.bufferReady.connect(lambda: samples.extend(array('h', bytes(decoder.read().constData()))))
        decoder.error.connect(lambda _code: (errors.append(decoder.errorString()), self.loop.quit()))
        decoder.finished.connect(self.loop.quit)
        decoder.setSource(QUrl.fromLocalFile(str(target)))
        decoder.start()
        self.wait()
        decoder.stop()
        self.assertFalse(errors, errors)
        self.assertAlmostEqual(len(samples) / SAMPLE_RATE, timeline.duration, delta=.04)
        for position, sign in ((.12, 1), (.5, -1), (.82, 1)):
            sample = round(position * SAMPLE_RATE)
            self.assertGreater(sign * sum(samples[sample:sample + 480]) / 480, 1000)
        decoder.deleteLater()

        player, sink = QMediaPlayer(), QVideoSink()
        player.setVideoSink(sink)
        frames = []
        def frame_ready(frame):
            if frame.isValid():
                image = frame.toImage()
                frames.append((frame.startTime(), frame.width(), frame.height(),
                               image.pixelColor(640, 360), image.pixelColor(0, 360)))
        sink.videoFrameChanged.connect(frame_ready)
        player.mediaStatusChanged.connect(lambda status: self.loop.quit()
            if status == QMediaPlayer.MediaStatus.EndOfMedia else None)
        player.errorOccurred.connect(lambda _code, message: (errors.append(message), self.loop.quit()))
        player.setSource(QUrl.fromLocalFile(str(target)))
        player.play()
        self.wait()
        player.stop()
        self.assertFalse(errors, errors)
        red = [f for f in frames if f[3].red() > 150]
        blue = [f for f in frames if f[3].blue() > 150]
        self.assertTrue(red and blue)
        self.assertTrue(all((f[1], f[2]) == (1280, 720) for f in frames))
        self.assertTrue(all(f[4].red() < 10 and f[4].blue() < 10 for f in frames))
        self.assertGreaterEqual(min(f[0] for f in blue), 680000)
        self.assertLess(min(f[0] for f in blue), 720000)
        player.deleteLater()
        sink.deleteLater()

    def test_missing_page_is_rejected_and_corrupt_image_reports_failure(self):
        with self.assertRaises(ValueError):
            StaticVideoComposition(self.timeline(), self.root / 'bad.mp4', {1: self.images[1]})
        self.images[3].write_bytes(b'bad image')
        encoder = StaticVideoComposition(self.timeline(), self.root / 'bad-image.mp4', self.images,
                                         options=VideoRecordingOptions(1280, 720))
        errors = []
        encoder.failed.connect(lambda message: (errors.append(message), self.loop.quit()))
        encoder.start()
        self.wait()
        self.assertTrue(errors)
        self.assertIn('第 3 页', errors[0])
        self.assertEqual(encoder.state, 'done')
        encoder.deleteLater()

    def test_cancel_closes_encoder_and_audio_without_success(self):
        write_audio(self.wavs[0], 30)
        timeline = VideoTimeline([1], [self.wavs[0]], lead_ms=0, tail_ms=0)
        encoder = StaticVideoComposition(timeline, self.root / 'cancel.mp4', self.images,
                                         options=VideoRecordingOptions(1280, 720))
        done, cancelled = [], []
        encoder.finished.connect(done.append)
        encoder.cancelled.connect(lambda: (cancelled.append(True), self.loop.quit()))
        encoder.start()
        QTimer.singleShot(20, encoder.cancel)
        self.wait()
        self.assertTrue(cancelled)
        self.assertFalse(done)
        self.assertEqual(encoder.state, 'done')
        self.assertIsNone(encoder.audio.reader)
        encoder.deleteLater()

    def test_dialog_switches_modes_and_exports_static_video_with_subtitle_track(self):
        self._verify_dialog_export('mp4')

    def test_dialog_exports_mkv_with_subtitle_track(self):
        self._verify_dialog_export('mkv')

    def _verify_dialog_export(self, container):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        source = self.root / '课程.pptx'
        source.write_bytes(b'original PPT must survive')
        destination = self.root / f'最终.{container}'
        settings = AppSettings()
        settings.set('video_recording', VideoRecordingOptions(1280, 720, container=container,
                                                              embed_subtitles=True).to_dict())
        settings.set('video_export_mode', 'static')
        context = NS(app_settings=settings, config=Mock())
        notes = [{'page': page, 'text': text}
                 for page, text in zip((1, 1, 3), ('第一段', '第二段', '第三页'))]
        with patch('ui.dialogs.video_recording_dialog.list_presentations') as list_shows:
            dialog = VideoRecordingDialog(notes, self.wavs, source, ctx=context)
        list_shows.assert_not_called()
        self.assertEqual(dialog.mode, 'static')
        self.assertTrue(dialog.live_source_panel.isHidden())
        self.assertFalse(dialog.static_source_panel.isHidden())
        self.assertTrue(dialog.wait_panel.isHidden())
        self.assertTrue(dialog.start_button.isEnabled())
        original_cleanup = dialog._cleanup
        def cleanup():
            original_cleanup()
            self.loop.quit()
        with patch('app.slide_images.export_slide_images', return_value=self.images), \
             patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(destination), 'MP4')), \
             patch.object(dialog, '_cleanup', side_effect=cleanup):
            QTimer.singleShot(0, dialog.start)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertEqual(dialog.completed, str(destination))
        self.assertFalse(list(self.root.glob('.ppt-*')))
        self.assertEqual(source.read_bytes(), b'original PPT must survive')
        context.config.save_later.assert_called_once()
        self.assertEqual(settings.get('video_export_mode'), 'static')
        dialog.deleteLater()
        player = QMediaPlayer()
        player.mediaStatusChanged.connect(lambda status: self.loop.quit()
            if status == QMediaPlayer.MediaStatus.LoadedMedia else None)
        player.setSource(QUrl.fromLocalFile(str(destination)))
        self.wait()
        self.assertEqual(len(player.subtitleTracks()), 1)
        player.deleteLater()

    def test_cancel_while_rendering_waits_for_worker_and_preserves_destination(self):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        source = self.root / 'source.pptx'
        source.write_bytes(b'keep PPT')
        destination = self.root / 'existing.mp4'
        destination.write_bytes(b'keep old video')
        settings = AppSettings()
        settings.set('video_export_mode', 'static')
        context = NS(app_settings=settings, config=Mock())
        with patch('ui.dialogs.video_recording_dialog.list_presentations'):
            dialog = VideoRecordingDialog([{'page': 1}], [self.wavs[0]], source, ctx=context)
        def render(*args, **kwargs):
            kwargs['progress'](0, 1)
            while not kwargs['cancelled']():
                time.sleep(.01)
            raise SlideRenderCancelled()
        dialog.finished.connect(self.loop.quit)
        def cancel_when_rendering():
            if dialog.rendering is not None:
                self.assertFalse(dialog.mode_selector.isEnabled())
                self.assertFalse(dialog.slide_file_button.isEnabled())
                dialog.reject()
            elif dialog.busy:
                QTimer.singleShot(10, cancel_when_rendering)
        with patch('app.slide_images.export_slide_images', side_effect=render), \
             patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(destination), 'MP4')):
            QTimer.singleShot(0, dialog.start)
            QTimer.singleShot(10, cancel_when_rendering)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertIsNone(dialog.rendering)
        self.assertIsNone(dialog.audio_directory)
        self.assertEqual(destination.read_bytes(), b'keep old video')
        self.assertEqual(source.read_bytes(), b'keep PPT')
        self.assertFalse(list(self.root.glob('.ppt-*')))
        dialog.deleteLater()


class SlideImageIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / '原文稿.pptx'
        self.source.write_bytes(b'original PPT content')
        self.presentation = Mock()
        self.presentation.Slides.Count = 3
        self.presentation.PageSetup.SlideWidth = 720
        self.presentation.PageSetup.SlideHeight = 540
        self.application = Mock(AutomationSecurity=2)
        self.application.Presentations.Open.return_value = self.presentation
        self.application.Presentations.Count = 0
        self.calls = []
        def slide(page):
            def export(path, format_name, width, height):
                self.calls.append((page, format_name, width, height))
                image = QImage(width, height, QImage.Format.Format_RGB32)
                image.fill(0xff4477cc)
                image.save(path)
            return NS(Export=export)
        self.presentation.Slides.Item.side_effect = slide
        self.patches = [patch('win32com.client.GetActiveObject', side_effect=OSError('No running Office')),
                        patch('win32com.client.DispatchEx', return_value=self.application),
                        patch('win32com.client.dynamic.Dispatch', side_effect=lambda value: value),
                        patch('win32process.EnumProcesses', return_value=[123]),
                        patch('app.slide_images._application_pid', return_value=123)]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.directory.cleanup()

    def test_reads_copy_exports_only_used_pages_and_keeps_existing_application(self):
        images = export_slide_images(self.source, [1, 1, 3], self.root / 'work', 1920, 1080)
        self.assertEqual(set(images), {1, 3})
        self.assertEqual(self.calls, [(1, 'PNG', 1440, 1080), (3, 'PNG', 1440, 1080)])
        opened = self.application.Presentations.Open.call_args.args
        self.assertNotEqual(Path(opened[0]), self.source)
        self.assertEqual(opened[1:], (-1, 0, 0))
        self.assertEqual(self.source.read_bytes(), b'original PPT content')
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()
        self.presentation.Save.assert_not_called()
        self.presentation.SaveAs.assert_not_called()
        self.assertEqual(self.application.AutomationSecurity, 2)

    def test_out_of_range_page_closes_copy_without_exporting_or_saving(self):
        with self.assertRaisesRegex(ValueError, '只有 3 页'):
            export_slide_images(self.source, [4], self.root / 'work', 1280, 720)
        self.assertFalse(self.calls)
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()

    def test_cancel_during_export_closes_copy_and_preserves_original(self):
        stopped = [False]
        with self.assertRaises(SlideRenderCancelled):
            export_slide_images(self.source, [1, 3], self.root / 'work', 1280, 720,
                                progress=lambda index, total: stopped.__setitem__(0, True),
                                cancelled=lambda: stopped[0])
        self.assertEqual(len(self.calls), 1)
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'original PPT content')

    def test_only_new_empty_application_is_quit(self):
        with patch('app.slide_images._application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Quit.assert_called_once()

    def test_running_application_is_reused_and_never_quit(self):
        with patch('win32com.client.GetActiveObject', return_value=self.application), \
             patch('win32com.client.DispatchEx') as start_application, \
             patch('app.slide_images._application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        start_application.assert_not_called()
        self.application.Quit.assert_not_called()
        self.presentation.Close.assert_called_once()
        self.presentation.Save.assert_not_called()
        self.assertEqual(self.application.AutomationSecurity, 2)

    def test_running_wps_is_used_before_starting_powerpoint(self):
        def running(prog_id):
            if prog_id == 'Kwpp.Application':
                return self.application
            raise OSError('PowerPoint is not running')
        with patch('win32com.client.GetActiveObject', side_effect=running), \
             patch('win32com.client.DispatchEx') as start_application:
            images = export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        start_application.assert_not_called()
        self.application.Quit.assert_not_called()
        self.assertEqual(images[1].parent.name, 'Kwpp.Application')

    def test_new_application_with_another_document_is_kept(self):
        self.application.Presentations.Count = 1
        with patch('app.slide_images._application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Quit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
