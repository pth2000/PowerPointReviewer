"""音频摘要保留峰值与声道，后台读取可取消且定位不改变原音频。"""

import sys
import tempfile
import time
import unittest
import wave
from array import array
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QThreadPool
from PySide6.QtTest import QSignalSpy, QTest
from qt_test_case import QtTestCase

from app.audio.waveform import read_waveform
from tasks.audio_generation_task import AudioGenerationTask
from ui.waveform import WaveformSlider


def write_wave(path, samples, *, rate=8000, channels=1):
    content = array('h', samples)
    if sys.byteorder != 'little':
        content.byteswap()
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(content.tobytes())
    return path


class WaveformDataTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'voice.wav'

    def test_peak_and_silence_boundaries_follow_audio_frames(self):
        write_wave(self.path, [0] * 800 + [10000, -6000] * 4000 + [0] * 1600)
        before = self.path.read_bytes()
        data = read_waveform(self.path)
        self.assertEqual((data.duration_ms, data.leading_ms, data.trailing_ms), (1300, 100, 200))
        self.assertEqual(max(high for _, high in data.peaks), 10000)
        self.assertEqual(min(low for low, _ in data.peaks), -6000)
        self.assertEqual(self.path.read_bytes(), before)

    def test_opposite_stereo_channels_do_not_cancel_out(self):
        write_wave(self.path, [12000, -12000] * 8000, channels=2)
        data = read_waveform(self.path)
        self.assertEqual(data.duration_ms, 1000)
        self.assertEqual(data.leading_ms, 0)
        self.assertEqual(data.trailing_ms, 0)
        self.assertEqual(data.peaks[0], (-12000, 12000))

    def test_long_audio_has_bounded_summary_and_keeps_brief_peaks(self):
        samples = [0] * 1_500_000
        samples[700_001], samples[1_200_007] = 30000, -25000
        write_wave(self.path, samples)
        data = read_waveform(self.path)
        self.assertLessEqual(len(data.peaks), 4096)
        self.assertEqual(max(high for _, high in data.peaks), 30000)
        self.assertEqual(min(low for low, _ in data.peaks), -25000)

    def test_silence_and_truncated_audio_are_distinguished(self):
        write_wave(self.path, [0] * 8000)
        data = read_waveform(self.path)
        self.assertEqual(data.duration_ms, 1000)
        self.assertTrue(all(low == high == 0 for low, high in data.peaks))
        self.path.write_bytes(self.path.read_bytes()[:-100])
        with self.assertRaises(ValueError):
            read_waveform(self.path)

    def test_cancellation_closes_source_file(self):
        write_wave(self.path, [1000] * 8000)
        with self.assertRaises(InterruptedError):
            read_waveform(self.path, cancelled=lambda: True)
        self.path.unlink()


class WaveformWidgetTests(QtTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.first = write_wave(self.root / 'first.wav', [8000, -8000] * 4000)
        self.second = write_wave(self.root / 'second.wav', [5000, -5000] * 8000)
        self.widget = WaveformSlider()
        self.widget.resize(640, 48)
        self.widget.show()
        self.qt.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.widget.close()
        self.widget.release()
        self.assertTrue(QThreadPool.globalInstance().waitForDone(3000))
        self.qt.processEvents()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_ready(self):
        deadline = time.monotonic() + 8
        while self.widget.loading or self.widget._task is not None:
            QTest.qWait(5)
            if time.monotonic() > deadline:
                self.fail('波形读取未完成')
        self.assertIsNotNone(self.widget.data)

    def test_selection_cache_and_file_replacement_follow_actual_audio(self):
        self.widget.set_source(self.first, 1000)
        self.wait_ready()
        data = self.widget.data
        self.widget.set_source(self.second, 2000)
        self.wait_ready()
        self.assertEqual(self.widget.data.duration_ms, 2000)
        with patch('ui.waveform.WaveformTask') as task:
            self.widget.set_source(self.first, 1000)
        task.assert_not_called()
        self.assertIs(self.widget.data, data)
        write_wave(self.first, [7000] * 24000)
        self.widget.set_source(self.first, 3000)
        self.wait_ready()
        self.assertEqual(self.widget.data.duration_ms, 3000)

    def test_quick_switch_cancels_old_work_and_ignores_stale_results(self):
        started = Event()

        def read(path, cancelled):
            if Path(path) == self.first:
                started.set()
                while not cancelled():
                    time.sleep(0.005)
                raise InterruptedError()
            return read_waveform(path, cancelled)

        with patch('tasks.waveform_task.read_waveform', side_effect=read):
            self.widget.set_source(self.first, 1000)
            self.assertTrue(started.wait(1))
            old = self.widget._desired
            self.widget.set_source(self.second, 2000)
            self.wait_ready()
        self.assertEqual(self.widget.data.duration_ms, 2000)
        self.widget._ready(old, read_waveform(self.first))
        self.assertEqual(self.widget.data.duration_ms, 2000)

    def test_mouse_and_keyboard_emit_seek_positions_and_keep_fixed_height(self):
        self.widget.set_source(self.first, 1000)
        self.wait_ready()
        moved = QSignalSpy(self.widget.sliderMoved)
        QTest.mouseClick(self.widget, Qt.MouseButton.LeftButton, pos=self.widget.rect().center())
        self.assertAlmostEqual(moved.at(moved.count() - 1)[0], 500, delta=2)
        QTest.keyClick(self.widget, Qt.Key.Key_End)
        self.assertEqual(moved.at(moved.count() - 1), [1000])
        QTest.keyClick(self.widget, Qt.Key.Key_Left)
        self.assertEqual(moved.at(moved.count() - 1), [900])
        self.widget.resize(1600, 300)
        self.assertEqual(self.widget.height(), 48)
        self.widget.grab()

    def test_close_releases_memory_and_background_reader(self):
        self.widget.set_source(self.first, 1000)
        self.wait_ready()
        self.assertTrue(self.widget._cache)
        self.widget.close()
        self.assertIsNone(self.widget.data)
        self.assertFalse(self.widget._cache)
        self.assertIsNone(self.widget._desired)

    def test_qt_decodes_mp3_without_creating_a_wav_copy(self):
        source = self.root / 'voice.mp3'
        AudioGenerationTask._write_silence(source, 'mp3')
        files = set(self.root.iterdir())
        self.widget.set_source(source)
        self.wait_ready()
        self.assertGreater(self.widget.data.duration_ms, 100)
        self.assertGreater(self.widget.maximum(), 100)
        self.assertEqual(set(self.root.iterdir()), files)
