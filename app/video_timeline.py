"""以音频采样帧为基准安排配音、动画点击与换页。"""

from dataclasses import dataclass
from pathlib import Path
import wave


SAMPLE_RATE = 48000
FPS = 30
SUPPORTED_FRAME_RATES = (24, 25, 30, 50, 60)
FRAMES_PER_TICK = SAMPLE_RATE // FPS


@dataclass(frozen=True)
class VideoCue:
    page: int
    path: Path
    action_frame: int
    audio_frame: int
    end_frame: int


class VideoTimeline:
    """前置动画等待和收尾静音均计入音视频的同一条时间轴。"""

    def __init__(self, pages, paths, wait_ms=0, fps=FPS, lead_ms=500, tail_ms=500):
        if fps not in SUPPORTED_FRAME_RATES:
            raise ValueError('录制帧率须为 24、25、30、50 或 60 fps。')
        self.fps = fps
        self.frames_per_tick = SAMPLE_RATE // fps
        if not pages or len(pages) != len(paths):
            raise ValueError('讲稿段与音频数量不一致，请重新生成音频。')
        if not 0 <= wait_ms <= 10000:
            raise ValueError('动画等待时间须为 0 到 10000 毫秒。')
        if not 0 <= lead_ms <= 10000 or not 0 <= tail_ms <= 10000:
            raise ValueError('视频头尾停留时间须为 0 到 10000 毫秒。')
        self.cues = []
        cursor = 0
        wait = round(wait_ms * SAMPLE_RATE / 1000)
        previous_page = 0
        for page, path in zip(pages, paths):
            page = int(page)
            if page < 1 or page < previous_page:
                raise ValueError('录制讲稿的页码须按升序排列。')
            previous_page = page
            path = Path(path)
            with wave.open(str(path), 'rb') as reader:
                if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, SAMPLE_RATE):
                    raise ValueError('录制音频须为 48 kHz 单声道 PCM16 WAV。')
                frames = reader.getnframes()
                if frames <= 0:
                    raise ValueError('录制音频为空。')
            # 实时录制默认等待首屏稳定；静态合成可直接从配音起点开始。
            audio_frame = cursor + (max(wait, round(lead_ms * SAMPLE_RATE / 1000)) if not self.cues else wait)
            self.cues.append(VideoCue(page, path, cursor, audio_frame, audio_frame + frames))
            cursor = audio_frame + frames
        # 按所需收尾停留补齐完整视频帧，静态合成只在结尾补不足一帧的静音。
        self.total_frames = ((cursor + round(tail_ms * SAMPLE_RATE / 1000) + self.frames_per_tick - 1)
                             // self.frames_per_tick * self.frames_per_tick)

    @property
    def duration(self):
        return self.total_frames / SAMPLE_RATE


class TimelineAudio:
    """按需读取片段，仅保留一个 WAV 句柄，长讲稿无需整体加载进内存。"""

    def __init__(self, timeline):
        self.timeline = timeline
        self.index = 0
        self.reader = None

    def read(self, start, frames):
        if start < 0 or frames <= 0 or start + frames > self.timeline.total_frames:
            raise ValueError('音频读取位置超出录制时间轴。')
        end = start + frames
        output = bytearray(frames * 2)
        while self.index < len(self.timeline.cues):
            cue = self.timeline.cues[self.index]
            if cue.end_frame <= start:
                self.close()
                self.index += 1
                continue
            if cue.audio_frame >= end:
                break
            overlap_start = max(start, cue.audio_frame)
            overlap_end = min(end, cue.end_frame)
            if overlap_end > overlap_start:
                if self.reader is None:
                    self.reader = wave.open(str(cue.path), 'rb')
                self.reader.setpos(overlap_start - cue.audio_frame)
                pcm = self.reader.readframes(overlap_end - overlap_start)
                if len(pcm) != (overlap_end - overlap_start) * 2:
                    raise RuntimeError('录制音频已损坏或被修改。')
                offset = (overlap_start - start) * 2
                output[offset:offset + len(pcm)] = pcm
            if cue.end_frame > end:
                break
            self.close()
            self.index += 1
        return bytes(output)

    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None
