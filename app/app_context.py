"""应用上下文：集中管理全局共享依赖"""

from dataclasses import dataclass

from tts_engine import TTSEngine


@dataclass
class AppContext:
    """应用运行期共享对象"""

    version: str
    tts_engine: TTSEngine
