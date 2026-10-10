"""视频录制参数及持久化配置校验。"""

from dataclasses import asdict, dataclass

from app.video_timeline import FPS, SUPPORTED_FRAME_RATES


RESOLUTIONS = (
    ('720p（1280 × 720）', 1280, 720),
    ('1080p（1920 × 1080）', 1920, 1080),
    ('1440p（2560 × 1440）', 2560, 1440),
    ('4K（3840 × 2160）', 3840, 2160),
)
AUDIO_BIT_RATES = (96000, 128000, 192000, 256000)


@dataclass(frozen=True)
class VideoRecordingOptions:
    width: int = 1920
    height: int = 1080
    fps: int = FPS
    video_bit_rate: int = 8000000
    audio_bit_rate: int = 128000
    container: str = 'mp4'
    embed_subtitles: bool = False

    def __post_init__(self):
        if (self.width, self.height) not in [(width, height) for _, width, height in RESOLUTIONS]:
            raise ValueError('请选择支持的视频分辨率。')
        if self.fps not in SUPPORTED_FRAME_RATES:
            raise ValueError('请选择支持的视频帧率。')
        if not 1000000 <= self.video_bit_rate <= 100000000:
            raise ValueError('视频码率须为 1 到 100 Mbps。')
        if self.audio_bit_rate not in AUDIO_BIT_RATES:
            raise ValueError('请选择支持的音频码率。')
        if self.container not in ('mp4', 'mkv'):
            raise ValueError('请选择 MP4 或 MKV 输出格式。')
        if not isinstance(self.embed_subtitles, bool):
            raise ValueError('字幕开关须为布尔值。')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        defaults = cls()
        if not isinstance(values, dict):
            return defaults
        parsed = defaults.to_dict()
        for key in parsed:
            if key in ('container', 'embed_subtitles'):
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
        if parsed['audio_bit_rate'] not in AUDIO_BIT_RATES:
            parsed['audio_bit_rate'] = defaults.audio_bit_rate
        if values.get('container') in ('mp4', 'mkv'):
            parsed['container'] = values['container']
        if isinstance(values.get('embed_subtitles'), bool):
            parsed['embed_subtitles'] = values['embed_subtitles']
        return cls(**parsed)
