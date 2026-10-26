"""确认后台配音失败既返回界面消息，也保留完整诊断。"""

from unittest.mock import Mock, patch
from pathlib import Path
import tempfile

from qt_test_case import QtTestCase
from tasks.audio_generation_task import AudioGenerationTask
from tasks.preview_task import PreviewTask
from app.settings_store import AppSettings, ConfigStore


class TaskLoggingTests(QtTestCase):
    def test_config_parse_failure_keeps_full_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('{', encoding='utf-8')
            store = ConfigStore(path, Mock(), AppSettings())
            with self.assertLogs('pptreviewer', level='ERROR') as logs:
                self.assertFalse(store.load())
            self.assertIn('配置读取失败', logs.output[0])
            self.assertIn('JSONDecodeError', logs.output[0])

    def test_config_write_failure_is_logged_and_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('previous fixture', encoding='utf-8')
            engine = Mock()
            engine.export_persistent_state.return_value = {}
            store = ConfigStore(path, engine, AppSettings())
            with patch.object(Path, 'replace', side_effect=PermissionError('fixture read-only')), \
                 self.assertLogs('pptreviewer', level='ERROR') as logs:
                self.assertFalse(store.save())
            self.assertIn('配置保存失败', logs.output[0])
            self.assertIn('PermissionError', logs.output[0])
            self.assertEqual(path.read_text(encoding='utf-8'), 'previous fixture')

    def test_preview_failure_keeps_traceback_and_error_signal(self):
        engine = Mock()
        engine.snapshot.return_value = engine
        engine.save_file.side_effect = RuntimeError('service failure fixture')
        task = PreviewTask(engine, 'fixture', 'unused.wav')
        errors = []
        task.signal_error.connect(errors.append)
        with self.assertLogs('pptreviewer', level='ERROR') as logs:
            task.run()
        self.assertEqual(errors, ['service failure fixture'])
        self.assertIn('试听配音生成失败', logs.output[0])
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertIn('Traceback', logs.output[0])

    def test_generation_failure_keeps_traceback_and_error_signal(self):
        task = AudioGenerationTask(Mock())
        errors = []
        task.signal_error.connect(errors.append)
        with patch.object(task, 'save_countdown_wav'), \
             patch.object(task, 'save_wav', side_effect=OSError('output full fixture')), \
             self.assertLogs('pptreviewer', level='ERROR') as logs:
            task.run()
        self.assertEqual(errors, ['output full fixture'])
        self.assertIn('讲稿配音生成失败', logs.output[0])
        self.assertIsNotNone(logs.records[0].exc_info)

