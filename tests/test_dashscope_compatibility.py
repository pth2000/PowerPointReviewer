"""离线验证应用使用的真实 SDK 接口、请求参数与合成回调。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dashscope
from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer
from dashscope.utils.oss_utils import OssUtils

from engines import bailian, qwen_clone


class DashScopeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_bailian_request_and_callback_use_the_real_sdk(self):
        target = self.root / '配音.wav'
        observed = {}

        def synthesize(synthesizer, text):
            observed['text'] = text
            observed['headers'] = synthesizer.request.get_websocket_headers(None, None)
            observed['request'] = json.loads(synthesizer.request.get_start_request())
            observed['url'] = synthesizer.url
            self.assertIsInstance(synthesizer.callback, ResultCallback)
            synthesizer.callback.on_open()
            synthesizer.callback.on_data(b'RIFFtest-audio')
            synthesizer.callback.on_complete()
            self.assertIsNone(synthesizer.callback.file)
            self.assertTrue(synthesizer.callback.done_event.is_set())
            synthesizer.callback.on_close()

        with patch.object(SpeechSynthesizer, 'call', autospec=True, side_effect=synthesize), \
             patch.object(dashscope, 'api_key', None):
            bailian.save('合成测试', str(target), api_key='test-sdk-key',
                         rate=1.2, volume=65, pitch=0.9)

        self.assertEqual(target.read_bytes(), b'RIFFtest-audio')
        self.assertEqual(observed['text'], '合成测试')
        self.assertEqual(observed['headers']['Authorization'], 'Bearer test-sdk-key')
        self.assertEqual(observed['url'], bailian._DEFAULT_WS_URL)
        payload = observed['request']['payload']
        self.assertEqual(payload['model'], 'cosyvoice-v3-flash')
        parameters = payload['parameters']
        for name, expected in {'voice': 'longanyang', 'volume': 65, 'rate': 1.2,
                               'pitch': 0.9, 'format': 'wav', 'sample_rate': 22050}.items():
            self.assertEqual(parameters[name], expected, name)

    def test_bailian_sdk_callback_error_reaches_the_caller(self):
        def fail(synthesizer, _text):
            synthesizer.callback.on_error('test-service-error')
            synthesizer.callback.on_close()

        with patch.object(SpeechSynthesizer, 'call', autospec=True, side_effect=fail), \
             patch.object(dashscope, 'api_key', None):
            with self.assertRaisesRegex(RuntimeError, 'test-service-error'):
                bailian.save('合成测试', str(self.root / 'failed.wav'), api_key='test-sdk-key')

    def test_qwen_clone_uses_sdk_request_conversion_and_response_parsing(self):
        audio_url = 'https://example.invalid/test-audio.wav'
        response = DashScopeAPIResponse(status_code=200, output={'audio': {'url': audio_url}})
        target = str(self.root / '复刻.wav')
        with patch('dashscope.client.base_api.BaseApi.call', return_value=response) as send, \
             patch.object(qwen_clone, '_download_audio') as download:
            qwen_clone.save('复刻测试', target, model=qwen_clone.VC_MODELS[0],
                            voice='test-voice', api_key='test-sdk-key',
                            workspace_id='test-workspace', language_type='Chinese')

        request = send.call_args.kwargs
        self.assertEqual(request['model'], qwen_clone.VC_MODELS[0])
        self.assertEqual(request['api_key'], 'test-sdk-key')
        self.assertEqual(request['input']['text'], '复刻测试')
        self.assertEqual(request['input']['voice'], 'test-voice')
        self.assertEqual(request['input']['language_type'], 'Chinese')
        self.assertEqual(request['base_address'],
                         'https://test-workspace.cn-beijing.maas.aliyuncs.com/api/v1')
        download.assert_called_once_with(audio_url, target, request_timeout=60)

    def test_reference_upload_uses_sdk_certificate_and_file_upload(self):
        source = self.root / 'reference.wav'
        source.write_bytes(b'RIFFtest-audio')
        certificate = {
            'oss_access_key_id': 'test-oss-key', 'signature': 'test-signature',
            'policy': 'test-policy', 'upload_dir': 'temporary/sdk',
            'x_oss_object_acl': 'private', 'x_oss_forbid_overwrite': 'true',
            'upload_host': 'https://example.invalid/upload',
        }
        response = DashScopeAPIResponse(status_code=200, output=certificate)

        def upload(url, *, files, data, **kwargs):
            self.assertEqual(url, certificate['upload_host'])
            self.assertEqual(files['file'].read(), b'RIFFtest-audio')
            self.assertEqual(data['key'], 'temporary/sdk/reference.wav')
            return Mock(status_code=200)

        base_url = 'https://test-workspace.cn-beijing.maas.aliyuncs.com/api/v1'
        with patch.object(OssUtils, 'get_upload_certificate', return_value=response) as policy, \
             patch('requests.Session.post', side_effect=upload):
            result = qwen_clone._upload_reference_audio(
                str(source), model='voice-enrollment', api_key='test-sdk-key', base_url=base_url)

        self.assertEqual(result, 'oss://temporary/sdk/reference.wav')
        policy.assert_called_once_with(model='voice-enrollment', api_key='test-sdk-key',
                                       base_address=base_url)
