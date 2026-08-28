"""千问声音复刻引擎：customization 接口管理音色，MultiModalConversation 接口合成语音"""

import base64
import os
import re
from pathlib import Path
from typing import Any


# dashscope 导入耗时接近一秒，只有实际调用千问时才需要，推迟到调用点导入。


_REGION_HTTP_BASE = {
    'cn-beijing': 'https://dashscope.aliyuncs.com/api/v1',
    'intl-singapore': 'https://dashscope-intl.aliyuncs.com/api/v1',
}


# 千问复刻分为两个模型系列，注册与合成接口互不通用：
# - Qwen3-TTS-VC：qwen-voice-enrollment 注册（参考音频内联为 base64），
#   MultiModalConversation 合成，走 dashscope 公共端点，支持北京与新加坡。
# - Qwen-Audio-TTS：voice-enrollment 注册（参考音频只收 URL，本地文件先传 OSS），
#   SpeechSynthesizer 合成，走工作空间专属的 MaaS 端点，仅北京地域。
AUDIO_TTS_MODELS = ('qwen-audio-3.0-tts-plus', 'qwen-audio-3.0-tts-flash')

VC_MODELS = ('qwen3-tts-vc-2026-01-22',)

def is_audio_tts(model: str) -> bool:
    """判断模型是否属于 Qwen-Audio-TTS 系列"""
    return str(model or '').strip() in AUDIO_TTS_MODELS


def _maas_base(workspace_id: str) -> str:
    """Qwen-Audio-TTS 的合成端点按工作空间划分，且仅在北京地域提供"""
    workspace = str(workspace_id or '').strip()
    if not workspace:
        raise RuntimeError('使用 Qwen-Audio-TTS 需要填写工作空间 ID，请在设置页填写后重试。')
    return f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1'


def _http_base(region: str) -> str:
    """返回地域对应的 HTTP API 根地址，未知地域回退到北京。"""
    return _REGION_HTTP_BASE.get(region, _REGION_HTTP_BASE['cn-beijing'])


def _get_api_key(api_key: str) -> str:
    """解析显式或环境变量中的 API Key，均为空时抛出可操作错误。"""
    key = (api_key or '').strip()
    if not key:
        key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    if not key:
        raise RuntimeError('未配置 API Key，请在设置页填写或设置环境变量 DASHSCOPE_API_KEY')
    return key


def _sanitize_prefix(value: str) -> str:
    """voice-enrollment 的 prefix 只允许小写字母与数字，且需短于十个字符"""
    text = re.sub(r'[^0-9a-z]', '', (value or '').strip().lower())
    return (text or 'pptr')[:9]


def _sanitize_preferred_name(value: str) -> str:
    """将音色名称规整为服务端允许的 16 位字母数字下划线格式。"""
    text = re.sub(r'[^0-9A-Za-z_]', '_', (value or '').strip())
    return (text or 'ppt_reviewer')[:16]


def _read_audio_data_uri(file_path: str, audio_mime_type: str) -> str:
    """读取本地参考音频，并编码为 customization 接口接受的 Data URI。"""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError(f'参考音频不存在：{file_path}')

    b64 = base64.b64encode(path.read_bytes()).decode('utf-8')
    return f'data:{audio_mime_type};base64,{b64}'


# 只映射处理方式明确的服务错误码；未知错误优先保留服务端原始说明。
_CODE_HINTS = {
    'AllocationQuota.FreeTierOnly':
        '免费额度已用完。请在阿里云百炼控制台充值开通付费，或关闭账号的"仅用免费额度"限制后重试。',
    'Arrearage': '账号已欠费，请在阿里云百炼控制台充值后重试。',
    'InvalidApiKey': 'API Key 无效，请在设置页核对后重试。',
    'Throttling': '请求过于频繁，请稍候重试。',
}

_STATUS_HINTS = {
    400: '请求被拒绝，请检查参考音频与参数是否符合要求。',
    401: 'API Key 无效或已过期，请在设置页重新填写。',
    403: '账号无权访问该服务，请确认已在阿里云百炼控制台开通并有可用额度。',
    404: '接口地址不存在，请确认所选服务地域是否正确。',
    429: '请求过于频繁，请稍候重试。',
}


