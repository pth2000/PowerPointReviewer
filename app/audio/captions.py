"""配音分句与随 WAV 保存的分句时间信息，供音频处理和字幕导出使用。"""

import json
import re
import struct
import unicodedata
import wave
from io import BytesIO
from pathlib import Path

SENTENCE_MODE = '短句配音'
CONTINUOUS_MODE = '整段配音'
_CHUNK = b'pprs'
_TOKEN = re.compile(
    r'[A-Za-z][A-Za-z0-9_]*(?:[.\-/+][A-Za-z0-9_]+)*'
    r'|\d+(?:[.,]\d+)*(?:\s*(?:[A-Za-z%℃°]+|毫秒|秒|分钟|小时|厘米|毫米|公里|米|年|月|日))?'
    r'|[^\s]',
    re.UNICODE,
)


def split_sentences(text, target=32, maximum=60):
    """按完整句子和长句内的标点分句，不为字幕宽度拆开连续词语。"""
    closing = '”’"\'）】》)]}'
    endings = '。！？!?；;'
    abbreviations = {'e.g', 'i.e', 'etc', 'vs', 'mr', 'mrs', 'ms', 'dr', 'prof', 'fig', 'no'}

    def width(value):
        return sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in value)

    def split_long(sentence):
        result = []
        while width(sentence) > target * 2:
            points = []
            for match in _TOKEN.finditer(sentence):
                if match.group().strip() not in '，,：:':
                    continue
                left, right = sentence[: match.end()].strip(), sentence[match.end() :].strip()
                if width(left) < 8 or width(right) < 8:
                    continue
                score = abs(width(left) - target * 2)
                if width(left) > maximum * 2:
                    score += maximum * 2
                points.append((score, match.end()))
            if not points:
                break
            cut = min(points)[1]
            result.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            result.append(sentence)
        return result

    result = []
    for paragraph in str(text).splitlines():
        paragraph = ' '.join(paragraph.split())
        tokens = list(_TOKEN.finditer(paragraph))
        start, pending = 0, False
        for index, match in enumerate(tokens):
            token = match.group().strip()
            if pending and token not in closing and token not in endings + '.…，,：:、':
                result.extend(split_long(paragraph[start : match.start()].strip()))
                start, pending = match.start(), False
            period = token == '.'
            if period and index:
                previous = tokens[index - 1].group()
                period = (
                    previous.lower() not in abbreviations
                    and not (len(previous) == 1 and previous.isupper())
                    and not re.fullmatch(r'[A-Za-z](?:\.[A-Za-z])+', previous)
                )
            if token in endings or period:
                pending = True
        result.extend(split_long(paragraph[start:].strip()))
    return result


def read_captions(source):
    """读取并校验应用私有 RIFF 块，普通 WAV 或损坏的可选元数据返回 None。"""
    try:
        stream = BytesIO(source) if isinstance(source, bytes) else Path(source).open('rb')
        with stream:
            with wave.open(
                BytesIO(source) if isinstance(source, bytes) else str(source), 'rb'
            ) as reader:
                rate, frames = reader.getframerate(), reader.getnframes()
            header = stream.read(12)
            if header[:4] != b'RIFF' or header[8:] != b'WAVE':
                return None
            limit = struct.unpack('<I', header[4:8])[0] + 8
            while stream.tell() + 8 <= limit:
                tag, size = struct.unpack('<4sI', stream.read(8))
                if stream.tell() + size > limit:
                    return None
                if tag != _CHUNK:
                    stream.seek(size + size % 2, 1)
                    continue
                if size > 1024 * 1024:
                    return None
                data = json.loads(stream.read(size))
                if (
                    data.get('version') != 1
                    or data.get('rate') != rate
                    or data.get('frames') != frames
                ):
                    return None
                previous = 0
                for cue in data['cues']:
                    start, end = cue['start'], cue['end']
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or not previous <= start < end <= frames
                        or not isinstance(cue['text'], str)
                        or not cue['text'].strip()
                    ):
                        return None
                    previous = end
                return data if data['cues'] else None
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        EOFError,
        wave.Error,
        struct.error,
    ):
        return None
    return None


def write_captions(content, cues):
    """重建 PCM WAV 后附加元数据，常规播放器和音频缓存均可直接读取。"""
    with wave.open(BytesIO(content), 'rb') as reader:
        params, pcm = reader.getparams(), reader.readframes(reader.getnframes())
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setparams(params)
        writer.writeframes(pcm)
    data = output.getvalue()
    payload = json.dumps(
        {'version': 1, 'rate': params.framerate, 'frames': params.nframes, 'cues': cues},
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    if len(payload) > 1024 * 1024:
        raise ValueError('单段讲稿过长，请分段后重试。')
    block = _CHUNK + struct.pack('<I', len(payload)) + payload + b'\0' * (len(payload) % 2)
    result = data[:4] + struct.pack('<I', len(data) + len(block) - 8) + data[8:] + block
    if read_captions(result) is None:
        raise ValueError('配音分句信息无效，请重新生成配音。')
    return result


def transfer_captions(source, content, *, speed=1.0, leading_frames=0):
    """重采样、变速或补留白时按采样率转换时间，不推测词语位置。"""
    data = read_captions(source)
    if data is None:
        return content
    with wave.open(BytesIO(content), 'rb') as reader:
        rate, frames = reader.getframerate(), reader.getnframes()
    ratio = rate / data['rate'] / speed
    cues = []
    for cue in data['cues']:
        start = min(frames, round(cue['start'] * ratio) + leading_frames)
        end = min(frames, round(cue['end'] * ratio) + leading_frames)
        if end > start:
            cues.append({**cue, 'start': start, 'end': end})
    return write_captions(content, cues) if cues else content
