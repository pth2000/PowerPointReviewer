"""本地 pyttsx3 TTS 引擎：初始化语音列表并执行离线合成。"""

import re

import pyttsx3


def init_voices() -> tuple[list, list[str]]:
    """初始化本地语音列表。

    Returns:
        (voices, voice_names): voices 为 pyttsx3 Voice 对象列表，
        voice_names 为对应的可读名称列表。
    """
    try:
        engine = pyttsx3.init()
        voices = engine.getProperty('voices')
        names: list[str] = []
        for voice in voices:
            match = re.search(r'name=([^\n]+)', str(voice))
            if match:
                names.append(match.group(1).strip())
            else:
                names.append(getattr(voice, 'name', '未知语音'))
        del engine
        return voices, names
    except Exception as e:
        print(f'初始化本地语音失败，将仅使用在线引擎：{e}')
        return [], []


def save(text: str, path: str, *, voices: list, rate: int = 200,
         volume: float = 1.0, voice_index: int = 0) -> None:
    """使用 pyttsx3 将文本合成为音频文件。

    Args:
        text:        要合成的文本。
        path:        输出文件路径。
        voices:      pyttsx3 Voice 对象列表（由 init_voices 返回）。
        rate:        语速，默认 200。
        volume:      音量 0~1，默认 1.0。
        voice_index: 发音人索引。
    """
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', int(rate))
        engine.setProperty('volume', float(volume))
        if voices and 0 <= voice_index < len(voices):
            engine.setProperty('voice', voices[voice_index].id)
        engine.save_to_file(text, path)
        engine.runAndWait()
        engine.stop()
        del engine
    except Exception as e:
        raise RuntimeError(f'本地引擎保存失败：{e}') from e
