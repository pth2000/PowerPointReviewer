"""edge-tts 在线 TTS 引擎：内置音色列表、参数格式转换工具、音频合成"""

import asyncio

import edge_tts

# edge-tts 内置中文音色列表
VOICES: list[str] = [
    'zh-CN-XiaoxiaoNeural',
    'zh-CN-XiaoyiNeural',
    'zh-CN-YunjianNeural',
    'zh-CN-YunxiNeural',
    'zh-CN-YunxiaNeural',
    'zh-CN-YunyangNeural',
    'zh-CN-liaoning-XiaobeiNeural',
    'zh-CN-shaanxi-XiaoniNeural',
]


# ──────────────────────────────────────────────────────────
# 参数格式转换工具
# ──────────────────────────────────────────────────────────

def _percent_text(value: int) -> str:
    """把整数转为 edge-tts 百分比文本，如 +10%、-5%"""
    return f'+{value}%' if value >= 0 else f'{value}%'


def rate_to_edge(rate: int) -> str:
    """把 UI 速率（基准 200 = 0%）映射为 edge-tts 速率文本"""
    return _percent_text(int((rate - 200) / 2))


def volume_to_edge(volume: float) -> str:
    """把 0~1 音量映射为 edge-tts 百分比文本（1.0 → +0%）"""
    return _percent_text(int((volume - 1.0) * 100))


def pitch_to_edge(pitch: int) -> str:
    """把音调偏移量转为 edge-tts Hz 文本，如 +0Hz、-10Hz"""
    return f'+{pitch}Hz' if pitch >= 0 else f'{pitch}Hz'


# ──────────────────────────────────────────────────────────
# 音频合成
# ──────────────────────────────────────────────────────────

def save(text: str, path: str, *, voices: list[str] = VOICES, rate: int = 200,
         volume: float = 1.0, pitch: int = 0, voice_index: int = 0) -> None:
    """使用 edge-tts 将文本合成为音频文件。

    Args:
        text:        要合成的文本。
        path:        输出文件路径。
        voices:      音色名称列表，默认使用模块内置 VOICES。
        rate:        UI 速率值（基准 200），映射为 edge-tts 百分比。
        volume:      音量 0~1，映射为 edge-tts 百分比。
        pitch:       音调偏移（Hz）。
        voice_index: 发音人索引。
    """
    voice = voices[voice_index] if 0 <= voice_index < len(voices) else voices[0]

    async def _run() -> None:
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=rate_to_edge(int(rate)),
            volume=volume_to_edge(float(volume)),
            pitch=pitch_to_edge(int(pitch)),
        )
        await communicate.save(path)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
