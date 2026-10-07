"""解析应用根目录，并集中声明所有运行期数据路径。

工作目录会随启动方式变化，不能用它定位配置和缓存。源码模式以仓库根目录为基准，
PyInstaller 冻结模式则以可执行文件所在目录为基准。
"""

import sys
from pathlib import Path


def app_root() -> Path:
    """返回源码仓库根目录或冻结后的可执行文件目录。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_root(app_dir: Path) -> Path:
    """返回用户数据根目录。

    Velopack 安装版与便携版的程序文件位于 current 子目录，每次更新整体替换，
    用户数据放在它的上一级。
    """
    if getattr(sys, 'frozen', False) and (app_dir.parent / 'Update.exe').is_file():
        return app_dir.parent
    return app_dir


APP_ROOT = app_root()
DATA_ROOT = data_root(APP_ROOT)
IS_VELOPACK_INSTALL = DATA_ROOT != APP_ROOT

CONFIG_DIR = DATA_ROOT / 'config'
SETTINGS_FILE = CONFIG_DIR / 'settings.json'

DATA_DIR = DATA_ROOT / 'data'
AUDIO_CACHE_DIR = DATA_DIR / 'cache' / 'audio_chunks'
COUNTDOWN_CACHE_DIR = DATA_DIR / 'cache' / 'countdown'
SESSION_DIR = DATA_DIR / 'sessions'
# Edge 音色目录缓存在数据区，设置页无需每次打开都访问网络。
EDGE_VOICE_CACHE = DATA_DIR / 'cache' / 'edge_voices.json'

LOG_DIR = DATA_DIR / 'logs'
LOG_FILE = LOG_DIR / 'app.log'

TEMP_DIR = DATA_ROOT / 'temp'
# 主页会扫描 TEMP_DIR 根目录中的音频；试听文件必须隔离，避免进入正文播放列表。
PREVIEW_DIR = TEMP_DIR / 'preview'


def ensure_runtime_dirs() -> None:
    """创建应用启动后可能写入的全部目录。"""
    for path in (CONFIG_DIR, AUDIO_CACHE_DIR, COUNTDOWN_CACHE_DIR, SESSION_DIR,
                 LOG_DIR, TEMP_DIR, PREVIEW_DIR):
        path.mkdir(parents=True, exist_ok=True)
