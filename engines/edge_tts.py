"""edge-tts 在线 TTS 引擎：音色目录、参数格式转换工具、音频合成"""

import asyncio
import json
from pathlib import Path
from typing import Optional

# edge_tts 会拉起 aiohttp，导入耗时可观，推迟到调用点。

from app import paths

# 当内存、磁盘和网络目录均不可用时，保留覆盖中英文的最小音色集合。
FALLBACK_VOICES: list[str] = [
    'zh-CN-XiaoxiaoNeural',
    'zh-CN-XiaoyiNeural',
    'zh-CN-YunjianNeural',
    'zh-CN-YunxiNeural',
    'zh-CN-YunxiaNeural',
    'zh-CN-YunyangNeural',
    'zh-CN-liaoning-XiaobeiNeural',
    'zh-CN-shaanxi-XiaoniNeural',
    'en-US-AriaNeural',
    'en-US-AndrewNeural',
    'en-US-EmmaNeural',
    'en-US-GuyNeural',
    'en-GB-SoniaNeural',
    'en-GB-RyanNeural',
]

# 常用地区固定置顶，其余地区按代码排序。
PREFERRED_LOCALES = ('zh-CN', 'zh-TW', 'zh-HK', 'en-US', 'en-GB', 'ja-JP', 'ko-KR')

DEFAULT_LOCALE = 'zh-CN'

# 发布包携带完整目录，首次进入设置页无需等待网络请求。
SEED_CATALOG_PATH = Path(__file__).with_name('edge_voices.json')

_CATALOG: Optional[dict[str, list[str]]] = None


# 音色目录

def _build_fallback_catalog() -> dict[str, list[str]]:
    """将最小兜底音色集合按地区代码分组。"""
    catalog: dict[str, list[str]] = {}
    for short_name in FALLBACK_VOICES:
        locale = '-'.join(short_name.split('-')[:2])
        catalog.setdefault(locale, []).append(short_name)
    return catalog


def _fetch_catalog_from_network(timeout: float = 8.0) -> dict[str, list[str]]:
    """从 Edge 服务获取完整音色目录，并按地区代码分组。"""

    async def _run():
        import edge_tts

        return await asyncio.wait_for(edge_tts.list_voices(), timeout=timeout)

    voices = asyncio.run(_run())

    catalog: dict[str, list[str]] = {}
    for item in voices:
        short_name = str(item.get('ShortName', '')).strip()
        locale = str(item.get('Locale', '')).strip()
        if short_name and locale:
            catalog.setdefault(locale, []).append(short_name)

    for names in catalog.values():
        names.sort()
    return catalog


def _load_catalog_file(path) -> Optional[dict]:
    """读取并规范化音色目录 JSON；文件缺失或结构无效时返回 ``None``。"""
    try:
        with Path(path).open('r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f'[edge-tts] 音色目录读取失败 {Path(path).name}：{e}')
        return None

    if isinstance(data, dict) and data:
        return {str(k): [str(v) for v in vs] for k, vs in data.items()}
    return None


def load_catalog(force_refresh: bool = False) -> dict[str, list[str]]:
    """按优先级解析音色目录，必要时联网刷新。

    普通读取依次检查内存、用户缓存和内置目录；只有本地来源不可用或显式刷新时才访问
    网络，最终仍失败则返回最小兜底目录。
    """
    global _CATALOG

    if _CATALOG is not None and not force_refresh:
        return _CATALOG

    cache_path = paths.EDGE_VOICE_CACHE
    if not force_refresh:
        for source in (cache_path, SEED_CATALOG_PATH):
            data = _load_catalog_file(source)
            if data:
                _CATALOG = data
                return _CATALOG

    try:
        catalog = _fetch_catalog_from_network()
        if catalog:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open('w', encoding='utf-8') as f:
                json.dump(catalog, f, ensure_ascii=False, indent=2)
            _CATALOG = catalog
            print(f'[edge-tts] 已获取 {sum(len(v) for v in catalog.values())} 个音色，'
                  f'覆盖 {len(catalog)} 个语言地区')
            return _CATALOG
    except Exception as e:
        print(f'[edge-tts] 获取音色列表失败，使用内置兜底列表：{e}')

    _CATALOG = _build_fallback_catalog()
    return _CATALOG


def list_locales() -> list[str]:
    """返回地区代码列表，常用项置顶、其余项排序。"""
    catalog = load_catalog()
    preferred = [code for code in PREFERRED_LOCALES if code in catalog]
    others = sorted(code for code in catalog if code not in preferred)
    return preferred + others


def voices_for_locale(locale: str) -> list[str]:
    """返回指定地区的音色，缺失时依次回退到默认地区和全部音色。"""
    catalog = load_catalog()
    voices = catalog.get(str(locale).strip())
    if voices:
        return list(voices)

    fallback = catalog.get(DEFAULT_LOCALE)
    if fallback:
        return list(fallback)

    return sorted(name for names in catalog.values() for name in names)


# 参数转换

def _percent_text(value: int) -> str:
    """将整数格式化为 Edge 接口要求的有符号百分比。"""
    return f'+{value}%' if value >= 0 else f'{value}%'


def rate_to_edge(rate: int) -> str:
    """将 UI 语速值映射为以 200 为零点的 Edge 百分比。"""
    return _percent_text(int((rate - 200) / 2))


def volume_to_edge(volume: float) -> str:
    """将 0 至 1 的音量映射为以 1.0 为零点的 Edge 百分比。"""
    return _percent_text(int((volume - 1.0) * 100))


def pitch_to_edge(pitch: int) -> str:
    """将音调偏移格式化为 Edge 接口要求的有符号 Hz 值。"""
    return f'+{pitch}Hz' if pitch >= 0 else f'{pitch}Hz'


# 音频合成

def save(text: str, path: str, *, voice: str = '', locale: str = DEFAULT_LOCALE,
         rate: int = 200, volume: float = 1.0, pitch: int = 0) -> None:
    """使用 Edge-TTS 合成音频，并在未指定音色时选用地区首项。

    ``rate`` 使用 UI 的 200 基准值，``volume`` 范围为 0 至 1，``pitch`` 单位为 Hz。
    """
    target_voice = str(voice).strip()
    if not target_voice:
        candidates = voices_for_locale(locale)
        if not candidates:
            raise RuntimeError('没有可用的 edge-tts 音色')
        target_voice = candidates[0]

    async def _run() -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            target_voice,
            rate=rate_to_edge(int(rate)),
            volume=volume_to_edge(float(volume)),
            pitch=pitch_to_edge(int(pitch)),
        )
        await communicate.save(path)

    # 每次合成可能运行在不同线程；asyncio.run 会完整创建和关闭本次专用事件循环。
    asyncio.run(_run())
