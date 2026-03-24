"""引擎注册表：包含所有引擎的 schema 定义和内置音色列表"""

# 所有引擎的统一定义，供 TTSEngine 管理器和设置 UI 使用
ENGINE_DEFS = [
    {
        'id': 'local',
        'name': '本地 · TTSx3',
        'description': 'TTSx3引擎离线可用，具有较高的稳定性和较快的速度，适合不依赖网络的场景，缺点是音色较为单一。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 0,
        'retry_delay': 0.0,
        'options': [
            {
                'key': 'rate', 'label': '语音速率', 'description': '控制语音播放快慢，数值越大越快',
                'type': 'int', 'min': 50, 'max': 500, 'step': 10, 'default': 200
            },
            {
                'key': 'volume', 'label': '音量强度', 'description': '控制语音音量，0 为静音，1 为最大',
                'type': 'float', 'min': 0.0, 'max': 1.0, 'step': 0.01, 'default': 1.0
            }
        ]
    },
    {
        'id': 'edge',
        'name': '在线 · Edge-TTS',
        'description': 'Edge-TTS引擎需联网使用，音色较为自然，无需额外配置，但在网络不稳定时可能出现失败。',
        'supports_voice': True,
        'parallel_enabled': True,
        'parallel_workers': 4,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'rate', 'label': '语音速率', 'description': '控制语音播放快慢，数值越大越快',
                'type': 'int', 'min': 50, 'max': 500, 'step': 10, 'default': 200
            },
            {
                'key': 'volume', 'label': '音量增益', 'description': '在线引擎音量增益，建议 0.5~1.0',
                'type': 'float', 'min': 0.0, 'max': 1.0, 'step': 0.01, 'default': 1.0
            },
            {
                'key': 'pitch', 'label': '音调偏移', 'description': '单位：Hz，正值更高，负值更低',
                'type': 'int', 'min': -50, 'max': 50, 'step': 1, 'default': 0
            }
        ]
    },
    {
        'id': 'bailian',
        'name': '在线 · 阿里百炼CosyVoice',
        'description': '百炼引擎需联网使用，需配置API Key，提供丰富的音色和较高的语音质量，但需要额外的账号配置和可能的使用成本。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'api_key', 'label': 'API Key', 'description': '阿里百炼访问密钥（必填）',
                'type': 'password', 'default': ''
            },
            {
                'key': 'model', 'label': '模型', 'description': '百炼语音模型',
                'type': 'choice', 'choices': ['cosyvoice-v3-flash', 'cosyvoice-v3-plus', 'cosyvoice-v2', 'cosyvoice-v1'],
                'default': 'cosyvoice-v3-flash'
            },
            {
                'key': 'rate', 'label': '语速倍率', 'description': '范围 0.5~2.0，1.0 为默认',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0
            },
            {
                'key': 'volume', 'label': '音量', 'description': '范围 0~100，50 为默认',
                'type': 'int', 'min': 0, 'max': 100, 'step': 1, 'default': 50
            },
            {
                'key': 'pitch', 'label': '音调倍率', 'description': '范围 0.5~2.0，1.0 为默认',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0
            },
            {
                'key': 'ws_url', 'label': '服务地址', 'description': '默认北京地址；新加坡请改为 dashscope-intl 域名',
                'type': 'choice',
                'choices': [
                    'wss://dashscope.aliyuncs.com/api-ws/v1/inference',
                    'wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference'
                ],
                'default': 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
            }
        ]
    },
    {
        'id': 'qwen_clone',
        'name': '在线 · 千问音色复刻',
        'description': '千问引擎需联网，需配置API Key，支持使用云端保存的复刻音色进行语音合成。可在设置页创建、刷新、删除音色。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'api_key', 'label': 'API Key', 'description': '千问 API Key（可留空，留空时尝试环境变量 DASHSCOPE_API_KEY）',
                'type': 'password', 'default': ''
            },
            {
                'key': 'region', 'label': '地域', 'description': '北京/新加坡，必须与 API Key 所属地域一致',
                'type': 'choice', 'choices': ['cn-beijing', 'intl-singapore'], 'default': 'cn-beijing'
            },
            {
                'key': 'model', 'label': '合成模型', 'description': '复刻音色推荐使用 qwen3-tts-vc',
                'type': 'choice', 'choices': ['qwen3-tts-vc-2026-01-22'], 'default': 'qwen3-tts-vc-2026-01-22'
            },
            {
                'key': 'language_type', 'label': '语言类型', 'description': '建议与文本语言一致（Chinese/English）',
                'type': 'choice', 'choices': ['Chinese', 'English'], 'default': 'Chinese'
            },
            {
                'key': 'voice', 'label': '当前音色', 'description': '当前使用的复刻音色（自动填入）',
                'type': 'text', 'default': ''
            },
            {
                'key': 'preferred_name', 'label': '默认音色名', 'description': '创建音色时使用的默认名称（字母数字下划线，<=16）',
                'type': 'text', 'default': 'ppt_reviewer'
            },
            {
                'key': 'reference_audio_path', 'label': '参考音频路径', 'description': '创建音色时使用的本地音频路径（10~20秒推荐）',
                'type': 'text', 'default': ''
            },
            {
                'key': 'audio_mime_type', 'label': '参考音频MIME', 'description': '参考音频 MIME 类型',
                'type': 'choice', 'choices': ['audio/mpeg', 'audio/wav', 'audio/mp4'], 'default': 'audio/mpeg'
            },
            {
                'key': 'request_timeout', 'label': '请求超时(秒)', 'description': '网络请求超时设置',
                'type': 'int', 'min': 10, 'max': 180, 'step': 5, 'default': 60
            }
        ]
    }
]


