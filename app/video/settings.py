"""视频录制参数及持久化配置校验。"""

from dataclasses import asdict, dataclass

from app.video.timeline import FPS, SUPPORTED_FRAME_RATES


RESOLUTIONS = (
    ('720p（1280 × 720）', 1280, 720),
    ('1080p（1920 × 1080）', 1920, 1080),
    ('1440p（2560 × 1440）', 2560, 1440),
    ('4K（3840 × 2160）', 3840, 2160),
)
OBS_QUALITY_PRESETS = (
    ('高质量，中等文件大小（推荐）', 'Small', '适合日常演示，兼顾画面清晰度与文件大小。'),
    ('近乎无损的质量，大文件大小', 'HQ', '保留更多画面细节，文件较大。'),
    ('自定义码率', 'Stream', '按指定的视频码率录制。'),
)


@dataclass(frozen=True)
class VideoRecordingOptions:
    width: int = 1920
    height: int = 1080
    fps: int = FPS
    video_bit_rate: int = 8000000
    container: str = 'mp4'
    embed_subtitles: bool = False
    windowed: bool = True
    obs_path: str = ''
    obs_quality: str = 'Small'

    def __post_init__(self):
        if (self.width, self.height) not in [(width, height) for _, width, height in RESOLUTIONS]:
            raise ValueError('请选择支持的视频分辨率。')
        if self.fps not in SUPPORTED_FRAME_RATES:
            raise ValueError('请选择支持的视频帧率。')
        if not 1000000 <= self.video_bit_rate <= 100000000:
            raise ValueError('视频码率须为 1 到 100 Mbps。')
        if self.container not in ('mp4', 'mkv'):
            raise ValueError('请选择 MP4 或 MKV 输出格式。')
        if not isinstance(self.embed_subtitles, bool):
            raise ValueError('字幕设置无效，请重新选择是否添加字幕。')
        if not isinstance(self.windowed, bool):
            raise ValueError('请选择有效的放映方式。')
        if not isinstance(self.obs_path, str):
            raise ValueError('请选择有效的 OBS Studio 程序位置。')
        if not isinstance(self.obs_quality, str) or self.obs_quality not in {
                value for _label, value, _hint in OBS_QUALITY_PRESETS}:
            raise ValueError('请选择有效的录制画质。')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        defaults = cls()
        if not isinstance(values, dict):
            return defaults
        parsed = defaults.to_dict()
        for key in parsed:
            if key in ('container', 'embed_subtitles', 'windowed', 'obs_path', 'obs_quality'):
                continue
            try:
                parsed[key] = int(values.get(key, parsed[key]))
            except (TypeError, ValueError, OverflowError):
                pass
        if (parsed['width'], parsed['height']) not in [(w, h) for _, w, h in RESOLUTIONS]:
            parsed['width'], parsed['height'] = defaults.width, defaults.height
        if parsed['fps'] not in SUPPORTED_FRAME_RATES:
            parsed['fps'] = defaults.fps
        if not 1000000 <= parsed['video_bit_rate'] <= 100000000:
            parsed['video_bit_rate'] = defaults.video_bit_rate
        if values.get('container') in ('mp4', 'mkv'):
            parsed['container'] = values['container']
        if isinstance(values.get('embed_subtitles'), bool):
            parsed['embed_subtitles'] = values['embed_subtitles']
        if isinstance(values.get('windowed'), bool):
            parsed['windowed'] = values['windowed']
        if isinstance(values.get('obs_path'), str):
            parsed['obs_path'] = values['obs_path']
        if isinstance(values.get('obs_quality'), str) and values['obs_quality'] in {
                value for _label, value, _hint in OBS_QUALITY_PRESETS}:
            parsed['obs_quality'] = values['obs_quality']
        return cls(**parsed)


def load_video_options(settings, mode):
    """优先读取当前方式的设置，旧的共用配置作为首次迁移来源。"""
    if mode not in ('native', 'obs'):
        raise ValueError('请选择 PowerPoint 导出或 OBS 录制。')
    if settings is None:
        return VideoRecordingOptions()
    saved = settings.get('video_recording_by_mode')
    if isinstance(saved, dict) and isinstance(saved.get(mode), dict):
        return VideoRecordingOptions.from_dict(saved[mode])
    return VideoRecordingOptions.from_dict(settings.get('video_recording'))


def save_video_options(settings, mode, options):
    """分别保存两种方式，不覆盖旧配置或另一种方式的选择。"""
    if mode not in ('native', 'obs'):
        raise ValueError('请选择 PowerPoint 导出或 OBS 录制。')
    saved = settings.get('video_recording_by_mode')
    saved = dict(saved) if isinstance(saved, dict) else {}
    saved[mode] = options.to_dict()
    settings.set('video_recording_by_mode', saved)
