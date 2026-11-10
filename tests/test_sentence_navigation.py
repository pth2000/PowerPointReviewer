"""分句定位、延迟跳转和不同音频来源的时间边界。"""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from qt_test_case import QtTestCase
from test_subtitle_sentences import wav_content

from app.audio.captions import write_captions
from app.audio.playback import SentencePlayback
from app.audio.sentences import load_sentences
from app.documents.statistics import build_statistics


class FakePlayer(QObject):
    sourceChanged = Signal(QUrl)
    positionChanged = Signal(int)
    mediaStatusChanged = Signal(object)
    seekableChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.url = QUrl()
        self.status = QMediaPlayer.MediaStatus.LoadingMedia
        self.seekable = False
        self.positions = []

    def source(self):
        return self.url

    def mediaStatus(self):
        return self.status

    def isSeekable(self):
        return self.seekable

    def set_source(self, path):
        self.url = QUrl.fromLocalFile(str(path)) if path else QUrl()
        self.sourceChanged.emit(self.url)

    def setPosition(self, value):
        self.positions.append(value)
        self.positionChanged.emit(value)


class SentenceNavigationTests(QtTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.audio = self.root / 'speech.wav'
        self.audio.write_bytes(
            write_captions(
                wav_content(),
                [
                    {'start': 4800, 'end': 28800, 'text': '第一句。'},
                    {'start': 36000, 'end': 48000, 'text': '第二句。'},
                ],
            )
        )

    def test_lookup_respects_gaps_and_end_boundary(self):
        timeline = load_sentences(self.audio, '第一句。第二句。')
        self.assertEqual(len(timeline), 2)
        for position in (-1, 0, 600, 700, 1000):
            self.assertIsNone(timeline.at(position))
        self.assertEqual(timeline.at(100).number, 1)
        self.assertEqual(timeline.at(750).number, 2)
        self.assertIsNone(timeline.sentence(0))
        self.assertIsNone(timeline.sentence(3))

    def test_cache_follows_audio_replacement_and_rejects_unmatched_text(self):
        self.assertFalse(load_sentences(self.audio, '另外的文字'))
        before = load_sentences(self.audio)
        self.audio.write_bytes(
            write_captions(wav_content(), [{'start': 0, 'end': 48000, 'text': '另一份配音。'}])
        )
        after = load_sentences(self.audio)
        self.assertEqual(len(before), 2)
        self.assertEqual(len(after), 1)
        self.assertEqual(after.spans[0].text, '另一份配音。')
        self.audio.write_bytes(wav_content())
        self.assertFalse(load_sentences(self.audio))

    def test_seek_waits_for_loaded_media_and_uses_the_selected_sentence(self):
        player = FakePlayer()
        tracker = SentencePlayback(player)
        player.set_source(self.audio)
        self.assertTrue(tracker.seek_to(2))
        self.assertEqual(player.positions, [])
        player.status = QMediaPlayer.MediaStatus.LoadedMedia
        player.seekable = True
        player.mediaStatusChanged.emit(player.status)
        self.assertEqual(player.positions, [750])
        self.assertEqual(tracker.current.number, 2)
        player.positionChanged.emit(650)
        self.assertIsNone(tracker.current)

    def test_changing_source_cancels_pending_seek_and_clears_old_sentences(self):
        player = FakePlayer()
        tracker = SentencePlayback(player)
        player.set_source(self.audio)
        tracker.seek_to(2)
        other = self.root / 'ordinary.wav'
        other.write_bytes(wav_content())
        player.set_source(other)
        player.status = QMediaPlayer.MediaStatus.LoadedMedia
        player.seekable = True
        player.seekableChanged.emit(True)
        self.assertFalse(tracker.timeline)
        self.assertEqual(player.positions, [])
        self.assertFalse(tracker.seek_to(1))

    def test_waveform_position_waits_for_loading_and_latest_drag_position_wins(self):
        player = FakePlayer()
        tracker = SentencePlayback(player)
        player.set_source(self.audio)
        tracker.seek_position(250)
        tracker.seek_position(425)
        self.assertEqual(player.positions, [])
        player.seekable = True
        player.status = QMediaPlayer.MediaStatus.BufferedMedia
        player.seekableChanged.emit(True)
        self.assertEqual(player.positions, [425])
        player.mediaStatusChanged.emit(player.status)
        self.assertEqual(player.positions, [425])

    def test_statistics_only_show_sentence_counts_when_every_segment_is_known(self):
        notes = {1: '第一段', 2: '第二段'}
        segments = [{'page': 1}, {'page': 2}]
        stats = build_statistics(notes, segments, [1, 2], sentence_counts=[2, 3])
        self.assertEqual(stats.sentences, 5)
        self.assertEqual([page.sentences for page in stats.pages], [2, 3])
        self.assertIsNone(
            build_statistics(notes, segments, [1, 2], sentence_counts=[2, None]).sentences
        )


if __name__ == '__main__':
    unittest.main()
