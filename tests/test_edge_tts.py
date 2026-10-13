"""Edge 服务地址兼容、参数传递与错误报告回归。"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import WSServerHandshakeError
from engines import edge_tts as engine


class EdgeTransportTests(unittest.TestCase):
    def test_legacy_synthesis_address_migrates_and_voice_catalog_is_untouched(self):
        import edge_tts.communicate as transport
        from edge_tts.constants import VOICE_LIST
        old = 'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=test'
        with patch.object(transport, 'WSS_URL', old):
            engine._prepare_synthesis_endpoint()
            self.assertTrue(transport.WSS_URL.startswith('wss://api.msedgeservices.com/tts/'))
            self.assertIn('Ocp-Apim-Subscription-Key=', transport.WSS_URL)
            changed = transport.WSS_URL
            engine._prepare_synthesis_endpoint()
            self.assertEqual(transport.WSS_URL, changed)
        from edge_tts.constants import VOICE_LIST as after
        self.assertEqual(after, VOICE_LIST)

    def test_future_upstream_endpoint_is_preserved(self):
        import edge_tts.communicate as transport
        updated = 'wss://api.msedgeservices.com/tts/new-upstream-endpoint?key=value'
        with patch.object(transport, 'WSS_URL', updated):
            engine._prepare_synthesis_endpoint()
            self.assertEqual(transport.WSS_URL, updated)

    def test_save_uses_compatibility_endpoint_and_keeps_voice_parameters(self):
        import edge_tts.communicate as transport
        def communicate(*args, **kwargs):
            self.assertTrue(transport.WSS_URL.startswith('wss://api.msedgeservices.com/tts/'))
            self.assertEqual(args, ('测试文字', 'zh-CN-XiaoxiaoNeural'))
            self.assertEqual(kwargs, dict(rate='+10%', volume='-25%', pitch='+3Hz'))
            return SimpleNamespace(save=save)
        save = AsyncMock()
        old = 'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?key=value'
        with patch.object(transport, 'WSS_URL', old), patch('edge_tts.Communicate', side_effect=communicate):
            engine.save('测试文字', 'test.mp3', voice='zh-CN-XiaoxiaoNeural', rate=220, volume=.75, pitch=3)
        save.assert_awaited_once_with('test.mp3')

    def test_403_has_readable_message_and_preserves_original_error(self):
        error = WSServerHandshakeError(None, (), status=403, message='Invalid response status')
        with patch.object(engine, '_prepare_synthesis_endpoint'), \
             patch('edge_tts.Communicate', return_value=SimpleNamespace(save=AsyncMock(side_effect=error))):
            with self.assertRaisesRegex(RuntimeError, 'Edge.*403') as raised:
                engine.save('测试', 'test.mp3', voice='zh-CN-XiaoxiaoNeural')
        self.assertIs(raised.exception.__cause__, error)

    def test_other_handshake_errors_are_not_misreported_as_403(self):
        error = WSServerHandshakeError(None, (), status=503, message='Service unavailable')
        with patch.object(engine, '_prepare_synthesis_endpoint'), \
             patch('edge_tts.Communicate', return_value=SimpleNamespace(save=AsyncMock(side_effect=error))):
            with self.assertRaises(WSServerHandshakeError) as raised:
                engine.save('测试', 'test.mp3', voice='zh-CN-XiaoxiaoNeural')
        self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
