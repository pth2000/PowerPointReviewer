"""引擎注册表：包含所有引擎的 schema 定义和内置音色列表"""

from engines.qwen_clone import AUDIO_LANGUAGE_TYPES, AUDIO_TTS_MODELS, VC_MODELS

# Auto 表示由模型自行判断；旧 VC 模型在运行期使用较小的语言集合。
QWEN_LANGUAGE_TYPES = AUDIO_LANGUAGE_TYPES

# 注册表同时驱动 TTSEngine 的调度策略和设置页的动态表单。
ENGINE_DEFS = [
    {
        'id': 'local',
        'name': '本地 · TTSx3',
        'description': '使用 Windows 自带的语音合成，无需联网',
        'help': '音色取决于系统已安装的语音包。安装 NaturalVoiceSAPIAdapter 后，可使用微软自然语音。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 0,
        'retry_delay': 0.0,
        'options': [
            {
                'key': 'rate', 'label': '语音速率', 'description': '调整朗读的快慢',
                'help': '取值 50–500，默认 200。数值越大，语速越快。',
                'type': 'int', 'min': 50, 'max': 500, 'step': 10, 'default': 200
            },
            {
                'key': 'volume', 'label': '音量强度', 'description': '调整朗读的音量',
                'help': '取值 0–1，默认 1，即最大音量。',
                'type': 'float', 'min': 0.0, 'max': 1.0, 'step': 0.01, 'default': 1.0
            }
        ]
    },
    {
        'id': 'edge',
        'name': '在线 · Edge-TTS',
        'description': '使用微软 Edge 的在线语音合成，音色自然',
        'help': '无需账号与配置，需要联网。网络不稳定时合成可能失败，失败的语句会自动重试。',
        'supports_voice': True,
        'parallel_enabled': True,
        'parallel_workers': 4,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'locale', 'label': '语言 / 地区',
                'description': '按语言和地区筛选发音人',
                'help': '朗读外语讲稿时，切换到对应的语言和地区，再选择发音人。',
                'type': 'choice', 'choices_provider': 'edge_locales',
                'choices': ['zh-CN', 'en-US'], 'default': 'zh-CN',
                'rebuild_voices': True
            },
            {
                'key': 'rate', 'label': '语音速率', 'description': '调整朗读的快慢',
                'help': '取值 50–500，200 为原速，400 约为两倍速，100 约为半速。',
                'type': 'int', 'min': 50, 'max': 500, 'step': 10, 'default': 200
            },
            {
                'key': 'volume', 'label': '音量增益', 'description': '在原音量的基础上调整音量',
                'help': '取值 0–1，1 为原音量，0.5 约为原音量的一半。',
                'type': 'float', 'min': 0.0, 'max': 1.0, 'step': 0.01, 'default': 1.0
            },
            {
                'key': 'pitch', 'label': '音调偏移', 'description': '在原音调的基础上调整音调',
                'help': '单位为赫兹，默认 0。正值升高，负值降低。',
                'type': 'int', 'min': -50, 'max': 50, 'step': 1, 'default': 0
            }
        ]
    },
    {
        'id': 'bailian',
        'name': '在线 · 阿里百炼 CosyVoice',
        'description': '使用阿里云百炼 CosyVoice 语音合成，音色丰富',
        'help': '需要阿里云百炼账号与 API Key，按用量计费。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'api_key', 'label': 'API Key', 'description': '用于访问阿里云百炼服务',
                'help': '必填，可在阿里云百炼控制台创建。',
                'type': 'password', 'default': ''
            },
            {
                'key': 'model', 'label': '模型', 'description': '选择用于合成的 CosyVoice 模型',
                'help': '不同模型可用的音色不同，切换模型后需重新选择发音人。',
                'type': 'choice', 'choices': ['cosyvoice-v3-flash', 'cosyvoice-v3-plus', 'cosyvoice-v2', 'cosyvoice-v1'],
                'default': 'cosyvoice-v3-flash', 'rebuild_voices': True
            },
            {
                'key': 'rate', 'label': '语速倍率', 'description': '按倍数调整音色的原始语速',
                'help': '取值 0.5–2.0，默认 1.0。小于 1 变慢，大于 1 变快。',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0
            },
            {
                'key': 'volume', 'label': '音量', 'description': '调整朗读的音量',
                'help': '取值 0–100，默认 50。',
                'type': 'int', 'min': 0, 'max': 100, 'step': 1, 'default': 50
            },
            {
                'key': 'pitch', 'label': '音调倍率', 'description': '按倍数调整音色的原始音调',
                'help': '取值 0.5–2.0，默认 1.0。小于 1 变低，大于 1 变高。',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0
            },
            {
                'key': 'ws_url', 'label': '服务地址', 'description': '选择百炼语音服务的接入地址',
                'help': '默认为北京地域。API Key 属于新加坡地域时，选择 dashscope-intl 地址。',
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
        'description': '使用千问复刻的专属音色合成语音',
        'help': '在「复刻音色管理」中上传一段参考音频即可创建音色。音色保存在云端，可反复使用。需要阿里云百炼账号与 API Key。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 2,
        'retry_delay': 0.8,
        'options': [
            {
                'key': 'api_key', 'label': 'API Key', 'description': '用于访问阿里云百炼服务',
                'help': '留空时读取环境变量 DASHSCOPE_API_KEY。',
                'type': 'password', 'default': ''
            },
            {
                'key': 'region', 'label': '地域', 'description': '选择百炼服务所在的地域',
                'help': '需与 API Key 所属地域一致。',
                'type': 'choice', 'choices': ['cn-beijing', 'intl-singapore'], 'default': 'cn-beijing',
                'rebuild_voices': True
            },
            {
                'key': 'model', 'label': '合成模型',
                'description': '选择用于复刻与合成的千问模型',
                'help': '3.1 Flash 是新增版本，支持指令和情感标签。Qwen-Audio-TTS 非实时合成需填写工作空间 ID 并使用北京地域。音色与模型绑定，更换模型后需重新复刻或选择对应音色。',
                'type': 'choice',
                'choices': list(AUDIO_TTS_MODELS + VC_MODELS),
                'default': AUDIO_TTS_MODELS[0], 'rebuild_voices': True
            },
            {
                'key': 'workspace_id', 'label': '工作空间 ID',
                'description': '指定所使用的百炼业务空间',
                'help': 'Qwen-Audio-TTS 系列必填，可在百炼控制台的业务空间管理中查看。旧 Qwen3-TTS-VC 可留空。',
                'type': 'text', 'default': ''
            },
            {
                'key': 'language_type', 'label': '语言类型',
                'description': '指定讲稿所使用的语言',
                'help': '默认 Auto，由模型自动识别。指定与讲稿一致的语言可提高发音准确度。',
                'type': 'choice', 'choices': list(QWEN_LANGUAGE_TYPES),
                'choices_provider': 'qwen_languages', 'default': 'Auto'
            },
            {
                'key': 'instructions', 'label': '语音风格指令',
                'description': '用文字控制语气、情绪、方言和朗读风格',
                'help': '例如「用自然、清晰的讲解语气，语速稍慢」。留空使用默认风格。也可在讲稿中使用 [excited]、[laughter] 等标签。',
                'type': 'text', 'default': '', 'supported_models': AUDIO_TTS_MODELS
            },
            {
                'key': 'rate', 'label': '语速倍率', 'description': '调整合成语音的快慢',
                'help': '取值 0.5–2.0，默认 1.0。',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0,
                'supported_models': AUDIO_TTS_MODELS
            },
            {
                'key': 'volume', 'label': '音量', 'description': '调整合成语音的音量',
                'help': '取值 0–100，默认 50。',
                'type': 'int', 'min': 0, 'max': 100, 'step': 1, 'default': 50,
                'supported_models': AUDIO_TTS_MODELS
            },
            {
                'key': 'pitch', 'label': '音调倍率', 'description': '调整合成语音的音调',
                'help': '取值 0.5–2.0，默认 1.0。',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.1, 'default': 1.0,
                'supported_models': AUDIO_TTS_MODELS
            },
            {
                'key': 'voice', 'label': '当前音色', 'description': '当前使用的复刻音色',
                'type': 'text', 'default': ''
            },
            {
                'key': 'preferred_name', 'label': '默认音色名', 'description': '为新建的复刻音色命名',
                'help': 'Qwen-Audio-TTS 使用小写字母和数字，最多 9 位；旧 Qwen3-TTS-VC 支持字母、数字和下划线，最多 16 位。',
                'type': 'text', 'default': 'ppt_reviewer'
            },
            {
                'key': 'reference_audio_path', 'label': '参考音频路径', 'description': '本地音频文件',
                'help': '建议 10–20 秒、无背景噪音的清晰人声。',
                'type': 'text', 'default': ''
            },
            {
                'key': 'audio_mime_type', 'label': '参考音频类型', 'description': '参考音频 MIME 类型',
                'type': 'choice', 'choices': ['audio/mpeg', 'audio/wav', 'audio/mp4'], 'default': 'audio/mpeg'
            },
            {
                'key': 'request_timeout', 'label': '请求超时（秒）', 'description': '设置等待服务响应的最长时间',
                'help': '取值 10–180，默认 60。网络较慢时可适当调大。',
                'type': 'int', 'min': 10, 'max': 180, 'step': 5, 'default': 60
            }
        ]
    },
    {
        'id': 'qwentts',
        'name': '本地 · qwentts.cpp',
        'description': '连接本机或局域网的 qwentts.cpp 服务，离线合成语音',
        'help': '先在 qwentts.cpp 启动器中选择模型和语言并启动服务，再填写后端地址、测试连接和选择音色。兼容原始项目及 Panda-Panta Windows 便携版。复刻音色可在服务网页中创建，再刷新音色列表。',
        'supports_voice': True,
        'parallel_enabled': False,
        'parallel_workers': 1,
        'retry_times': 0,
        'retry_delay': 0.0,
        'options': [
            {
                'key': 'base_url', 'label': '服务地址',
                'description': '填写本地 TTS 后端地址与端口',
                'help': '默认 http://127.0.0.1:8080，也接受以 /v1 结尾的地址。填写后点击「测试连接」。',
                'type': 'text', 'default': 'http://127.0.0.1:8080'
            },
            {
                'key': 'model', 'label': '当前模型',
                'type': 'text', 'default': ''
            },
            {
                'key': 'voice', 'label': '当前音色',
                'type': 'text', 'default': ''
            },
            {
                'key': 'instructions', 'label': '语音风格指令',
                'description': '为支持声音设计的模型提供朗读风格',
                'help': '1.7B CustomVoice 可选填，0.6B CustomVoice 不支持指令控制。VoiceDesign 需要填写声音描述；Base 声音克隆模型请留空。语言在服务启动器中设置。风格指令不能保证不同讲稿段落的声音完全一致。',
                'type': 'text', 'default': ''
            },
            {
                'key': 'speed', 'label': '语速倍率',
                'description': '调整生成音频的朗读速度，保持音高',
                'help': '取值 0.5–2.0，默认 1.0。小于 1 放慢，大于 1 加快。试听、连播和导出使用相同语速。',
                'type': 'float', 'min': 0.5, 'max': 2.0, 'step': 0.05, 'default': 1.0
            },
            {
                'key': 'seed', 'label': '随机种子',
                'description': '固定同一文本重复生成时的随机选择',
                'help': '默认 42，相同文本、音色和参数重复生成可复现。-1 表示每次随机。固定种子不能保证不同文本的语调完全相同。',
                'type': 'int', 'min': -1, 'max': 2147483647, 'step': 1, 'default': 42
            },
            {
                'key': 'temperature', 'label': '采样随机性',
                'description': '调整生成声音时的随机程度',
                'help': '取值 0–2，默认 0.9。调低可减少采样随机性，但不一定改善音质；0 使用确定的最优候选。此项不是语速控制。',
                'type': 'float', 'min': 0.0, 'max': 2.0, 'step': 0.1, 'default': 0.9
            },
            {
                'key': 'request_timeout', 'label': '请求超时（秒）',
                'description': '设置等待本地语音合成的最长时间',
                'help': '默认 300 秒。CPU 或旧显卡生成较慢时可适当调大，最长 1800 秒。',
                'type': 'int', 'min': 10, 'max': 1800, 'step': 10, 'default': 300
            }
        ]
    }
]

# 所有模式共用留白定义，各模式分别保存参数；不覆盖已有配置。
for engine in ENGINE_DEFS:
    format_help = ' Edge 启用留白后输出 WAV，文件体积会增大。' if engine['id'] == 'edge' else ''
    for key, label, position in (('leading_silence_ms', '开头留白（毫秒）', '开头'),
                                 ('trailing_silence_ms', '结尾留白（毫秒）', '结尾')):
        engine['options'].append({
            'key': key, 'label': label,
            'description': f'保证每段音频{position}至少保留这段停顿',
            'help': '取值 0–3000，默认 0，不追加留白。已有足够停顿时不再追加；原有停顿会保留。' + format_help,
            'type': 'int', 'min': 0, 'max': 3000, 'step': 50, 'default': 0,
        })


