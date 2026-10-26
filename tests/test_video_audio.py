"""共用视频时间轴与配音解码验证。"""

from array import array
from pathlib import Path
import tempfile
import unittest
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app.video.audio import RecordingAudioPreparation
from app.video.settings import VideoRecordingOptions
from app.video.timeline import FRAMES_PER_TICK, SAMPLE_RATE, SUPPORTED_FRAME_RATES, TimelineAudio, VideoTimeline


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
        self.assertEqual(options.video_bit_rate, 12000000)
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


class AudioPreparationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
