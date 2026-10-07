"""集中装配需要跨页面共享的应用级对象。"""

from dataclasses import dataclass, field

from app import paths
from app.hotkeys import HotkeyManager
from app.slideshow import SlideshowWatcher
from app.playback import PlaybackBus
from app.settings_store import AppSettings, ConfigStore
from tts_engine import TTSEngine


@dataclass
class AppContext:
    """保存 TTS、播放总线和配置仓库等进程级依赖。"""

    version: str
    tts_engine: TTSEngine
    app_settings: AppSettings = field(default_factory=AppSettings)
    playback_bus: PlaybackBus = field(default_factory=PlaybackBus)
    hotkeys: HotkeyManager = field(default_factory=HotkeyManager)
    slideshow_watcher: SlideshowWatcher = field(default_factory=SlideshowWatcher)
    # 由 Velopack 更新后重启时为新版本号
    updated_to: str = ''
    config: ConfigStore = field(init=False)

    def __post_init__(self):
        """在其余依赖就绪后创建唯一的配置读写入口。"""
        self.config = ConfigStore(paths.SETTINGS_FILE, self.tts_engine, self.app_settings)
