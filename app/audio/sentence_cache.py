"""按文本和语音参数缓存分句音频，供局部更新和失败重试复用。"""

import hashlib
import json
import re
import wave
from pathlib import Path

from app.audio.concurrency import cache_lock
from app.workspaces import TemporaryWorkspace

_KEY = re.compile(r'sentence_[a-f0-9]{64}')


def is_sentence_key(value):
    return isinstance(value, str) and _KEY.fullmatch(value) is not None


def cache_key(text, profile):
    payload = json.dumps(
        {'version': 1, 'text': ' '.join(text.split()), 'profile': profile},
        ensure_ascii=False,
        sort_keys=True,
    )
    return 'sentence_' + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def valid_pcm(path):
    """验证完整 PCM 数据，截断或格式不符的缓存需要重新生成。"""
    try:
        with wave.open(str(path), 'rb') as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (
                1,
                2,
                48000,
            ):
                return False
            remaining = reader.getnframes()
            if not remaining:
                return False
            while remaining:
                count = min(remaining, 32768)
                if len(reader.readframes(count)) != count * 2:
                    return False
                remaining -= count
        return True
    except OSError, ValueError, EOFError, wave.Error:
        return False


def get_or_generate(directory, key, generate, *, force=False, cancelled=lambda: False, report=None):
    """返回完整 WAV 字节；在锁内读取，避免并行重生成替换正在使用的文件。"""
    if not is_sentence_key(key):
        raise ValueError('分句配音缓存标识无效。')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{key}.wav'
    with cache_lock(target, cancelled):
        if not force and valid_pcm(target):
            content = target.read_bytes()
            if report is not None:
                report(True)
            return content
        with TemporaryWorkspace(prefix='sentence-', dir=directory) as folder:
            candidate = Path(folder) / 'audio.wav'
            generate(candidate)
            if cancelled():
                raise InterruptedError('配音生成已停止')
            if not valid_pcm(candidate):
                raise RuntimeError('分句配音不完整，请重试或更换音色。')
            candidate.replace(target)
        content = target.read_bytes()
        if report is not None:
            report(False)
        return content
