"""数字倒计时按实际时间推进，停止后不触发正文播放。"""

import wave
from unittest.mock import Mock, patch

from qt_test_case import QtTestCase

from app import paths
from app.audio.countdown import PlaybackCountdown


class CountdownTests(QtTestCase):
    def setUp(self):
        self.countdown = PlaybackCountdown()
        self.addCleanup(self.countdown.stop)
        self.elapsed_ms = 0
        self.countdown.elapsed = Mock()
        self.countdown.elapsed.start.side_effect = lambda: setattr(self, 'elapsed_ms', 0)
        self.countdown.elapsed.elapsed.side_effect = lambda: self.elapsed_ms
        self.countdown._beep = Mock()
        self.changed = []
        self.finished = Mock()
        self.countdown.changed.connect(self.changed.append)
        self.countdown.finished.connect(self.finished)

    def test_counts_then_finishes_once_without_generating_audio(self):
        self.countdown.start(5)
        for elapsed in range(1000, 6000, 1000):
            self.elapsed_ms = elapsed
            self.countdown._tick()
        self.assertEqual(self.changed, [5, 4, 3, 2, 1, None])
        self.assertEqual(self.countdown._beep.call_count, 5)
        self.finished.assert_called_once()

    def test_delayed_events_skip_elapsed_seconds_instead_of_extending_countdown(self):
        self.countdown.start(5)
        self.elapsed_ms = 2750
        self.countdown._tick()
        self.assertEqual(self.countdown.remaining, 3)
        self.assertEqual(self.countdown.timer.interval(), 250)
        self.elapsed_ms = 5100
        self.countdown._tick()
        self.assertEqual(self.changed, [5, 3, None])
        self.finished.assert_called_once()

    def test_stop_prevents_finish_and_restart_uses_full_duration(self):
        self.countdown.start(5)
        self.elapsed_ms = 2500
        self.countdown.stop()
        self.countdown._tick()
        self.finished.assert_not_called()
        self.assertFalse(self.countdown.timer.isActive())
        self.countdown.start(5)
        self.assertEqual(self.countdown.remaining, 5)
        self.assertEqual(self.elapsed_ms, 0)

    def test_zero_duration_is_clamped_to_one_second(self):
        self.countdown.start(0)
        self.assertEqual(self.countdown.remaining, 1)
        self.finished.assert_not_called()
        self.countdown._beep.assert_called_once()
        self.assertTrue(self.countdown.timer.isActive())
        self.elapsed_ms = 1000
        self.countdown._tick()
        self.finished.assert_called_once()

    def test_packaged_beep_is_a_short_pcm_wave(self):
        for filename in ('countdown.wav', 'countdown-final.wav'):
            with self.subTest(filename=filename):
                source = paths.resource_root() / 'audio' / filename
                with wave.open(str(source), 'rb') as reader:
                    self.assertEqual(reader.getcomptype(), 'NONE')
                    self.assertEqual(reader.getnchannels(), 1)
                    self.assertGreater(reader.getnframes(), 0)
                    self.assertLess(reader.getnframes() / reader.getframerate(), 0.2)
                self.assertLess(source.stat().st_size, 16 * 1024)


class CountdownSoundTests(QtTestCase):
    def setUp(self):
        self.countdown = PlaybackCountdown()
        self.addCleanup(self.countdown.stop)
        self.elapsed_ms = 0
        self.countdown.elapsed = Mock()
        self.countdown.elapsed.elapsed.side_effect = lambda: self.elapsed_ms
        self.heard = []
        self.sound, self.final = Mock(), Mock()
        for sound in (self.sound, self.final):
            sound.play.side_effect = lambda s=sound: self.heard.append((
                self.countdown.remaining,
                'final' if s.setSource.call_args.args[0].toLocalFile().endswith('countdown-final.wav') else 'normal'))
        patcher = patch('app.audio.countdown.QSoundEffect', side_effect=(self.sound, self.final))
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def test_duration_is_limited_to_ten_seconds_and_only_last_cue_changes(self):
        finished = Mock()
        self.countdown.finished.connect(finished)
        self.countdown.start(30)
        self.assertEqual(self.countdown.remaining, 10)
        for second in range(1, 11):
            self.elapsed_ms = second * 1000
            self.countdown._tick()
        self.assertEqual(self.heard, [(number, 'final' if number == 1 else 'normal')
                                     for number in range(10, 0, -1)])
        self.assertEqual(self.factory.call_count, 2)
        self.assertTrue(self.sound.setSource.call_args.args[0].toLocalFile().endswith('countdown.wav'))
        self.assertTrue(self.final.setSource.call_args.args[0].toLocalFile().endswith('countdown-final.wav'))
        finished.assert_called_once()
        self.countdown.stop()
        self.sound.stop.assert_called()
        self.final.stop.assert_called()

    def test_delayed_tick_plays_only_the_current_cue(self):
        self.countdown.start(10)
        self.elapsed_ms = 6500
        self.countdown._tick()
        self.assertEqual(self.heard, [(10, 'normal'), (4, 'normal')])

    def test_short_countdown_sounds_once_per_second(self):
        self.countdown.start(3)
        for second in range(1, 4):
            self.elapsed_ms = second * 1000
            self.countdown._tick()
        self.assertEqual(self.heard, [(3, 'normal'), (2, 'normal'), (1, 'final')])

    def test_one_second_countdown_uses_only_the_final_cue(self):
        self.countdown.start(1)
        self.assertEqual(self.heard, [(1, 'final')])
        self.assertTrue(self.sound.setSource.call_args.args[0].toLocalFile().endswith('countdown-final.wav'))
