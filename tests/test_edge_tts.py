"""Edge 上游传输、参数传递与错误报告回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import WSServerHandshakeError

from engines import edge_tts as engine


class EdgeTransportTests(unittest.TestCase):
    def test_save_preserves_upstream_endpoint_and_voice_parameters(self):
        import edge_tts.communicate as transport

        endpoint = 'wss://example.invalid/upstream-synthesis'

        def communicate(*args, **kwargs):
            self.assertEqual(transport.WSS_URL, endpoint)
            self.assertEqual(args, ('测试文字', 'zh-CN-XiaoxiaoNeural'))
            self.assertEqual(kwargs, {'rate': '+10%', 'volume': '-25%', 'pitch': '+3Hz'})
            return SimpleNamespace(save=save)

        save = AsyncMock()
        with patch.object(transport, 'WSS_URL', endpoint), \
             patch('edge_tts.Communicate', side_effect=communicate):
            engine.save('测试文字', 'test.mp3', voice='zh-CN-XiaoxiaoNeural', rate=220, volume=.75, pitch=3)
        save.assert_awaited_once_with('test.mp3')

    def test_403_has_readable_message_and_preserves_original_error(self):
        error = WSServerHandshakeError(None, (), status=403, message='Invalid response status')
        with patch('edge_tts.Communicate',
                   return_value=SimpleNamespace(save=AsyncMock(side_effect=error))):
            with self.assertRaisesRegex(RuntimeError, 'Edge.*403') as raised:
                engine.save('测试', 'test.mp3', voice='zh-CN-XiaoxiaoNeural')
        self.assertIs(raised.exception.__cause__, error)

    def test_other_handshake_errors_are_not_misreported_as_403(self):
        error = WSServerHandshakeError(None, (), status=503, message='Service unavailable')
        with patch('edge_tts.Communicate',
                   return_value=SimpleNamespace(save=AsyncMock(side_effect=error))):
            with self.assertRaises(WSServerHandshakeError) as raised:
                engine.save('测试', 'test.mp3', voice='zh-CN-XiaoxiaoNeural')
        self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
