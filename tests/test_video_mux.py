"""实际录制、封装、播放字幕及导出失败/取消保护验证。"""

from array import array
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QSizeF, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioDecoder, QCapturableWindow, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QApplication

from app.video_mux import MuxCancelled, VideoCaption, build_video_captions, mux_video
from app.video_recording import VideoRecording, pcm_format
from app.video_settings import VideoRecordingOptions
from app.video_timeline import SAMPLE_RATE, VideoTimeline


def write_wav(path, frames):
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(array('h', [4000] * frames).tobytes())


class Presentation:
    name = '字幕演示.pptx'
    slide_count = 2
    slide_size = QSizeF(960, 540)

    def matches_source(self, source): return True
    def capturable_window(self): return QCapturableWindow()
    def begin(self, page): pass
    def advance(self, page, previous): pass
    def verify(self, page): pass
    def restore(self): pass


class VideoMuxTests(unittest.TestCase):
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
        self.wav = self.root / '中文 配音.wav'
        write_wav(self.wav, SAMPLE_RATE // 2)
        self.image = QImage(640, 360, QImage.Format.Format_RGB32)
        self.image.fill(0xff334455)

    def tearDown(self):
        self.timeout.stop()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def wait(self):
        self.timeout.start(12000)
        self.loop.exec()
        self.timeout.stop()

    def record(self):
        target = self.root / '源 视频.mp4'
        timeline = VideoTimeline([1, 1], [self.wav, self.wav], wait_ms=100)
        recording = VideoRecording(timeline, target, Presentation(), QCapturableWindow(),
                                    options=VideoRecordingOptions(1280, 720))
        done, errors = [], []
        recording.finished.connect(lambda path: (done.append(path), self.loop.quit()))
        recording.failed.connect(lambda error: (errors.append(error), self.loop.quit()))
        with patch('app.video_recording.QWindowCapture.start',
                   lambda capture: recording._capture_frame(QVideoFrame(self.image))):
            QTimer.singleShot(0, recording.start)
            self.wait()
        if recording.state != 'done': recording.cancel()
        self.assertTrue(done, errors)
        self.assertFalse(errors)
        recording.deleteLater()
        self.application.processEvents()
        return target, timeline

    def audio_hash(self, path):
        decoder = QAudioDecoder()
        decoder.setAudioFormat(pcm_format())
        digest = hashlib.sha256()
        errors, done = [], []
        decoder.bufferReady.connect(lambda: digest.update(bytes(decoder.read().constData())))
        decoder.finished.connect(lambda: (done.append(True), self.loop.quit()))
        decoder.error.connect(lambda code: (errors.append(decoder.errorString()), self.loop.quit()))
        decoder.setSource(QUrl.fromLocalFile(str(path)))
        QTimer.singleShot(0, decoder.start)
        self.wait()
        decoder.stop()
        decoder.deleteLater()
        self.application.processEvents()
        self.assertTrue(done, errors)
        self.assertFalse(errors)
        return digest.hexdigest()

    def play(self, path, subtitle_count):
        player, sink = QMediaPlayer(), QVideoSink()
        player.setVideoSink(sink)
        captions, frames, errors, done = [], [], [], []
        sink.subtitleTextChanged.connect(lambda text: captions.append((player.position(), text)))
        sink.videoFrameChanged.connect(lambda frame: frames.append(frame.startTime()) if frame.isValid() else None)
        player.tracksChanged.connect(lambda: player.setActiveSubtitleTrack(0) if player.subtitleTracks() else None)
        player.mediaStatusChanged.connect(lambda status: (done.append(True), self.loop.quit())
            if status == QMediaPlayer.MediaStatus.EndOfMedia else None)
        player.errorOccurred.connect(lambda code, message: (errors.append(message), self.loop.quit()))
        player.setSource(QUrl.fromLocalFile(str(path)))
        QTimer.singleShot(0, player.play)
        self.wait()
        count = len(player.subtitleTracks())
        player.stop()
        player.setSource(QUrl())
        player.deleteLater()
        sink.deleteLater()
        self.application.processEvents()
        self.assertTrue(done, errors)
        self.assertFalse(errors)
        self.assertEqual(count, subtitle_count)
        self.assertGreater(len(frames), 30)
        return captions

    def test_captions_include_wait_and_blank_segments_without_drift(self):
        timeline = VideoTimeline([1, 1, 2], [self.wav] * 3, wait_ms=700)
        notes = [{'page': 1, 'text': '第一段'}, {'page': 1, 'text': '  '}, {'page': 2, 'text': '第二页\r\n正文'}]
        captions = build_video_captions(notes, timeline)
        self.assertEqual(captions, (VideoCaption(700, 1200, '第一段'), VideoCaption(3100, 3600, '第二页\n正文')))
        write_wav(self.wav, 4801)
        timeline = VideoTimeline([1] * 1000, [self.wav] * 1000)
        captions = build_video_captions([{'page': 1, 'text': '正文'}] * 1000, timeline)
        self.assertEqual(captions[-1].end_ms, round((24000 + 4801 * 1000) * 1000 / SAMPLE_RATE))

    def test_mp4_and_mkv_subtitles_play_and_audio_is_identical(self):
        source, timeline = self.record()
        source_hash = self.audio_hash(source)
        notes = [{'page': 1, 'text': '这是第一条中文字幕'}, {'page': 1, 'text': 'Second caption\n第二条字幕'}]
        captions = build_video_captions(notes, timeline)
        for container in ('mp4', 'mkv'):
            with self.subTest(container=container):
                target = self.root / ('字幕 视频.' + container)
                mux_video(source, target, container, captions)
                self.assertEqual(self.audio_hash(target), source_hash)
                shown = self.play(target, 1)
                for caption in captions:
                    matches = [(position, text) for position, text in shown if caption.text in text]
                    self.assertTrue(matches, shown)
                    self.assertAlmostEqual(matches[0][0], caption.start_ms, delta=120)
                self.assertTrue(any(not text and abs(position - captions[0].end_ms) <= 120
                                    for position, text in shown), shown)
        target = self.root / '无字幕.mkv'
        mux_video(source, target, 'mkv')
        self.assertEqual(self.audio_hash(target), source_hash)
        self.assertFalse(any(text for _, text in self.play(target, 0)))

    def test_cancel_validation_and_io_failure_preserve_source_and_destination(self):
        source, timeline = self.record()
        source_bytes = source.read_bytes()
        existing = self.root / '已有视频.mp4'
        existing.write_bytes(b'keep existing video')
        with self.assertRaises(RuntimeError): mux_video(source, existing)
        self.assertEqual(existing.read_bytes(), b'keep existing video')
        with self.assertRaises(ValueError): mux_video(source, source)
        with self.assertRaises(ValueError):
            mux_video(source, self.root / 'bad.mp4', captions=[VideoCaption(0, 100, '正文'), VideoCaption(50, 150, '重叠')])
        with self.assertRaises(ValueError):
            mux_video(source, self.root / 'bad.mp4', captions=[VideoCaption(0, 100, '坏\0字幕')])
        with self.assertRaises(RuntimeError): mux_video(source, self.root / '不存在/target.mkv', 'mkv')
        with self.assertRaises(MuxCancelled): mux_video(source, self.root / 'cancel.mp4', cancelled=lambda: True)
        cancel = [False]
        def progress(position, duration):
            if position >= 600000: cancel[0] = True
        with self.assertRaises(MuxCancelled):
            mux_video(source, self.root / 'cancel-during.mkv', 'mkv', cancelled=lambda: cancel[0], progress=progress)
        self.assertEqual(source.read_bytes(), source_bytes)

    def create_dialog(self, destination, container='mkv', subtitles=True):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        settings = AppSettings()
        settings.set('video_recording', VideoRecordingOptions(1280, 720, container=container,
                                                              embed_subtitles=subtitles).to_dict())
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[Presentation()]):
            dialog = VideoRecordingDialog([{'page': 1, 'text': '字幕内容'}], [self.wav],
                                          ctx=SimpleNamespace(app_settings=settings, config=Mock()))
        def capture(_capture): dialog.recording._capture_frame(QVideoFrame(self.image))
        return dialog, patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                             return_value=(str(destination), f'{container.upper()} 视频 (*.{container})')), \
               patch('app.video_recording.QWindowCapture.start', capture)

    def test_dialog_saves_subtitles_and_cleans_staging_files(self):
        destination = self.root / '最终视频.mkv'
        dialog, save, capture = self.create_dialog(destination)
        original_cleanup = dialog._cleanup
        def cleanup():
            original_cleanup()
            self.loop.quit()
        with save, capture, patch.object(dialog, '_cleanup', side_effect=cleanup):
            QTimer.singleShot(0, dialog.start)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertEqual(dialog.completed, str(destination))
        self.assertFalse(list(self.root.glob('.ppt-*')))
        dialog.deleteLater()
        shown = self.play(destination, 1)
        self.assertTrue(any('字幕内容' in text for _, text in shown))

    def test_dialog_mux_failure_preserves_existing_video(self):
        destination = self.root / '已有视频.mkv'
        destination.write_bytes(b'previous video')
        dialog, save, capture = self.create_dialog(destination)
        original_cleanup = dialog._cleanup
        def cleanup():
            original_cleanup()
            self.loop.quit()
        with save, capture, patch.object(dialog, '_cleanup', side_effect=cleanup), \
                patch('app.video_mux.mux_video', side_effect=RuntimeError('模拟磁盘写入失败')):
            QTimer.singleShot(0, dialog.start)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertIn('模拟磁盘写入失败', dialog.status.text())
        self.assertEqual(destination.read_bytes(), b'previous video')
        self.assertFalse(list(self.root.glob('.ppt-mux-*')))
        preserved = list(self.root.glob('.ppt-recording-*.mp4'))
        self.assertEqual(len(preserved), 1)
        self.assertGreater(preserved[0].stat().st_size, 1000)
        self.assertIn(str(preserved[0]), dialog.status.text())
        dialog.deleteLater()

    def test_dialog_cancels_mux_before_replacing_existing_video(self):
        destination = self.root / '保留视频.mkv'
        destination.write_bytes(b'previous video')
        dialog, save, capture = self.create_dialog(destination)
        dialog.finished.connect(self.loop.quit)
        # Hold the worker at a cancellable boundary, so cancellation cannot race a tiny file.
        def hold(source, target, container, captions, cancelled, progress):
            import time
            target.write_bytes(b'partial mux')
            while not cancelled(): time.sleep(0.005)
            raise MuxCancelled()
        def cancel_when_muxing():
            if dialog.muxing:
                dialog.reject()
            elif dialog.busy:
                QTimer.singleShot(10, cancel_when_muxing)
        with save, capture, patch('app.video_mux.mux_video', side_effect=hold):
            QTimer.singleShot(0, dialog.start)
            QTimer.singleShot(10, cancel_when_muxing)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertIsNone(dialog.muxing)
        self.assertEqual(destination.read_bytes(), b'previous video')
        self.assertFalse(list(self.root.glob('.ppt-*')))
        dialog.deleteLater()

    def test_final_save_failure_keeps_complete_mux_and_existing_destination(self):
        destination = self.root / '被占用的视频.mkv'
        destination.write_bytes(b'previous video')
        dialog, save, capture = self.create_dialog(destination)
        original_cleanup = dialog._cleanup
        def cleanup():
            original_cleanup()
            self.loop.quit()
        with save, capture, patch.object(dialog, '_cleanup', side_effect=cleanup), \
                patch('pathlib.Path.replace', side_effect=PermissionError('文件被占用')):
            QTimer.singleShot(0, dialog.start)
            self.wait()
        self.assertFalse(dialog.busy)
        self.assertEqual(destination.read_bytes(), b'previous video')
        preserved = list(self.root.glob('.ppt-mux-*.mkv'))
        self.assertEqual(len(preserved), 1)
        self.assertIn(str(preserved[0]), dialog.status.text())
        self.assertFalse(list(self.root.glob('.ppt-recording-*')))
        dialog.deleteLater()
        shown = self.play(preserved[0], 1)
        self.assertTrue(any('字幕内容' in text for _, text in shown))


if __name__ == '__main__':
    unittest.main()
