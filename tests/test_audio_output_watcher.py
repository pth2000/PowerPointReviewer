"""默认音频设备的轮询仅在播放期间运行。"""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer

from app.audio.playback import AudioOutputWatcher

from qt_test_case import QtTestCase


class Player(QObject):
    playbackStateChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.pause, self.play, self.setPosition = Mock(), Mock(), Mock()

    def playbackState(self):
        return self.state

    def position(self):
        return 150

    def change(self, state):
        self.state = state
        self.playbackStateChanged.emit(state)


class AudioOutputWatcherTests(QtTestCase):

    def setUp(self):
        self.device = NS(id=lambda: b'old', description=lambda: 'test output')
        self.default = patch('app.audio.playback.QMediaDevices.defaultAudioOutput',
                             side_effect=lambda: self.device)
        self.default.start()
        self.addCleanup(self.default.stop)
        self.player = Player()
        self.output = Mock()
        self.watcher = AudioOutputWatcher(self.player, self.output)
        self.addCleanup(self.watcher.deleteLater)
        self.addCleanup(self.watcher._timer.stop)

    def test_idle_paused_and_stopped_do_not_poll(self):
        self.assertFalse(self.watcher._timer.isActive())
        self.player.change(QMediaPlayer.PlaybackState.PlayingState)
        self.assertTrue(self.watcher._timer.isActive())
        self.player.change(QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.watcher._timer.isActive())
        self.player.change(QMediaPlayer.PlaybackState.PlayingState)
        self.player.change(QMediaPlayer.PlaybackState.StoppedState)
        self.assertFalse(self.watcher._timer.isActive())

    def test_device_changed_while_idle_is_checked_on_next_play(self):
        self.device = NS(id=lambda: b'new', description=lambda: 'new output')
        self.player.change(QMediaPlayer.PlaybackState.PlayingState)
        self.output.setDevice.assert_called_once_with(self.device)
        self.player.setPosition.assert_called_once_with(150)
        self.player.play.assert_called_once()

    def test_device_signal_still_works_without_an_active_timer(self):
        self.device = NS(id=lambda: b'new', description=lambda: 'new output')
        self.watcher._devices.audioOutputsChanged.emit()
        self.output.setDevice.assert_called_once_with(self.device)
        self.assertFalse(self.watcher._timer.isActive())
        self.player.play.assert_not_called()


if __name__ == '__main__':
    unittest.main()
