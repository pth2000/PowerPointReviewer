"""模型目录请求、可编辑选择及异步请求生命周期。"""

import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from app.ai import client
from app.settings_store import AppSettings
from qt_test_case import QtTestCase
from test_user_messages import make_response
from ui.dialogs.llm_config_dialog import LLMConfigMessageBox


class ModelsClientTests(unittest.TestCase):
    def test_addresses_authentication_and_model_ids_are_normalized(self):
        response = make_response(200, '{"data":[{"id":"z-model"},{"id":" Alpha "},'
                                 '{"id":"z-model"},{"id":null},{"name":"ignored"}]}')
        for base in ('https://example.invalid/v1', 'https://example.invalid/v1/',
                     'https://example.invalid/v1/chat/completions/'):
            with self.subTest(base=base), patch('requests.get', return_value=response) as request:
                self.assertEqual(client.list_models(base_url=base, api_key=' fixture-key '),
                                 ['Alpha', 'z-model'])
                self.assertEqual(request.call_args.args[0], 'https://example.invalid/v1/models')
                self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-key')
                self.assertEqual(request.call_args.kwargs['timeout'], (5, 15))

    def test_local_service_does_not_require_a_key_or_selected_model(self):
        with patch('requests.get', return_value=make_response(200, '{"data":[]}')) as request:
            self.assertEqual(client.list_models(base_url='http://localhost:1234/v1', api_key=''), [])
        self.assertNotIn('Authorization', request.call_args.kwargs['headers'])

    def test_http_failure_preserves_raw_response_and_manual_fallback(self):
        for status in (401, 403, 404, 405, 429, 500):
            with self.subTest(status=status), \
                 patch('requests.get', return_value=make_response(status, 'original response fixture')):
                with self.assertRaises(client.LLMError) as caught:
                    client.list_models(base_url='https://example.invalid/v1', api_key='')
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.raw_response, 'original response fixture')
                if status in (404, 405):
                    self.assertIn('手动填写', str(caught.exception))

    def test_network_and_invalid_response_failures_keep_diagnostics(self):
        with patch('requests.get', side_effect=requests.ConnectionError('network fixture')):
            with self.assertRaisesRegex(client.LLMError, 'network fixture'):
                client.list_models(base_url='http://localhost/v1', api_key='')
        for body in ('<html>error</html>', '[]', '{}', '{"data":{}}', '{"data":[{"name":"bad"}]}'):
            with self.subTest(body=body), patch('requests.get', return_value=make_response(200, body)):
                with self.assertRaises(client.LLMError) as caught:
                    client.list_models(base_url='https://example.invalid/v1', api_key='')
                self.assertEqual(caught.exception.raw_response, body)


