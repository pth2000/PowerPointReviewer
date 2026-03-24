"""千问声音复刻引擎：基于 MultiModalConversation 合成 + customization 音色管理"""

import base64
import os
import re
from pathlib import Path
from typing import Any

import requests
import dashscope


_REGION_HTTP_BASE = {
    'cn-beijing': 'https://dashscope.aliyuncs.com/api/v1',
    'intl-singapore': 'https://dashscope-intl.aliyuncs.com/api/v1',
}


def _http_base(region: str) -> str:
    return _REGION_HTTP_BASE.get(region, _REGION_HTTP_BASE['cn-beijing'])


def _get_api_key(api_key: str) -> str:
    key = (api_key or '').strip()
    if not key:
        key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    if not key:
        raise RuntimeError('未配置 API Key，请在设置页填写或设置环境变量 DASHSCOPE_API_KEY')
    return key


def _sanitize_preferred_name(value: str) -> str:
    text = re.sub(r'[^0-9A-Za-z_]', '_', (value or '').strip())
    return (text or 'ppt_reviewer')[:16]


def _read_audio_data_uri(file_path: str, audio_mime_type: str) -> str:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError(f'参考音频不存在：{file_path}')

    b64 = base64.b64encode(path.read_bytes()).decode('utf-8')
    return f'data:{audio_mime_type};base64,{b64}'


def _parse_voice_list(payload: Any) -> list[dict[str, str]]:
    output = payload.get('output', {}) if isinstance(payload, dict) else {}
    voice_list = output.get('voice_list', []) if isinstance(output, dict) else []
    result: list[dict[str, str]] = []
    if not isinstance(voice_list, list):
        return result

    for item in voice_list:
        if not isinstance(item, dict):
            continue
        voice = str(item.get('voice', '')).strip()
        if not voice:
            continue
        result.append({
            'voice': voice,
            'target_model': str(item.get('target_model', '')).strip(),
            'gmt_create': str(item.get('gmt_create', '')).strip(),
        })
    return result


def list_voices(*, api_key: str = '', region: str = 'cn-beijing', page_size: int = 100, page_index: int = 0) -> list[dict[str, str]]:
    key = _get_api_key(api_key)
    url = f"{_http_base(region)}/services/audio/tts/customization"

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

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'查询音色失败: {resp.status_code}, {resp.text}')

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f'查询音色响应解析失败: {e}') from e

    return _parse_voice_list(data)


def create_voice(*,
                 reference_audio_path: str,
                 target_model: str,
                 preferred_name: str,
                 audio_mime_type: str,
                 api_key: str = '',
                 region: str = 'cn-beijing',
                 text: str = '',
                 language: str = '') -> str:
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
        raise RuntimeError(f'创建音色失败: {resp.status_code}, {resp.text}')

    try:
        data = resp.json()
        output = data.get('output', {}) if isinstance(data, dict) else {}
        voice = str(output.get('voice', '')).strip()
    except Exception as e:
        raise RuntimeError(f'创建音色响应解析失败: {e}') from e

    if not voice:
        raise RuntimeError('创建音色失败：响应中未返回 voice')
    return voice


def delete_voice(*, voice: str, api_key: str = '', region: str = 'cn-beijing') -> None:
    key = _get_api_key(api_key)
    target_voice = (voice or '').strip()
    if not target_voice:
        raise RuntimeError('删除音色失败：voice 不能为空')

    url = f"{_http_base(region)}/services/audio/tts/customization"
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
        raise RuntimeError(f'删除音色失败: {resp.status_code}, {resp.text}')


def _extract_audio_url(response: Any) -> str:
    # 兼容 SDK 返回对象/字典
    output = getattr(response, 'output', None)
    if output is not None:
        audio = getattr(output, 'audio', None)
        if audio is not None:
            url = getattr(audio, 'url', None)
            if isinstance(url, str) and url.strip():
                return url.strip()

    if isinstance(response, dict):
        output_dict = response.get('output', {})
        if isinstance(output_dict, dict):
            audio_dict = output_dict.get('audio', {})
            if isinstance(audio_dict, dict):
                url = audio_dict.get('url', '')
                if isinstance(url, str) and url.strip():
                    return url.strip()

    # 某些 SDK 返回对象可转 dict
    try:
        if hasattr(response, 'to_dict'):
            return _extract_audio_url(response.to_dict())
    except Exception:
        pass

    raise RuntimeError(f'语音合成失败：响应中未找到音频URL，response={response}')


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
         request_timeout: int = 60) -> None:
    """使用千问语音合成接口输出音频文件到本地。"""
    key = _get_api_key(api_key)
    target_voice = (voice or '').strip()
    if not target_voice:
        raise RuntimeError('未选择复刻音色，请先创建或选择音色后再试')

    base_http = _http_base(region)
    dashscope.base_http_api_url = base_http

    kwargs: dict[str, Any] = {
        'model': model,
        'api_key': key,
        'text': text,
        'voice': target_voice,
        'language_type': language_type,
        'stream': False,
    }

    if instructions.strip():
        kwargs['instructions'] = instructions.strip()
        kwargs['optimize_instructions'] = bool(optimize_instructions)

    response = dashscope.MultiModalConversation.call(**kwargs)
    audio_url = _extract_audio_url(response)

    resp = requests.get(audio_url, timeout=max(int(request_timeout), 10))
    if resp.status_code != 200:
        raise RuntimeError(f'下载合成音频失败: {resp.status_code}, url={audio_url}')

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
