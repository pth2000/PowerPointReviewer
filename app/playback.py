"""提供默认音频设备跟随和跨页面播放互斥。"""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer


class AudioOutputWatcher(QObject):
    """将播放器持续绑定到系统默认输出设备。

    设备插拔可由 ``audioOutputsChanged`` 捕获；Windows 手动切换默认设备时不一定
    发出该信号，因此还需定时轮询。
    """

    def __init__(self, player: QMediaPlayer, audio_output, label: str = '播放器',
                 interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self._player = player
        self._audio_output = audio_output
        self._label = label
        self._current_device_id = QMediaDevices.defaultAudioOutput().id()

        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self.sync_default_device)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sync_default_device)
        self._timer.start(interval_ms)

    def sync_default_device(self):
        """检测默认设备变化，并在切换后恢复原播放位置。"""
        default_device = QMediaDevices.defaultAudioOutput()
        if default_device.id() == self._current_device_id:
            return

        self._current_device_id = default_device.id()
        self._audio_output.setDevice(default_device)

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            position = self._player.position()
            self._player.pause()
            self._player.setPosition(position)
            self._player.play()

        print(f'[{self._label}] 音频输出设备已自适应切换至: {default_device.description()}')


class PlaybackBus(QObject):
    """在互不引用的页面之间协调播放互斥。

    主页朗读和设置页试听共用输出设备；任一方开始播放前广播停止请求，避免音频叠加。
    """

    stop_requested = Signal(object)

    def request_stop(self, requester=None):
        """广播停止请求；发起方可用 ``requester`` 标识自身。"""
        self.stop_requested.emit(requester)