class ModelsDialogTests(QtTestCase):
    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(1000, 800)
        self.settings = AppSettings()
        self.settings.set('llm_base_url', 'https://example.invalid/v1')
        self.settings.set('llm_model', 'manual-model')
        self.dialog = LLMConfigMessageBox(self.settings, self.parent)
        self.notice = QWidget(self.dialog)
        self.notice.addWidget = Mock()
        self.dialog._info = Mock(return_value=self.notice)

    def tearDown(self):
        for task in (self.dialog.test_thread, self.dialog.models_thread):
            if task is not None:
                task.requestInterruption()
                task.wait(3000)
        self.qt.processEvents()
        self.dialog.deleteLater()
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_request(self):
        deadline = time.monotonic() + 5
        while self.dialog._busy():
            QTest.qWait(5)
            self.assertLess(time.monotonic(), deadline)

    def test_fetch_is_async_and_keeps_manual_model_until_explicitly_selected(self):
        released = threading.Event()
        def fetch(**kwargs):
            released.wait(2)
            return ['model-a', 'model-b']
        with patch('tasks.rewrite_task.llm_client.list_models', side_effect=fetch) as request:
            self.dialog.models_button.click()
            self.assertTrue(self.dialog._busy())
            self.assertFalse(self.dialog.models_button.isEnabled())
            self.assertFalse(self.dialog.test_button.isEnabled())
            self.assertFalse(self.dialog.yesButton.isEnabled())
            # 界面仍可处理编辑，返回时保留用户最新输入。
            self.dialog.model_edit.setText('typed-while-loading')
            released.set()
            self.wait_request()
            request.assert_called_once()
        self.assertEqual(self.dialog.model_edit.text(), 'typed-while-loading')
        self.assertEqual(self.dialog.model_edit.count(), 2)
        self.dialog.model_edit.setCurrentIndex(1)
        self.assertEqual(self.dialog.current_config()['model'], 'model-b')
        self.assertEqual(self.settings.get('llm_model'), 'manual-model')
        self.dialog.apply_to_settings()
        self.assertEqual(self.settings.get('llm_model'), 'model-b')
        self.assertTrue(self.dialog.models_button.isEnabled())

    def test_changing_connection_clears_choices_but_preserves_manual_entry(self):
        self.dialog.on_models_finish(True, ['model-a'])
        self.dialog.base_url_edit.setText('https://other.invalid/v1')
        self.assertEqual(self.dialog.model_edit.count(), 0)
        self.assertEqual(self.dialog.model_names.stringList(), [])
        self.assertEqual(self.dialog.model_edit.text(), 'manual-model')

    def test_selected_model_can_still_be_tested_and_buttons_restore_afterwards(self):
        with patch('tasks.rewrite_task.llm_client.test_connection', return_value='就绪') as request:
            self.dialog.test_connection()
            self.wait_request()
        self.assertEqual(request.call_args.kwargs['model'], 'manual-model')
        self.dialog._info.assert_called_with('success', '连接正常', '模型回复：就绪')
        self.assertTrue(self.dialog.test_button.isEnabled())
        self.assertTrue(self.dialog.models_button.isEnabled())
        self.assertTrue(self.dialog.yesButton.isEnabled())

    def test_response_from_previous_connection_is_ignored(self):
        released = threading.Event()
        def fetch(**kwargs):
            released.wait(2)
            return ['wrong-provider-model']
        with patch('tasks.rewrite_task.llm_client.list_models', side_effect=fetch):
            self.dialog.fetch_models()
            self.dialog.api_key_edit.setText('changed-fixture-key')
            released.set()
            self.wait_request()
        self.assertEqual(self.dialog.model_edit.count(), 0)
        self.assertEqual(self.dialog.model_edit.text(), 'manual-model')
        self.dialog._info.assert_not_called()

    def test_fetch_failure_and_empty_result_keep_manual_entry_and_details(self):
        for result in ([], client.LLMError('服务未提供模型列表', response=make_response(404, 'original fixture'))):
            with self.subTest(result=result), patch('tasks.rewrite_task.llm_client.list_models') as request:
                if isinstance(result, Exception):
                    request.side_effect = result
                else:
                    request.return_value = result
                self.dialog.fetch_models()
                self.wait_request()
                self.assertEqual(self.dialog.model_edit.text(), 'manual-model')
                self.assertTrue(self.dialog.models_button.isEnabled())
                self.assertTrue(self.dialog.yesButton.isEnabled())
        bar = self.dialog._info.return_value
        detail_button = bar.addWidget.call_args.args[0]
        with patch.object(self.dialog, '_show_error_details') as details:
            detail_button.click()
        self.assertIn('original fixture', details.call_args.args[0])

    def test_cancel_waits_for_worker_exit_and_does_not_persist_settings(self):
        released = threading.Event()
        def fetch(**kwargs):
            released.wait(2)
            return ['model-a']
        with patch('tasks.rewrite_task.llm_client.list_models', side_effect=fetch):
            self.parent.show()
            self.dialog.show()
            self.dialog.fetch_models()
            self.dialog.cancelButton.click()
            self.assertIsNotNone(self.dialog._pending_result)
            self.assertTrue(self.dialog.isVisible())
            self.assertTrue(self.dialog.models_thread.isInterruptionRequested())
            released.set()
            self.wait_request()
            QTest.qWait(150)
        self.assertFalse(self.dialog.isVisible())
        self.assertFalse(self.dialog.models_button.isEnabled())
        self.assertEqual(self.settings.get('llm_model'), 'manual-model')

