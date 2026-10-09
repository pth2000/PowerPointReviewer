"""对本地 TTS 的 PCM WAV 调整语速，并补足头尾停顿。"""

from array import array
from io import BytesIO
import math
import sys
import wave

from app import signalsmith

DEFAULT_SPEED = 1.0
DEFAULT_LEADING_SILENCE_MS = 0
DEFAULT_TRAILING_SILENCE_MS = 0
# 处理算法变化时隔离旧音频缓存。
PROCESSING_VERSION = 'signalsmith-1.3.2-native-v1'
# 16 位 PCM 中约 -54 dB 的幅度，仅用于测量已有低音量留白；不裁切音频。
SILENCE_THRESHOLD = 64


def validate_options(speed, leading_silence_ms, trailing_silence_ms):
    if (not isinstance(speed, (int, float)) or isinstance(speed, bool) or
            not math.isfinite(speed) or not 0.5 <= speed <= 2):
        raise RuntimeError('语速倍率须为 0.5 到 2 之间的数值。')
    for value in (leading_silence_ms, trailing_silence_ms):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3000:
            raise RuntimeError('音频前后留白须为 0 到 3000 之间的整数毫秒。')


def _read_wav(content):
    try:
        with wave.open(BytesIO(content), 'rb') as audio:
            params = audio.getparams()
            pcm = audio.readframes(params.nframes)
            if (params.nframes <= 0 or params.framerate <= 0 or
                    len(pcm) != params.nframes * params.nchannels * params.sampwidth):
                raise ValueError('空音频或不完整 WAV')
    except (wave.Error, EOFError, ValueError) as exc:
        raise RuntimeError('本地 TTS 未返回完整的 WAV 音频，请查看服务日志后重试。') from exc
    return params, pcm


def _change_speed(content, speed):
    params, pcm = _read_wav(content)
    if params.sampwidth != 2 or params.nchannels not in (1, 2):
        raise RuntimeError('语速处理需要单声道或双声道的 16 位 PCM WAV。')
    # 不足 10 ms 的波形不具备稳定变速所需的周期，保留原样以免被处理成静音。
    if params.nframes * 1000 < params.framerate * 10:
        return content
    pcm = signalsmith.change_pcm16(pcm, params.framerate, params.nchannels, speed)
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams(params)
        writer.writeframes(pcm)
    return output.getvalue()


def _silence_frames(samples, channels, limit, *, from_end=False):
    total = len(samples) // channels
    for offset in range(min(total, limit)):
        frame = total - 1 - offset if from_end else offset
        if any(abs(samples[frame * channels + channel]) > SILENCE_THRESHOLD
               for channel in range(channels)):
            return offset
    return min(total, limit)


def process_wav(content: bytes, *, speed=DEFAULT_SPEED,
                leading_silence_ms=DEFAULT_LEADING_SILENCE_MS,
                trailing_silence_ms=DEFAULT_TRAILING_SILENCE_MS) -> bytes:
    """变速保持音高；随后只补足最低头尾停顿，不删除模型生成的内容。"""
    validate_options(speed, leading_silence_ms, trailing_silence_ms)
    params, pcm = _read_wav(content)
    if speed != 1:
        content = _change_speed(content, speed)
        params, pcm = _read_wav(content)
    if leading_silence_ms == trailing_silence_ms == 0:
        return content
    if params.sampwidth != 2:
        raise RuntimeError('音频留白处理需要 16 位 PCM WAV，请确认本地 TTS 的输出格式。')
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    leading = round(params.framerate * leading_silence_ms / 1000)
    trailing = round(params.framerate * trailing_silence_ms / 1000)
    leading -= _silence_frames(samples, params.nchannels, leading)
    trailing -= _silence_frames(samples, params.nchannels, trailing, from_end=True)
    if not leading and not trailing:
        return content
    silence_frame = bytes(params.nchannels * params.sampwidth)
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams(params)
        writer.writeframes(silence_frame * leading + pcm + silence_frame * trailing)
    return output.getvalue()
