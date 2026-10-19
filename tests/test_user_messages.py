"""验证友好显示不会丢失服务响应，也不会改变音频格式参数。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from PySide6.QtWidgets import QWidget

from app.ai import client as llm_client
from app.settings_store import AppSettings
from engines.qwen_clone import AUDIO_TTS_MODELS
from ui.dialogs.llm_config_dialog import LLMConfigMessageBox
from ui.dialogs.qwen_clone_voice_dialog import QwenCloneVoiceDialog
from ui.dialogs.rewrite_dialog import RewriteDialog

from qt_test_case import QtTestCase


def make_response(status, body):
    response = requests.Response()
    response.status_code = status
    response.encoding = 'utf-8'
    response._content = body.encode('utf-8')
    return response


class AIResponseTests(unittest.TestCase):
    def call_chat(self):
        return llm_client.chat(base_url='https://example.invalid/v1', api_key='',
                               model='test', messages=[])

    def test_http_errors_keep_full_original_response(self):
        body = '{"error": "原始错误"}\n' + '长响应' * 300
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                with patch('requests.post', return_value=make_response(status, body)):
                    with self.assertRaises(llm_client.LLMError) as caught:
                        self.call_chat()
                error = caught.exception
                self.assertEqual(error.status_code, status)
                self.assertEqual(error.raw_response, body)
                self.assertIn(f'原始响应（HTTP {status}）：\n{body}', str(error))
                self.assertIn('请', error.user_message)

    def test_invalid_success_response_preserves_body_and_cause(self):
        body = '<html>这不是模型回复\n' + '服务错误' * 300 + '</html>'
        with patch('requests.post', return_value=make_response(200, body)):
            with self.assertRaises(llm_client.LLMError) as caught:
                self.call_chat()
        self.assertEqual(caught.exception.raw_response, body)
        self.assertIn('OpenAI 兼容接口', caught.exception.user_message)
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_network_failure_keeps_original_error_without_response(self):
        original = requests.ConnectionError('connection refused')
        with patch('requests.post', side_effect=original):
            with self.assertRaises(llm_client.LLMError) as caught:
                self.call_chat()
        self.assertIsNone(caught.exception.raw_response)
        self.assertIn('原始错误：connection refused', str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)

    def test_exception_keeps_response_even_when_response_is_false(self):
        response = make_response(503, 'service unavailable\nretry later')
        self.assertFalse(response)
        original = requests.HTTPError('failed', response=response)
        with patch('requests.post', side_effect=original):
            with self.assertRaises(llm_client.LLMError) as caught:
                self.call_chat()
        self.assertEqual(caught.exception.raw_response, response.text)
        self.assertEqual(caught.exception.status_code, 503)

    def test_normal_reply_remains_usable(self):
        response = make_response(200, '{"choices":[{"message":{"content":"  讲稿  "}}]}')
        with patch('requests.post', return_value=response):
            self.assertEqual(self.call_chat(), '讲稿')


class MessageInterfaceTests(QtTestCase):

    def make_voice_dialog(self, mime='audio/mpeg'):
        engine = Mock()
        engine.get_current_option_values.return_value = {
            'model': AUDIO_TTS_MODELS[0], 'audio_mime_type': mime,
            'preferred_name': 'lesson', 'voice': '',
        }
        engine.create_qwen_clone_voice.return_value = 'voice-test'
        with patch.object(QwenCloneVoiceDialog, 'refresh_list', lambda self, **kwargs: None):
            dialog = QwenCloneVoiceDialog(engine)
        self.addCleanup(dialog.deleteLater)
        return dialog, engine

    def test_format_labels_keep_mime_values_when_creating_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            reference = Path(folder) / 'reference.wav'
            reference.write_bytes(b'test audio')
            for label, mime in (('MP3', 'audio/mpeg'), ('WAV', 'audio/wav'), ('M4A', 'audio/mp4')):
                with self.subTest(label=label):
                    dialog, engine = self.make_voice_dialog(mime)
                    self.assertEqual(dialog.mime_combo.currentText(), label)
                    self.assertEqual(dialog.mime_combo.currentData(), mime)
                    dialog.reference_audio_edit.setText(str(reference))
                    with patch.object(dialog, '_info_success'), patch.object(dialog, 'refresh_list'):
                        dialog.create_voice()
                    engine.set_current_option.assert_any_call('audio_mime_type', mime)
                    engine.create_qwen_clone_voice.assert_called_once_with(
                        str(reference), preferred_name='lesson')

    def test_file_selection_sets_display_format_and_service_parameter(self):
        dialog, _ = self.make_voice_dialog()
        with patch('ui.dialogs.qwen_clone_voice_dialog.QFileDialog.getOpenFileName',
                   return_value=('example.m4a', '')), patch.object(dialog, '_info_success'):
            dialog.choose_reference_audio()
        self.assertEqual(dialog.mime_combo.currentText(), 'M4A')
        self.assertEqual(dialog.mime_combo.currentData(), 'audio/mp4')

    def test_rewrite_error_uses_scrollable_result_without_enabling_apply(self):
        dialog = RewriteDialog({1: '原讲稿'}, '●', AppSettings())
        self.addCleanup(dialog.deleteLater)
        message = '请检查服务设置。\n\n原始响应（HTTP 500）：\n' + '原始信息\n' * 400
        dialog.on_page_failed(1, message)
        self.assertEqual(dialog.rewritten_edit.toPlainText(), message)
        self.assertEqual(dialog.warning_label.text(), '提示：请检查服务设置。')
        self.assertTrue(dialog.rewritten_edit.isReadOnly())
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.results[1]['text'], '')

    def test_connection_notification_offers_full_details(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        dialog = LLMConfigMessageBox(AppSettings(), parent)
        self.addCleanup(dialog.deleteLater)
        message = '请检查服务设置。\n\n原始响应（HTTP 500）：\n原始内容'
        bar = QWidget(dialog)
        bar.addWidget = Mock()
        with patch.object(dialog, '_info', return_value=bar) as info:
            dialog.on_test_finish(False, message)
        info.assert_called_once_with('error', '连接失败', '请检查服务设置。')
        button = bar.addWidget.call_args.args[0]
        with patch.object(dialog, '_show_error_details') as details:
            button.click()
        details.assert_called_once_with(message)


if __name__ == '__main__':
    unittest.main()
