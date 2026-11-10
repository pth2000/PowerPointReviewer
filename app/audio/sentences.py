"""读取实际配音的分句时间轴，供播放、审听、统计和字幕共用。"""

from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.audio.captions import read_captions


@dataclass(frozen=True)
class SentenceSpan:
    number: int
    text: str
    start_frame: int
    end_frame: int
    rate: int

    @property
    def start(self):
        return self.start_frame / self.rate

    @property
    def end(self):
        return self.end_frame / self.rate

    @property
    def start_ms(self):
        return round(self.start * 1000)

    @property
    def end_ms(self):
        return round(self.end * 1000)


@dataclass(frozen=True)
class SentenceTimeline:
    spans: tuple[SentenceSpan, ...] = ()
    _starts: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, '_starts', tuple(span.start * 1000 for span in self.spans))

    def __len__(self):
        return len(self.spans)

    def at(self, position_ms):
        index = bisect_right(self._starts, position_ms) - 1
        if index >= 0 and position_ms < self.spans[index].end * 1000:
            return self.spans[index]
        return None

    def sentence(self, number):
        return self.spans[number - 1] if 1 <= number <= len(self.spans) else None


@lru_cache(maxsize=256)
def _load(path, modified_ns, size):
    data = read_captions(path)
    if data is None:
        return SentenceTimeline()
    return SentenceTimeline(
        tuple(
            SentenceSpan(number, cue['text'], cue['start'], cue['end'], data['rate'])
            for number, cue in enumerate(data['cues'], 1)
        )
    )


def load_sentences(path, text=None):
    """按文件版本复用轻量元数据；旧音频、坏元数据或文字不匹配时返回空时间轴。"""
    try:
        path = Path(path).resolve()
        stat = path.stat()
    except OSError, TypeError, ValueError:
        return SentenceTimeline()
    timeline = _load(str(path), stat.st_mtime_ns, stat.st_size)
    if text is not None:
        recorded = ''.join(''.join(span.text for span in timeline.spans).split())
        if recorded != ''.join(str(text).split()):
            return SentenceTimeline()
    return timeline


class SentenceGenerationError(RuntimeError):
    def __init__(self, number, detail):
        self.number = number
        self.detail = detail
        super().__init__(f'第 {number} 句：{detail}')