def _describe_http_error(resp) -> str:
    """将百炼 HTTP 错误转换为面向用户的可操作说明。

    完整响应仅写入日志，以保留 request ID 等排障信息而不在界面回显原始 JSON。
    """
    code = message = request_id = ''
    try:
        data = resp.json()
        if isinstance(data, dict):
            code = str(data.get('code', '') or '').strip()
            message = str(data.get('message', '') or '').strip()
            request_id = str(data.get('request_id') or data.get('requestId') or '').strip()
    except Exception:
        pass

    print(f'[qwen_clone] HTTP {resp.status_code} code={code or "-"} '
          f'request_id={request_id or "-"} body={resp.text}')

    for key, hint in _CODE_HINTS.items():
        if code == key or code.startswith(key + '.'):
            return hint

    # 未知错误码的服务端消息通常比 HTTP 状态通用文案更具体。
    if message:
        return f'{message}（错误码 {code}）' if code else message

    hint = _STATUS_HINTS.get(resp.status_code)
    if hint:
        return hint
    return f'服务返回异常状态 {resp.status_code}，请稍候重试。'


def _describe_response_error(response) -> str:
    """从 MultiModalConversation 失败响应中提取适合界面展示的错误。"""
    def _pick(name):
        return str(response.get(name, '') or '').strip()

    code, message, status = _pick('code'), _pick('message'), _pick('status_code')
    print(f'[qwen_clone] 合成失败 status={status or "-"} code={code or "-"} message={message}')

    for key, hint in _CODE_HINTS.items():
        if code == key or code.startswith(key + '.'):
            return hint

    if message:
        return f'{message}（错误码 {code}）' if code else message
    return '语音合成失败，服务未返回音频，请稍候重试。'


def _parse_voice_list(payload: Any) -> list[dict[str, str]]:
    """从 customization 响应中提取规范化的音色详情列表。"""
    output = payload.get('output', {}) if isinstance(payload, dict) else {}
    voice_list = output.get('voice_list', []) if isinstance(output, dict) else []
    result: list[dict[str, str]] = []
    if not isinstance(voice_list, list):
        return result

    for item in voice_list:
        if not isinstance(item, dict):
            continue
        # qwen-voice-enrollment 返回 voice，voice-enrollment 返回 voice_id
        voice = str(item.get('voice') or item.get('voice_id') or '').strip()
        if not voice:
            continue
        result.append({
            'voice': voice,
            'target_model': str(item.get('target_model', '')).strip(),
            'gmt_create': str(item.get('gmt_create', '')).strip(),
            'status': str(item.get('status', '')).strip(),
        })
    return result


def list_voices(*, api_key: str = '', region: str = 'cn-beijing', page_size: int = 100,
                page_index: int = 0, target_model: str = '') -> list[dict[str, str]]:
    """分页查询指定地域下当前账户创建的复刻音色。

    两个系列的音色分别登记在各自的注册服务中，因此按 target_model 决定查询哪一边。
    voice-enrollment 的返回项未必带 target_model，缺少该字段时不做过滤。
    """
    import requests


    key = _get_api_key(api_key)
    url = f"{_http_base(region)}/services/audio/tts/customization"

    if is_audio_tts(target_model):
        payload = {
            'model': 'voice-enrollment',
            'input': {
                'action': 'list_voice',
                'page_size': int(page_size),
                'page_index': int(page_index),
            }
        }
    else:
        payload = {
            'model': 'qwen-voice-enrollment',
            'input': {
                'action': 'list',
                'page_size': int(page_size),
                'page_index': int(page_index),
            }
        }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }

    # 该方法可能由设置页同步调用，连接与读取超时需保持较短以限制界面冻结时间。
    resp = requests.post(url, json=payload, headers=headers, timeout=(5, 15))
    if resp.status_code != 200:
        raise RuntimeError(_describe_http_error(resp))

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f'查询音色响应解析失败: {e}') from e

    items = _parse_voice_list(data)
    wanted = str(target_model or '').strip()
    if wanted:
        tagged = [item for item in items if item['target_model']]
        if tagged:
            return [item for item in tagged if item['target_model'] == wanted]
    return items


