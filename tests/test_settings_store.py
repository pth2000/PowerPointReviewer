"""设置实例隔离与配置读写边界，全部使用临时配置。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from qt_test_case import QtTestCase

from app.settings_store import AppSettings, ConfigStore


class SettingsIsolationTests(unittest.TestCase):
    def test_legacy_countdown_duration_is_clamped_when_loaded_and_saved(self):
        settings = AppSettings()
        settings.load_from({'countdown_seconds': 30})
        self.assertEqual(settings.get('countdown_seconds'), 10)
        self.assertEqual(settings.to_dict()['countdown_seconds'], 10)
        settings.set('countdown_seconds', -1)
        self.assertEqual(settings.get('countdown_seconds'), 1)
        settings.load_from({'countdown_seconds': 0})
        self.assertEqual(settings.get('countdown_seconds'), 1)
        self.assertEqual(settings.to_dict()['countdown_seconds'], 1)

    def test_nested_defaults_are_not_shared_between_settings_instances(self):
        defaults = copy.deepcopy(AppSettings.DEFAULTS)
        first = AppSettings()
        second = AppSettings()

        first.get('page_turn_window')['title'] = 'first instance only'
        first.get('hotkeys').clear()
        first.get('llm_styles')[0]['name'] = 'first instance preset'

        self.assertEqual(second.get('page_turn_window'), defaults['page_turn_window'])
        self.assertEqual(second.get('hotkeys'), defaults['hotkeys'])
        self.assertEqual(second.get('llm_styles'), defaults['llm_styles'])
        self.assertEqual(AppSettings.DEFAULTS, defaults)


class ConfigBoundaryTests(QtTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'settings.json'
        self.engine = Mock()
        self.settings = AppSettings()
        self.store = ConfigStore(self.path, self.engine, self.settings)

    def test_non_object_configuration_is_rejected_without_changing_settings(self):
        original = self.settings.to_dict()
        for value in ([], None, 12, 'invalid'):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertLogs('pptreviewer', level='ERROR') as logs:
                    self.assertFalse(self.store.load())
                self.assertIn('配置读取失败', logs.output[0])
                self.assertEqual(self.settings.to_dict(), original)
                self.engine.import_persistent_state.assert_not_called()

    def test_engine_restore_error_returns_failure_and_logs_cause(self):
        self.path.write_text('{"app": {"resume_last_session": false}}', encoding='utf-8')
        self.engine.import_persistent_state.side_effect = ValueError('engine restore fixture')
        with self.assertLogs('pptreviewer', level='ERROR') as logs:
            self.assertFalse(self.store.load())
        self.assertIn('engine restore fixture', logs.output[0])
        self.assertTrue(self.settings.get('resume_last_session'))

    def test_state_serialization_error_does_not_overwrite_previous_configuration(self):
        self.path.write_text('{"previous": true}', encoding='utf-8')
        self.engine.export_persistent_state.side_effect = ValueError('engine export fixture')
        with self.assertLogs('pptreviewer', level='ERROR') as logs:
            self.assertFalse(self.store.save())
        self.assertIn('engine export fixture', logs.output[0])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), {'previous': True})

    def test_valid_configuration_round_trip_preserves_engine_and_app_values(self):
        self.engine.export_persistent_state.return_value = {'engine': 'fixture'}
        self.settings.set('resume_last_session', False)
        self.assertTrue(self.store.save())

        restored = AppSettings()
        store = ConfigStore(self.path, self.engine, restored)
        self.assertTrue(store.load())
        self.assertFalse(restored.get('resume_last_session'))
        self.engine.import_persistent_state.assert_called_once_with(
            {'engine': 'fixture', 'app': self.settings.to_dict()}
        )
