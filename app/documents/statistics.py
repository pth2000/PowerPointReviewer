"""汇总当前讲稿及已记录的配音时长，不读取音频文件。"""

import math
import re
from dataclasses import dataclass


def count_characters(text, mark='●'):
    text = str(text)
    if mark:
        text = text.replace(mark, '')
    return len(re.sub(r'[\s\x00-\x1f\x7f]+', '', text))


def format_duration(seconds):
    if seconds is None:
        return '待生成'
    if seconds >= 60:
        total, fraction = int(seconds + 0.5), 0
    else:
        total, fraction = divmod(round(seconds * 100), 100)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f'{hours} 小时')
    if minutes:
        parts.append(f'{minutes} 分')
    if seconds or fraction or not parts:
        value = f'{seconds}.{fraction:02d}'.rstrip('0').rstrip('.') if fraction else str(seconds)
        parts.append(f'{value} 秒')
    return ' '.join(parts)


@dataclass(frozen=True)
class PageStatistics:
    page: int
    characters: int
    segments: int
    duration: float | None
    sentences: int | None = None


@dataclass(frozen=True)
class SegmentStatistics:
    page: int
    number: int
    duration: float


@dataclass(frozen=True)
class ScriptStatistics:
    pages: tuple[PageStatistics, ...]
    characters: int
    segments: int
    duration: float | None
    longest: SegmentStatistics | None
    shortest: SegmentStatistics | None
    sentences: int | None = None


def build_statistics(notes, segments, durations=None, mark='●', *, sentence_counts=None):
    segments = list(segments)
    ready = durations is not None and bool(segments) and len(durations) == len(segments)
    if ready:
        try:
            durations = [float(value) for value in durations]
            ready = all(math.isfinite(value) and value >= 0 for value in durations)
        except (TypeError, ValueError, OverflowError):
            ready = False
    counts = dict.fromkeys(notes, 0)
    sentence_totals = dict.fromkeys(notes, 0)
    sentence_ready = (sentence_counts is not None and len(sentence_counts) == len(segments)
                      and all(type(value) is int and value >= 0 for value in sentence_counts)
                      and any(sentence_counts))
    totals = dict.fromkeys(notes, 0.0)
    timed = []
    for index, segment in enumerate(segments):
        page = segment['page']
        counts[page] = counts.get(page, 0) + 1
        if sentence_ready:
            sentence_totals[page] = sentence_totals.get(page, 0) + sentence_counts[index]
        if ready:
            duration = durations[index]
            totals[page] = totals.get(page, 0.0) + duration
            timed.append(SegmentStatistics(page, counts[page], duration))
    pages = tuple(PageStatistics(page, count_characters(notes[page], mark), counts[page],
                                 totals[page] if ready else None,
                                 sentence_totals[page] if sentence_ready else None)
                  for page in sorted(notes))
    return ScriptStatistics(pages, sum(row.characters for row in pages), len(segments),
                            sum(durations) if ready else None,
                            max(timed, key=lambda row: row.duration) if timed else None,
                            min(timed, key=lambda row: row.duration) if timed else None,
                            sum(sentence_counts) if sentence_ready else None)
