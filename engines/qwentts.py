"""连接 qwentts.cpp 本地 HTTP 服务，兼容上游与 Panda-Panta Windows 分支。"""

import math
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from app import audio_processing


DEFAULT_BASE_URL = 'http://127.0.0.1:8080'
DEFAULT_SEED = 42
DEFAULT_TEMPERATURE = 0.9


def is_base_model(model: str) -> bool:
    """识别常见模型 ID、GGUF 文件名与启动器别名中的 Base 类型。"""
    return bool(re.search(r'(?:^|[^a-z0-9])base(?:$|[^a-z0-9])', str(model).lower()))


class ServiceError(RuntimeError):
    """保留 HTTP 状态，便于处理服务明确拒绝的可选参数。"""

    def __init__(self, response):
        self.status_code = response.status_code
        super().__init__(_describe_error(response))


def normalize_base_url(value: str) -> str:
    """接受服务根地址或 /v1 地址，统一为服务根地址。"""
    value = str(value or '').strip().rstrip('/')
    try:
        parts = urlsplit(value)
        valid_port = parts.port
    except ValueError as exc:
        raise RuntimeError('服务端口不正确，请填写例如 http://127.0.0.1:8080 的地址。') from exc
    if (parts.scheme not in ('http', 'https') or not parts.hostname or
            parts.username is not None or parts.password is not None or parts.query or parts.fragment):
        raise RuntimeError('服务地址不正确，请填写例如 http://127.0.0.1:8080 的地址。')
    path = parts.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    if path.endswith('/audio/speech'):
        raise RuntimeError('请填写服务根地址，而非语音合成接口地址。')
    host = parts.hostname.lower()
    if host == '0.0.0.0':
        host = '127.0.0.1'
    if ':' in host:
        host = f'[{host}]'
    authority = host + (f':{valid_port}' if valid_port is not None else '')
    return urlunsplit((parts.scheme, authority, path, '', ''))


def _describe_error(response) -> str:
    message = ''
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get('error')
            if isinstance(error, dict):
                message = str(error.get('message') or '')
            elif isinstance(error, str):
                message = error
            message = message or str(payload.get('message') or '')
    except (ValueError, TypeError):
        pass
    return (message[:500] if message else
            f'本地 TTS 服务返回 HTTP {response.status_code}，请确认端口指向 qwentts.cpp 服务。')


def _request(session, method: str, url: str, *, timeout: int, **kwargs):
    import requests

    try:
        response = session.request(method, url, timeout=(3, max(int(timeout), 1)), **kwargs)
    except requests.exceptions.Timeout as exc:
        raise RuntimeError('本地 TTS 请求超时。请检查服务状态；合成较慢时可增大请求超时。') from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError('无法连接本地 TTS，请先启动 qwentts.cpp 服务，并确认服务地址与端口。') from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f'本地 TTS 请求失败：{exc}') from exc
    if response.status_code != 200:
        raise ServiceError(response)
    return response


def _json(session, url: str, *, timeout: int):
    response = _request(session, 'GET', url, timeout=timeout)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError('服务返回了无效 JSON，请确认填写的是 qwentts.cpp 后端端口。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('本地 TTS 服务响应格式不正确。')
    return payload


def _model(session, base_url: str, *, timeout: int) -> str:
    payload = _json(session, base_url + '/v1/models', timeout=timeout)
    models = payload.get('data')
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict):
        raise RuntimeError('未读到已加载的模型，请在 qwentts.cpp 启动器中选择模型并启动服务。')
    model = str(models[0].get('id') or '').strip()
    if not model:
        raise RuntimeError('本地 TTS 服务未返回模型名称。')
    return model


def _session():
    import requests

    session = requests.Session()
    # 本地或局域网服务直接连接，避免系统代理将请求送往互联网。
    session.trust_env = False
    return session


def query_service(base_url: str, *, timeout: int = 5, cancelled=None) -> dict:
    """检查服务并读取模型和音色；只读操作，不触发模型推理。"""
    base_url = normalize_base_url(base_url)

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise RuntimeError('连接测试已取消')

    with _session() as session:
        check_cancelled()
        health = _json(session, base_url + '/health', timeout=timeout)
        if health.get('status') != 'ok':
            raise RuntimeError('本地 TTS 服务尚未就绪，请检查启动器中的状态。')
        check_cancelled()
        model = _model(session, base_url, timeout=timeout)
        check_cancelled()
        payload = _json(session, base_url + '/v1/audio/voices', timeout=timeout)
        items = payload.get('voices')
        if not isinstance(items, list):
            raise RuntimeError('本地 TTS 服务未返回有效音色列表。')
        voices = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            if name and name not in voices:
                voices.append(name)
        check_cancelled()
        return {'base_url': base_url, 'model': model, 'voices': voices}


def check_model(base_url: str, expected_model: str) -> None:
    """在使用缓存前确认模型身份，避免换模型后复用旧音频。"""
    if not expected_model:
        raise RuntimeError('请先在设置页点击「测试连接」，读取本地模型与音色后再生成语音。')
    base_url = normalize_base_url(base_url)
    with _session() as session:
        current = _model(session, base_url, timeout=5)
    if current != expected_model:
        raise RuntimeError('本地 TTS 模型已变化，请在设置页重新测试连接、选择音色并保存设置。')


def save(text: str, path: str, *, base_url: str = DEFAULT_BASE_URL, model: str = '',
         voice: str = '', instructions: str = '', request_timeout: int = 300,
         seed: int = DEFAULT_SEED, temperature: float = DEFAULT_TEMPERATURE,
         speed: float = audio_processing.DEFAULT_SPEED,
         leading_silence_ms: int = audio_processing.DEFAULT_LEADING_SILENCE_MS,
         trailing_silence_ms: int = audio_processing.DEFAULT_TRAILING_SILENCE_MS) -> None:
    """请求完整 WAV，校验音频后落盘；语言使用服务端启动时的设置。"""
    base_url = normalize_base_url(base_url)
    if not str(text or '').strip():
        raise RuntimeError('待合成文本不能为空。')
    audio_processing.validate_options(speed, leading_silence_ms, trailing_silence_ms)
    if not isinstance(seed, int) or isinstance(seed, bool) or not -1 <= seed <= 2147483647:
        raise RuntimeError('随机种子须为 -1 到 2147483647 的整数；-1 表示每次随机。')
    if (not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or
            not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise RuntimeError('采样随机性须为 0 到 2 之间的数值。')
    check_model(base_url, model)
    body = {'input': text, 'model': model, 'response_format': 'wav',
            'seed': seed, 'temperature': temperature}
    if voice:
        body['voice'] = voice
    if instructions.strip() and not is_base_model(model):
        body['instructions'] = instructions.strip()
    with _session() as session:
        try:
            response = _request(session, 'POST', base_url + '/v1/audio/speech',
                                timeout=request_timeout, json=body)
        except ServiceError as exc:
            # 用户可自定义模型别名；类型无法从名称辨认时，只处理明确的 Base 指令拒绝。
            if ('instructions' not in body or exc.status_code not in (400, 422) or
                    not re.search(r'\binstruct(?:ions)?\s+is\s+not\s+supported\s+for\s+base\s+models\b',
                                  str(exc), flags=re.IGNORECASE)):
                raise
            body.pop('instructions')
            response = _request(session, 'POST', base_url + '/v1/audio/speech',
                                timeout=request_timeout, json=body)
        content = response.content
    content = audio_processing.process_wav(content, speed=speed,
                                           leading_silence_ms=leading_silence_ms,
                                           trailing_silence_ms=trailing_silence_ms)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
