"""仅用当前任务的实时进度估算剩余时间，不保存设备基准或跨任务历史。"""

from collections import deque
from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True)
class RemainingTime:
    lower: float
    upper: float


class RemainingTimeEstimator:
    """五秒一组测量速度，最近三组跟随速度变化，近期分布提供预估范围。"""

    def __init__(self):
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def observe(self, fraction, now):
        if not math.isfinite(fraction) or not math.isfinite(now) or not 0 <= fraction < 1:
            self.reset()
            return None
        if self.samples:
            previous_time, previous_fraction = self.samples[-1]
            if now < previous_time or fraction < previous_fraction or now - previous_time > 10:
                self.reset()
            elif now - previous_time < .5:
                return self._estimate()
        self.samples.append((now, fraction))
        while len(self.samples) > 1 and self.samples[1][0] < now - 60:
            self.samples.popleft()
        return self._estimate()

    def _estimate(self):
        if len(self.samples) < 3:
            return None
        rates = []
        start_time, start_fraction = self.samples[0]
        for timestamp, fraction in self.samples:
            elapsed = timestamp - start_time
            if elapsed >= 5:
                rates.append((fraction - start_fraction) / elapsed)
                start_time, start_fraction = timestamp, fraction
        # 至少两组当前任务的实测速率；短任务可能在样本充分之前就已完成。
        if len(rates) < 2:
            return None
        recent = rates[-3:]
        if any(rate <= 0 for rate in recent):
            return None
        speed = median(recent)
        distribution = sorted(rates[-6:])
        lower_speed = min(speed * .9, self._quantile(distribution, .2))
        upper_speed = max(speed * 1.1, self._quantile(distribution, .8))
        if lower_speed <= 0:
            return None
        remaining = 1 - self.samples[-1][1]
        lower, upper = remaining / upper_speed, remaining / lower_speed
        if not math.isfinite(upper) or upper < 1:
            return None
        return RemainingTime(lower, upper)

    @staticmethod
    def _quantile(values, fraction):
        index = (len(values) - 1) * fraction
        lower = int(index)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (index - lower)


class RemainingTimeDisplay:
    """平滑预计完成时刻，并限制文字变化；不延迟进度条和阶段切换。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self._deadlines = None
        self._previous_time = None
        self._changed_at = None
        self._pending = None
        self._pending_since = None
        self._text = format_remaining(None)

    def update(self, estimate, now):
        if estimate is None:
            self.reset()
            return self._text
        if self._previous_time is not None and (
                now < self._previous_time or now - self._previous_time > 10):
            self.reset()
        deadlines = (now + estimate.lower, now + estimate.upper)
        if self._deadlines is None:
            self._deadlines = deadlines
        else:
            # 平滑完成时刻而非剩余秒数，匀速推进时不会额外滞后二十秒。
            weight = -math.expm1(-(now - self._previous_time) / 20)
            self._deadlines = tuple(previous + weight * (current - previous)
                                    for previous, current in zip(self._deadlines, deadlines))
        self._previous_time = now
        candidate = format_remaining(RemainingTime(
            max(0, self._deadlines[0] - now), max(1, self._deadlines[1] - now)))
        if self._changed_at is None:
            self._text, self._changed_at = candidate, now
        elif candidate == self._text:
            self._pending = self._pending_since = None
        else:
            if candidate != self._pending:
                self._pending, self._pending_since = candidate, now
            elapsed = now - self._changed_at
            # 文字更新至少间隔十秒；新文案持续五秒才采用。持续大幅变化时，
            # 最多保留旧值三十秒，避免为了稳定而长期显示过时的预估。
            if elapsed >= 10 and (now - self._pending_since >= 5 or elapsed >= 30):
                self._text, self._changed_at = candidate, now
                self._pending = self._pending_since = None
        return self._text


def format_remaining(estimate):
    if estimate is None:
        return '正在估算时间…'
    seconds = (estimate.lower + estimate.upper) / 2
    if seconds < 60:
        step, unit = 10, '秒'
    elif seconds < 3600:
        step, unit = 60, '分钟'
    else:
        step, unit = 3600, '小时'
    value = max(1, math.ceil(seconds / step))
    if unit == '秒':
        value *= step
    return f'预计剩余约 {value} {unit}'