def _upload_reference_audio(file_path: str, *, model: str, api_key: str) -> str:
    """把本地参考音频上传到百炼的临时 OSS，返回 oss:// 形式的地址。

    voice-enrollment 只接受 URL 形式的参考音频，而桌面端选中的是本地文件，
    因此注册前先上传。请求该地址时需附带 X-DashScope-OssResourceResolve 头。
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError(f'参考音频不存在：{file_path}')

    from dashscope.utils.oss_utils import OssUtils

    try:
        url, _certificate = OssUtils.upload(model=model, file_path=str(path), api_key=api_key)
    except Exception as e:
        raise RuntimeError(f'参考音频上传失败：{e}') from e

    if not url:
        raise RuntimeError('参考音频上传失败，未获得可用地址，请稍候重试。')
    return url


def _create_voice_audio_tts(*, reference_audio_path, target_model, preferred_name,

                            api_key, region, language) -> str:
    """Qwen-Audio-TTS 系列的音色注册"""
    import requests

    key = _get_api_key(api_key)
    audio_url = _upload_reference_audio(reference_audio_path, model='voice-enrollment', api_key=key)

    body_input: dict[str, Any] = {
        'action': 'create_voice',
        'target_model': target_model,
        'prefix': _sanitize_prefix(preferred_name),
        'url': audio_url,
    }
    if language.strip() and language.strip() != 'Auto':
        body_input['language_hints'] = [language.strip()]

    resp = requests.post(
        f"{_http_base(region)}/services/audio/tts/customization",
        json={'model': 'voice-enrollment', 'input': body_input},
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'X-DashScope-OssResourceResolve': 'enable',
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(_describe_http_error(resp))

    try:
        output = resp.json().get('output', {})
        voice = str(output.get('voice_id') or output.get('voice') or '').strip()
    except Exception as e:
        raise RuntimeError(f'创建音色响应解析失败: {e}') from e

    if not voice:
        raise RuntimeError('创建音色失败：响应中未返回音色 ID')
    return voice


def create_voice(*,

                 reference_audio_path: str,
                 target_model: str,
                 preferred_name: str,
                 audio_mime_type: str,
                 api_key: str = '',
                 region: str = 'cn-beijing',
                 text: str = '',
                 language: str = '') -> str:
    """上传参考音频创建复刻音色，并返回服务端分配的音色 ID。

    两个系列的注册接口不同：Qwen-Audio-TTS 需要先把音频传到 OSS 再提交地址，
    Qwen3-TTS-VC 则直接把音频内联为 base64 提交。
    """
    import requests

    if is_audio_tts(target_model):
        return _create_voice_audio_tts(
            reference_audio_path=reference_audio_path,
            target_model=target_model,
            preferred_name=preferred_name,
            api_key=api_key,
            region=region,
            language=language,
        )

    key = _get_api_key(api_key)
    url = f"{_http_base(region)}/services/audio/tts/customization"

    data_uri = _read_audio_data_uri(reference_audio_path, audio_mime_type)
    body_input: dict[str, Any] = {
        'action': 'create',
        'target_model': target_model,
        'preferred_name': _sanitize_preferred_name(preferred_name),
        'audio': {'data': data_uri},
    }
    if text.strip():
        body_input['text'] = text.strip()
    if language.strip():
        body_input['language'] = language.strip()

    payload = {
        'model': 'qwen-voice-enrollment',
        'input': body_input,
    }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(_describe_http_error(resp))

    try:
        data = resp.json()
        output = data.get('output', {}) if isinstance(data, dict) else {}
        voice = str(output.get('voice', '')).strip()
    except Exception as e:
        raise RuntimeError(f'创建音色响应解析失败: {e}') from e

    if not voice:
        raise RuntimeError('创建音色失败：响应中未返回 voice')
    return voice


def delete_voice(*, voice: str, api_key: str = '', region: str = 'cn-beijing',

                 target_model: str = '') -> None:
    """删除指定地域下的复刻音色；空音色 ID 直接拒绝。"""
    import requests

    key = _get_api_key(api_key)
    target_voice = (voice or '').strip()
    if not target_voice:
        raise RuntimeError('删除音色失败：voice 不能为空')

    url = f"{_http_base(region)}/services/audio/tts/customization"
    if is_audio_tts(target_model):
        payload = {
            'model': 'voice-enrollment',
            'input': {
                'action': 'delete_voice',
                'voice_id': target_voice,
            }
        }
    else:
        payload = {
            'model': 'qwen-voice-enrollment',
            'input': {
                'action': 'delete',
                'voice': target_voice,
            }
        }
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(_describe_http_error(resp))


def _extract_audio_url(response: Any) -> str:
    """从合成响应中提取临时音频下载地址。

    dashscope 响应继承自 ``dict``，其 ``__getattr__`` 在字段缺失时会抛 ``KeyError``，
    因此必须使用字典接口读取可选字段。
    """
    output = response.get('output') or {}
    url = str((output.get('audio') or {}).get('url', '') or '').strip()
    if not url:
        raise RuntimeError(_describe_response_error(response))
    return url


def _save_audio_tts(text: str, path: str, *, model: str, voice: str, workspace_id: str,

                    api_key: str, instructions: str, request_timeout: int) -> None:
    """Qwen-Audio-TTS 系列的非实时合成。

    该系列走工作空间专属的 MaaS 端点，与 Qwen3-TTS-VC 的公共端点不通用；
    非实时合成仅在北京地域提供。响应返回的音频地址有效期 24 小时。
    """
    import requests

    url = f'{_maas_base(workspace_id)}/services/audio/tts/SpeechSynthesizer'
    body_input: dict[str, Any] = {
        'text': text,
        'voice': voice,
        # 应用侧统一按 wav 落盘，这里直接指定，避免拿到与扩展名不符的音频
        'format': 'wav',
        'sample_rate': 24000,
    }
    if instructions.strip():
        body_input['instructions'] = instructions.strip()

    resp = requests.post(
        url,
        json={'model': model, 'input': body_input},
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        timeout=max(int(request_timeout), 10),
    )
    if resp.status_code != 200:
        raise RuntimeError(_describe_http_error(resp))

    try:
        payload = resp.json()
    except Exception as e:
        raise RuntimeError(f'语音合成响应解析失败: {e}') from e

    audio_url = _extract_audio_url(payload)
    _download_audio(audio_url, path, request_timeout=request_timeout)


def _download_audio(audio_url: str, path: str, *, request_timeout: int) -> None:
    """下载合成结果并落盘"""
    import requests


    resp = requests.get(audio_url, timeout=max(int(request_timeout), 10))
    if resp.status_code != 200:
        raise RuntimeError(f'合成音频下载失败（HTTP {resp.status_code}），请稍候重试。')

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)


def save(text: str,
         path: str,
         *,
         model: str = 'qwen3-tts-vc-2026-01-22',
         voice: str = '',
         language_type: str = 'Chinese',
         instructions: str = '',
         optimize_instructions: bool = False,
         api_key: str = '',
         region: str = 'cn-beijing',
         workspace_id: str = '',
         request_timeout: int = 60) -> None:
    """使用已复刻音色合成语音，并将临时下载结果保存到本地。"""
    key = _get_api_key(api_key)
    target_voice = (voice or '').strip()
    if not target_voice:
        raise RuntimeError('未选择复刻音色，请先创建或选择音色后再试')

    if is_audio_tts(model):
        _save_audio_tts(
            text, path,
            model=model, voice=target_voice, workspace_id=workspace_id,
            api_key=key, instructions=instructions, request_timeout=request_timeout,
        )
        return

    import dashscope

    dashscope.base_http_api_url = _http_base(region)

    kwargs: dict[str, Any] = {
        'model': model,
        'api_key': key,
        'text': text,
        'voice': target_voice,
        'stream': False,
    }
    if language_type.strip() and language_type.strip() != 'Auto':
        kwargs['language_type'] = language_type.strip()

    if instructions.strip():
        kwargs['instructions'] = instructions.strip()
        kwargs['optimize_instructions'] = bool(optimize_instructions)

    response = dashscope.MultiModalConversation.call(**kwargs)
    _download_audio(_extract_audio_url(response), path, request_timeout=request_timeout)
