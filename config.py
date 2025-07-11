"""Configuration constants and settings for PowerPointReviewer."""

import os
from typing import Dict, List

# Application Info
APP_NAME = "PowerPointReviewer"
VERSION = "1.1.0"
COPYRIGHT = "Copyright © 2023-2025 Nagisa"

# UI Configuration
WINDOW_SIZE = (850, 750)
SPLASH_ICON_SIZE = (106, 106)

# File Extensions
SUPPORTED_PPT_EXTENSIONS = ("*.pptx",)
SUPPORTED_WORD_EXTENSIONS = ("*.docx",)
SUPPORTED_AUDIO_EXTENSIONS = (".wav",)

# Theme Colors
DEFAULT_THEME_COLOR = '#B7472A'
WORD_THEME_COLOR = '#2B579A'
PPT_THEME_COLOR = '#B7472A'

# TTS Configuration
DEFAULT_TTS_RATE = 200
DEFAULT_TTS_VOLUME = 1.0
MAX_TTS_RATE = 500

# File Paths
TEMP_DIR = "./temp"
COUNTDOWN_TEMP_DIR = "./temp/countdown"

# Default Settings
DEFAULT_MARK = '●'
DEFAULT_COUNTDOWN_MAX = 10

# Online TTS Voices
ONLINE_TTS_VOICES: List[str] = [
    'zh-CN-XiaoxiaoNeural',
    'zh-CN-XiaoyiNeural',
    'zh-CN-YunjianNeural',
    'zh-CN-YunxiNeural',
    'zh-CN-YunxiaNeural',
    'zh-CN-YunyangNeural',
    'zh-CN-liaoning-XiaobeiNeural',
    'zh-CN-shaanxi-XiaoniNeural',
]

# InfoBar Durations (milliseconds)
INFO_BAR_DURATIONS: Dict[str, int] = {
    'success': 2000,
    'warning': 3000,
    'error': -1,  # Persistent
    'update': 5000
}

# Update URLs
GITHUB_URL = "https://github.com/pth2000"
GITEE_URL = "https://gitee.com/pth2000"
UPDATE_API_URL = "https://gitee.com/api/v5/repos/pth2000/PowerPointReviewer/releases/latest"

# UI Text Constants
UI_TEXT = {
    'status': {
        'imported': '已导入',
        'not_imported': '未导入',
        'playing': '播放',
        'stopped': '停止',
        'countdown': '倒计时'
    },
    'messages': {
        'import_cancelled': '导入已取消',
        'reselect_file': '请重新选择文件进行导入。',
        'conversion_complete': '转换完成',
        'ready_to_play': '语音播放功能已准备就绪。',
        'import_script_first': '请先导入演讲稿。',
        'file_selection_cancelled': '文件选择已取消',
        'directory_selection_cancelled': '目录选择已取消',
        'reselect_directory': '请重新选择保存目录。'
    }
}

def ensure_temp_directories():
    """Ensure temporary directories exist."""
    for directory in [TEMP_DIR, COUNTDOWN_TEMP_DIR]:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)