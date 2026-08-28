"""通过 OpenAI 兼容的 Chat Completions 接口调用大模型。

本项目只使用固定的非流式请求结构，直接复用 ``requests`` 可避免额外 SDK 依赖。
``base_url`` 应包含服务的版本路径，例如 ``https://api.openai.com/v1``。
"""

import json

# requests 导入约 250 毫秒，AI 改写属于低频功能，推迟到调用点导入。


class LLMError(RuntimeError):
    """表示请求、服务响应或响应解析失败。"""


def _endpoint(base_url: str) -> str:
    """规范化服务根地址并补全 Chat Completions 路径。"""
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        raise LLMError('未配置服务地址（base_url）')
    if base.endswith('/chat/completions'):
        return base
    return f'{base}/chat/completions'


def chat(*, base_url: str, api_key: str, model: str, messages: list[dict],
         timeout: int = 120, temperature: float = 0.4) -> str:
    """发起一次非流式对话补全，并返回去除首尾空白的回复文本。"""
    if not str(model or '').strip():
        raise LLMError('未配置模型名称')

    headers = {'Content-Type': 'application/json'}
    key = str(api_key or '').strip()
    if key:
        headers['Authorization'] = f'Bearer {key}'

    payload = {
        'model': str(model).strip(),
        'messages': messages,
        'temperature': float(temperature),
        'stream': False,
    }

    import requests

    try:
        response = requests.post(
            _endpoint(base_url), headers=headers, json=payload,
            timeout=(10, max(int(timeout), 10)))
    except requests.RequestException as e:
        raise LLMError(f'请求失败：{e}') from e

    if response.status_code != 200:
        detail = response.text[:300].replace('\n', ' ')
        raise LLMError(f'服务返回 {response.status_code}：{detail}')

    try:
        data = response.json()
        content = data['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError) as e:
        detail = response.text[:300].replace('\n', ' ')
        raise LLMError(f'响应解析失败（{e}）：{detail}') from e

    if not isinstance(content, str):
        # 部分兼容服务返回内容分段列表，将其中的 text 字段展平成统一字符串。
        try:
            content = ''.join(
                part.get('text', '') for part in content if isinstance(part, dict))
        except TypeError:
            content = json.dumps(content, ensure_ascii=False)

    return content.strip()


def test_connection(*, base_url: str, api_key: str, model: str, timeout: int = 30) -> str:
    """用最小请求验证连接参数，并返回模型回复。"""
    return chat(
        base_url=base_url, api_key=api_key, model=model, timeout=timeout, temperature=0.0,
        messages=[{'role': 'user', 'content': '回复两个字：就绪'}],
    )
