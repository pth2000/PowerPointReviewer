"""按实际经过时间显示倒数，用短提示音提示节拍。"""

from PySide6.QtCore import QElapsedTimer, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QSoundEffect

from app import paths
from app.logging_setup import get_logger

MIN_COUNTDOWN_SECONDS = 1
MAX_COUNTDOWN_SECONDS = 10


class PlaybackCountdown(QObject):
    changed = Signal(object)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remaining = None
        self.elapsed = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.sounds = {}

    def start(self, seconds):
        self.stop()
        self.seconds = min(MAX_COUNTDOWN_SECONDS, max(MIN_COUNTDOWN_SECONDS, int(seconds)))
        self.elapsed.start()
        self.remaining = self.seconds
        self.changed.emit(self.remaining)
        self._beep()
        self.timer.start(1000)

    def stop(self):
        self.timer.stop()
        for sound in self.sounds.values():
            sound.stop()
        if self.remaining is not None:
            self.remaining = None
            self.changed.emit(None)

    @Slot()
    def _tick(self):
        if self.remaining is None:
            return
        elapsed = self.elapsed.elapsed()
        remaining = max(0, self.seconds - elapsed // 1000)
        if not remaining:
            self.remaining = None
            self.changed.emit(None)
            self.finished.emit()
            return
        if remaining != self.remaining:
            self.remaining = remaining
            self.changed.emit(remaining)
            self._beep()
        self.timer.start(max(1, 1000 - elapsed % 1000))

    def _beep(self):
        if self.remaining is None:
            return
        filename = 'countdown-final.wav' if self.remaining == 1 else 'countdown.wav'
        if filename not in self.sounds:
            sound = QSoundEffect(self)
            sound.setVolume(0.4)
            sound.statusChanged.connect(self._sound_status_changed)
            sound.setSource(QUrl.fromLocalFile(str(paths.resource_root() / 'audio' / filename)))
            self.sounds[filename] = sound
        self.sounds[filename].play()

    @Slot()
    def _sound_status_changed(self):
        sound = self.sender()
        if sound is not None and sound.status() == QSoundEffect.Status.Error:
            get_logger().warning('倒计时提示音无法播放，继续显示数字倒计时')
