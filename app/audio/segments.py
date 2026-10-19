"""片段配音调整、不可变音频引用及更新时的匹配。"""

from array import array
from collections import defaultdict, deque
import copy
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import uuid
import wave

from app.audio import processing as audio_processing


DEFAULT_SETTINGS = {'speed': 1.0, 'volume': 100, 'leading_ms': 0, 'trailing_ms': 0}
_SECRET_KEYS = {'api_key', 'access_token', 'token', 'password', 'authorization'}


def public_profile(profile):
    if not isinstance(profile, dict):
        return {}
    return {key: public_profile(value) if isinstance(value, dict) else copy.deepcopy(value)
            for key, value in profile.items() if str(key).casefold() not in _SECRET_KEYS}


def profile_id(profile):
    content = json.dumps(public_profile(profile), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def settings(values=None):
    result = {**DEFAULT_SETTINGS, **(values or {})}
    speed = float(result['speed'])
    if not math.isfinite(speed) or not 0.5 <= speed <= 2:
        raise ValueError('配音语速须为 0.5 到 2 倍。')
    result['speed'] = speed
    for key, limit in (('volume', 200), ('leading_ms', 3000), ('trailing_ms', 3000)):
        number = int(result[key])
        if not 0 <= number <= limit:
            raise ValueError('音量须为 0–200%，开头和结尾留白须为 0–3000 毫秒。')
        result[key] = number
    return result


def process_wav(content, options):
    options = settings(options)
    content = audio_processing.process_wav(
        content, speed=options['speed'], leading_silence_ms=options['leading_ms'],
        trailing_silence_ms=options['trailing_ms'])
    if options['volume'] == 100:
        return content
    params, pcm = audio_processing._read_wav(content)
    if params.sampwidth != 2:
        raise ValueError('当前音频格式无法调整音量，请重新生成后再试。')
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    gain = options['volume'] / 100
    for index, value in enumerate(samples):
        samples[index] = max(-32768, min(32767, round(value * gain)))
    if sys.byteorder != 'little':
        samples.byteswap()
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams(params)
        writer.writeframes(samples.tobytes())
    return output.getvalue()


def cache_path(directory, reference):
    key = str(reference.get('key', ''))
    ext = str(reference.get('ext', '')).lower()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', key) or ext not in ('wav', 'mp3'):
        raise ValueError('片段的音频记录不完整，请重新生成配音。')
    return Path(directory) / f'{key}.{ext}'


def reference(key, ext, duration):
    return {'key': str(key), 'ext': str(ext), 'duration': float(duration)}


def references(item):
    """包含正式音频、效果处理前音频和用于恢复的原始配音。"""
    found = []
    if item.get('cache_key'):
        found.append({'key': str(item['cache_key']), 'ext': item.get('cache_ext', 'wav'),
                      'duration': item.get('duration', 0)})
    edit = item.get('audio_edit')
    if isinstance(edit, dict):
        for name in ('base', 'original'):
            value = edit.get(name)
            if isinstance(value, dict) and value.get('key'):
                found.append(dict(value))
    return found


def validated_edit(item):
    value = item.get('audio_edit')
    if not isinstance(value, dict):
        return None
    try:
        if (value.get('text') != item['text'] or value['audio_key'] != item['cache_key']
                or value['audio_ext'] != item.get('cache_ext', 'wav')):
            return None
        settings(value.get('settings'))
        if not isinstance(value.get('spoken_text', item['text']), str):
            return None
        for name in ('base', 'original'):
            cache_path(Path('.'), value[name])
            duration = float(value[name]['duration'])
            if not math.isfinite(duration) or duration <= 0:
                return None
        return copy.deepcopy(value)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def store_audio(source, directory):
    """按音频内容保存新文件，任何已分发的缓存引用都保持不变。"""
    source, directory = Path(source), Path(directory)
    ext = source.suffix.lower().lstrip('.')
    if ext not in ('mp3', 'wav') or not source.is_file() or source.stat().st_size == 0:
        raise ValueError('音频文件不可用，请重新试听后再采用。')
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    key = digest.hexdigest()
    target = directory / f'{key}.{ext}'
    directory.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.stat().st_size == 0:
        temporary = directory / f'{key}.{uuid.uuid4().hex}.tmp'
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return key, ext, target


def match_edits(before, edits, after):
    """仅为同页中未变的正文保留调整，重复句子按出现顺序匹配。"""
    candidates = defaultdict(deque)
    for index, segment in enumerate(before):
        edit = edits.get(index)
        identity = (int(segment['page']), str(segment['text']))
        candidates[identity].append(copy.deepcopy(edit) if isinstance(edit, dict)
                                    and edit.get('text') == segment['text'] else None)
    result = {}
    for index, segment in enumerate(after):
        queue = candidates[(int(segment['page']), str(segment['text']))]
        if queue:
            edit = queue.popleft()
            if edit:
                result[index] = edit
    return result
