"""通过 Qt WebSocket 控制本次导出独占的 OBS 实例。"""

import base64
import hashlib
import json
import logging
import time
import uuid

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtWebSockets import QWebSocket

from app.video.mux import MuxCancelled


class ObsRequestError(RuntimeError):
    def __init__(self, kind, status):
        self.code = status.get('code')
        self.request = kind
        self.details = str(status.get('comment', self.code))
        messages = {'StartRecord': 'OBS 无法开始录制，请检查显卡驱动和保存位置后重试。',
                    'StopRecord': 'OBS 视频尚未保存完整，请检查剩余空间后重试。'}
        super().__init__(messages.get(kind, 'OBS 无法完成录制操作，请重新启动导出。'))


class ObsClient:
    def __init__(self, port, password, cancelled=lambda: False, timeout=5):
        self.cancelled, self.timeout = cancelled, timeout
        self.messages = []
        self.error = ''
        self.socket = QWebSocket()
        self.socket.textMessageReceived.connect(self._message)
        self.socket.errorOccurred.connect(lambda _code: self._error())
        self.socket.disconnected.connect(self._error)
        self.socket.open(QUrl(f'ws://127.0.0.1:{port}'))
        try:
            hello = self._receive(0)
            identify = {'rpcVersion': 1, 'eventSubscriptions': 0}
            if 'authentication' in hello:
                auth = hello['authentication']
                secret = base64.b64encode(hashlib.sha256((password + auth['salt']).encode()).digest())
                identify['authentication'] = base64.b64encode(
                    hashlib.sha256(secret + auth['challenge'].encode()).digest()).decode()
            self._send(1, identify)
            self._receive(2)
        except Exception:
            self.close()
            raise

    def _message(self, message):
        try:
            payload = json.loads(message)
            if not isinstance(payload, dict) or not isinstance(payload.get('d'), dict):
                raise ValueError('invalid response')
            self.messages.append(payload)
        except (ValueError, TypeError):
            self.error = 'OBS 返回了无法识别的响应。'

    def _error(self):
        self.error = '无法连接 OBS Studio，请重新启动录制。'

    def _send(self, op, data):
        self.socket.sendTextMessage(json.dumps({'op': op, 'd': data}))

    def _receive(self, op, identifier=None):
        deadline = time.monotonic() + self.timeout
        loop, timer = QEventLoop(), QTimer()
        timer.setInterval(10)
        timer.timeout.connect(loop.quit)
        self.socket.textMessageReceived.connect(loop.quit)
        self.socket.errorOccurred.connect(loop.quit)
        timer.start()
        try:
            while time.monotonic() < deadline:
                if self.cancelled():
                    raise MuxCancelled()
                if self.error:
                    raise RuntimeError(self.error)
                while self.messages:
                    message = self.messages.pop(0)
                    data = message.get('d', {})
                    if message.get('op') == op and (identifier is None or data.get('requestId') == identifier):
                        return data
                loop.exec()
            raise RuntimeError('OBS Studio 响应超时，请检查录制是否正常运行。')
        finally:
            timer.stop()
            self.socket.textMessageReceived.disconnect(loop.quit)
            self.socket.errorOccurred.disconnect(loop.quit)

    def call(self, kind, **data):
        identifier = uuid.uuid4().hex
        self._send(6, {'requestType': kind, 'requestId': identifier, 'requestData': data})
        response = self._receive(7, identifier)
        if not response.get('requestStatus', {}).get('result'):
            status = response.get('requestStatus', {})
            log = logging.getLogger(__name__)
            (log.debug if status.get('code') == 207 else log.error)('OBS 请求失败：%s；状态=%s', kind, status)
            raise ObsRequestError(kind, response.get('requestStatus', {}))
        return response.get('responseData', {})

    def close(self):
        self.socket.abort()
