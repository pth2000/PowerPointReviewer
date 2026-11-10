"""设置和主页的同步翻页偏好、禁用状态及播放行为验证。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from qfluentwidgets import CaptionLabel
from qt_test_case import QtTestCase
from test_presentation_link import make_ppt

from app.audio.playback import PlaybackBus
from app.presentation import link as presentation_link
from app.presentation.com import PROG_IDS
from app.settings_store import AppSettings
from app.window import Window
from tts_engine import TTSEngine
from ui import theme
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.settings_page import SettingInterface


class SyncPlaybackFixture(QtTestCase):

    def setUp(self):
        installed = patch('ui.pages.settings_page.installed_applications', return_value=PROG_IDS)
        installed.start()
        self.addCleanup(installed.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = make_ppt(Path(folder.name) / 'lesson.pptx')
        self.page, self.settings, self.ctx = self.make_ui()

    def make_ui(self, enabled=False, ready=True, resume=True, *, countdown_enabled=True, countdown_seconds=5):
        engine = TTSEngine()
        engine.set_mode('edge')
        prefs = AppSettings()
        prefs.set('scroll_enabled', enabled)
        prefs.set('resume_last_session', resume)
        prefs.set('countdown_enabled', countdown_enabled)
        prefs.set('countdown_seconds', countdown_seconds)
        ctx = NS(version='1.6.3', tts_engine=engine, app_settings=prefs, config=Mock(),
                 playback_bus=PlaybackBus(), hotkeys=Mock(), slideshow_watcher=Mock())
        with patch('app.paths.ensure_runtime_dirs'), \
             patch.object(engine, 'get_voices_list', return_value=['zh-CN-XiaoxiaoNeural']), \
             patch('ui.theme.available_fonts', return_value=(('', theme.DEFAULT_FONT_LABEL),)):
            page = PPTReviewer(ctx)
            if ready:
                page.project.notes = {1: '第一页', 2: '第二页'}
                page.project.notes_list = [{'page': 1, 'text': '第一页'}, {'page': 2, 'text': '第二页'}]
                page.project.presentation_path = self.source
                page.project.presentation_link = presentation_link.describe(self.source)
                page.project.audio_loaded = True
                page.check_import()
            settings = SettingInterface(ctx)
        Window._bind_sync_playback(NS(ppt_r=page, setting_interface=settings))
        Window._bind_countdown(NS(ppt_r=page, setting_interface=settings))
        def cleanup():
            page.presentation_monitor.shutdown()
            page.ctx.slideshow_watcher.stop()
            settings.deleteLater()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.qt.processEvents()
        self.addCleanup(cleanup)
        ctx.config.reset_mock()
        ctx.slideshow_watcher.reset_mock()
        return page, settings, ctx

class CountdownPreferencesTests(SyncPlaybackFixture):
    def test_settings_update_home_and_save_once_without_regeneration(self):
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        before = self.ctx.tts_engine.export_persistent_state()
        position = self.page.project.current_index
        self.settings.countdown_seconds_spin.setValue(9)
        self.assertEqual(self.page.currentSpinBox.value(), 9)
        self.assertEqual(self.ctx.app_settings.get('countdown_seconds'), 9)
        self.ctx.config.save_later.assert_called_once()
        self.ctx.config.reset_mock()
        self.settings.countdown_switch.setChecked(False)
        self.assertFalse(self.page.currentSwitch.isChecked())
        self.assertFalse(self.page.currentSpinBox.isEnabled())
        self.assertFalse(self.settings.countdown_seconds_spin.isEnabled())
        self.assertTrue(self.settings.countdown_duration_row.isHidden())
        self.assertTrue(self.settings.countdown_duration_separator.isHidden())
        self.assertFalse(self.ctx.app_settings.get('countdown_enabled'))
        self.ctx.config.save_later.assert_called_once()
        self.settings.countdown_switch.setChecked(True)
        self.assertTrue(self.page.currentSpinBox.isEnabled())
        self.assertTrue(self.settings.countdown_seconds_spin.isEnabled())
        self.assertFalse(self.settings.countdown_duration_row.isHidden())
        self.assertFalse(self.settings.countdown_duration_separator.isHidden())
        self.assertEqual(self.page.currentSpinBox.value(), 9)
        self.assertEqual(self.ctx.tts_engine.export_persistent_state(), before)
        self.assertEqual(self.page.project.current_index, position)
        self.assertFalse(self.settings.is_dirty())
        regenerate.assert_not_called()

    def test_home_updates_settings_without_signal_loop(self):
        changes = Mock()
        self.settings.countdown_preferences_changed.connect(changes)
        self.page.currentSwitch.setChecked(False)
        self.assertFalse(self.settings.countdown_switch.isChecked())
        self.assertFalse(self.settings.countdown_seconds_spin.isEnabled())
        self.assertTrue(self.settings.countdown_duration_row.isHidden())
        self.ctx.config.save_later.assert_called_once()
        self.ctx.config.reset_mock()
        self.page.currentSwitch.setChecked(True)
        self.page.currentSpinBox.setValue(7)
        self.assertTrue(self.settings.countdown_switch.isChecked())
        self.assertTrue(self.settings.countdown_seconds_spin.isEnabled())
        self.assertFalse(self.settings.countdown_duration_row.isHidden())
        self.assertEqual(self.settings.countdown_seconds_spin.value(), 7)
        self.assertEqual(self.ctx.config.save_later.call_count, 2)
        changes.assert_not_called()

    def test_saved_countdown_preferences_restore_before_import(self):
        page, settings, ctx = self.make_ui(ready=False, countdown_enabled=False, countdown_seconds=8)
        self.assertFalse(page.currentSwitch.isChecked())
        self.assertFalse(settings.countdown_switch.isChecked())
        self.assertEqual(settings.countdown_seconds_spin.value(), 8)
        self.assertFalse(settings.countdown_seconds_spin.isEnabled())
        self.assertTrue(settings.countdown_duration_row.isHidden())
        self.assertTrue(settings.countdown_duration_separator.isHidden())
        self.assertTrue(settings.countdown_switch.isEnabled())
        ctx.config.save_later.assert_not_called()
        settings.countdown_switch.setChecked(True)
        self.assertTrue(page.currentSwitch.isChecked())
        self.assertEqual(page.currentSpinBox.value(), 8)
        self.assertFalse(page.currentSpinBox.isEnabled())
        self.assertFalse(settings.countdown_duration_row.isHidden())
        self.assertFalse(settings.countdown_duration_separator.isHidden())
        ctx.config.save_later.assert_called_once()

    def test_duration_limits_match_home(self):
        control = self.settings.countdown_seconds_spin
        self.assertEqual((control.minimum(), control.maximum()),
                         (self.page.currentSpinBox.minimum(), self.page.currentSpinBox.maximum()))
        control.setValue(100)
        self.assertEqual(self.page.currentSpinBox.value(), 10)
        self.assertEqual(self.ctx.app_settings.get('countdown_seconds'), 10)
        control.setValue(-1)
        self.assertEqual(self.page.currentSpinBox.value(), 1)
        self.assertEqual(self.ctx.app_settings.get('countdown_seconds'), 1)


class QwenttsFeedbackTests(SyncPlaybackFixture):

    def setUp(self):
        super().setUp()
        self.ctx.tts_engine.set_mode('qwentts')
        self.settings.engineSelectComboBox.setCurrentIndex(self.ctx.tts_engine.get_mode_index())
        self.address = 'http://192.0.2.10:8080'
        self.settings.dynamic_option_widgets['base_url'].setText(self.address)
        self.descriptions = [label.text() for label in self.settings._qwentts_card.findChildren(CaptionLabel)]

    def assert_descriptions_unchanged(self):
        self.assertEqual([label.text() for label in self.settings._qwentts_card.findChildren(CaptionLabel)],
                         self.descriptions)

    def test_connection_results_use_notifications_and_keep_descriptions(self):
        with patch('ui.pages.settings_page.LocalTtsConnectTask') as factory, \
                patch.object(self.settings, 'create_success_info_bar') as success, \
                patch.object(self.settings, 'create_error_info_bar') as error:
            task = factory.return_value
            task.base_url = self.address
            task.isInterruptionRequested.return_value = False
            for voices in (['Voice A', 'Voice B'], []):
                self.settings.test_qwentts_connection()
                self.assertFalse(self.settings._qwentts_test_button.isEnabled())
                self.assertEqual(self.settings._qwentts_test_button.text(), '连接中…')
                self.assert_descriptions_unchanged()
                self.settings.on_qwentts_connected(
                    {'base_url': self.address, 'model': 'Qwen3-TTS-Base', 'voices': voices})
                self.settings.finish_qwentts_connection()
                self.assertTrue(self.settings._qwentts_test_button.isEnabled())
                self.assertEqual(self.settings._qwentts_test_button.text(), '测试连接')
                self.assertIn('Qwen3-TTS-Base', success.call_args.args[1])
                self.assertIn('2 个音色' if voices else '模型默认声音', success.call_args.args[1])
                self.assert_descriptions_unchanged()
            self.settings.test_qwentts_connection()
            self.settings.on_qwentts_connection_error('HTTP 503: service unavailable')
            self.settings.finish_qwentts_connection()
            error.assert_called_once_with('连接失败', 'HTTP 503: service unavailable')
            self.assert_descriptions_unchanged()

    def test_old_address_results_do_not_notify_or_replace_current_options(self):
        with patch('ui.pages.settings_page.LocalTtsConnectTask') as factory, \
                patch.object(self.settings, 'create_success_info_bar') as success, \
                patch.object(self.settings, 'create_error_info_bar') as error:
            task = factory.return_value
            task.base_url = self.address
            task.isInterruptionRequested.return_value = False
            self.settings.test_qwentts_connection()
            self.settings.dynamic_option_widgets['base_url'].setText('http://192.0.2.20:8080')
            self.settings.on_qwentts_connected(
                {'base_url': self.address, 'model': 'Old model', 'voices': ['Old voice']})
            self.settings.on_qwentts_connection_error('Old error')
            self.settings.finish_qwentts_connection()
            success.assert_not_called()
            error.assert_not_called()
            self.assertIsNone(self.ctx.tts_engine.get_qwentts_service_info())
            self.assert_descriptions_unchanged()


class StartupPreferencesTests(SyncPlaybackFixture):

    def test_missing_software_is_disabled_and_cannot_change_saved_preference(self):
        combo = self.settings.presentation_preference_combo
        for providers, labels in ((PROG_IDS[:1], ('PowerPoint',)),
                                  (PROG_IDS[1:], ('WPS 演示',)), ((), ())):
            with self.subTest(providers=providers), \
                    patch('ui.pages.settings_page.installed_applications', return_value=providers):
                self.settings.refresh_presentation_preference()
                self.assertEqual(combo.isEnabled(), bool(providers))
                for index, provider in enumerate(PROG_IDS, 1):
                    self.assertEqual(combo.items[index].isEnabled, provider in providers)
                    self.assertEqual('未安装' in combo.itemText(index), provider not in providers)
                    if provider not in providers:
                        combo.setCurrentIndex(index)
                        self.assertEqual(combo.currentData(), 'auto')
                for label in labels:
                    self.assertIn(label, combo.toolTip())
                self.assertEqual(self.settings.presentation_preference_caption.text(), '选择放映时优先使用的软件')
                self.ctx.config.save_later.assert_not_called()

    def test_uninstalled_saved_preference_is_explained_and_survives_refresh(self):
        combo = self.settings.presentation_preference_combo
        combo.setCurrentIndex(2)
        self.ctx.config.reset_mock()
        with patch('ui.pages.settings_page.installed_applications', return_value=PROG_IDS[:1]):
            self.settings.refresh_presentation_preference()
        self.assertEqual(combo.currentData(), 'wps')
        self.assertEqual(self.ctx.app_settings.get('presentation_preference'), 'wps')
        self.assertIn('WPS 演示 未安装', combo.toolTip())
        self.assertIn('PowerPoint', combo.toolTip())
        self.assertEqual(self.settings.presentation_preference_caption.text(), '选择放映时优先使用的软件')
        self.ctx.config.save_later.assert_not_called()
        with patch('ui.pages.settings_page.installed_applications', return_value=PROG_IDS):
            self.settings.refresh_presentation_preference()
        self.assertTrue(combo.items[2].isEnabled)
        self.assertNotIn('未安装', combo.currentText())
        self.ctx.config.save_later.assert_not_called()

    def test_presentation_preference_saves_immediately_and_retains_existing_audio(self):
        combo = self.settings.presentation_preference_combo
        self.assertEqual(combo.currentData(), 'auto')
        old_segments = list(self.page.project.notes_list)
        combo.setCurrentIndex(2)
        self.assertEqual(self.ctx.app_settings.get('presentation_preference'), 'wps')
        self.ctx.config.save_later.assert_called_once()
        self.assertEqual(self.page.project.notes_list, old_segments)
        self.assertFalse(self.settings.is_dirty())
        restored = AppSettings()
        restored.load_from(self.ctx.app_settings.to_dict())
        self.assertEqual(restored.get('presentation_preference'), 'wps')
    def test_script_options_defaults_and_immediate_save_do_not_change_existing_audio(self):
        self.assertFalse(self.settings.script_comments_switch.isChecked())
        self.assertTrue(self.settings.animation_check_switch.isChecked())
        old_segments = list(self.page.project.notes_list)
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        self.settings.script_comments_switch.setChecked(True)
        self.settings.animation_check_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('script_comments_enabled'))
        self.assertFalse(self.ctx.app_settings.get('animation_check_enabled'))
        self.assertEqual(self.ctx.config.save_later.call_count, 2)
        self.assertEqual(self.page.project.notes_list, old_segments)
        self.assertTrue(self.page.project.audio_loaded)
        self.assertFalse(self.settings.is_dirty())
        regenerate.assert_not_called()
        restored = AppSettings()
        restored.load_from(self.ctx.app_settings.to_dict())
        self.assertTrue(restored.get('script_comments_enabled'))
        self.assertFalse(restored.get('animation_check_enabled'))

    def test_resume_defaults_on_and_toggle_saves_without_changing_voice_or_position(self):
        self.assertTrue(AppSettings().get('resume_last_session'))
        self.assertTrue(self.settings.resume_session_switch.isChecked())
        self.ctx.app_settings.set('last_session', {'record_path': 'history.json', 'index': 3})
        engine_before = self.ctx.tts_engine.export_persistent_state()
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        self.settings.resume_session_switch.setChecked(False)
        self.assertFalse(self.ctx.app_settings.get('resume_last_session'))
        self.ctx.config.save_later.assert_called_once()
        self.assertFalse(self.settings.is_dirty())
        self.assertEqual(self.ctx.tts_engine.export_persistent_state(), engine_before)
        self.assertEqual(self.ctx.app_settings.get('last_session')['index'], 3)
        regenerate.assert_not_called()

    def test_saved_disabled_preference_is_restored_and_switch_is_available_before_import(self):
        page, settings, ctx = self.make_ui(resume=False, ready=False)
        self.assertFalse(settings.resume_session_switch.isChecked())
        self.assertTrue(settings.resume_session_switch.isEnabled())
        ctx.config.save_later.assert_not_called()
        settings.resume_session_switch.setChecked(True)
        self.assertTrue(ctx.app_settings.get('resume_last_session'))
        ctx.config.save_later.assert_called_once()
        restored = AppSettings()
        restored.load_from({'resume_last_session': False})
        self.assertFalse(restored.get('resume_last_session'))
        old_preferences = AppSettings()
        old_preferences.load_from({'scroll_enabled': True})
        self.assertTrue(old_preferences.get('resume_last_session'))


class SlideshowPreferenceTests(SyncPlaybackFixture):
    def test_display_choice_follows_sync_and_auto_start_gates(self):
        combo = self.settings.slideshow_display_combo
        self.assertFalse(combo.isEnabled())
        combo.setCurrentIndex(1)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertFalse(self.ctx.app_settings.get('slideshow_windowed'))
        self.settings.sync_playback_switch.setChecked(True)
        self.assertTrue(combo.isEnabled())
        combo.setCurrentIndex(1)
        self.assertTrue(self.ctx.app_settings.get('slideshow_windowed'))
        self.settings.slideshow_preference_switches['slideshow_auto_start'].setChecked(False)
        self.assertFalse(combo.isEnabled())
        self.assertTrue(self.ctx.app_settings.get('slideshow_windowed'))

    def test_defaults_and_independent_preferences_save_without_regenerating_audio(self):
        self.settings.sync_playback_switch.setChecked(True)
        before = self.ctx.tts_engine.export_persistent_state()
        keys = tuple(self.settings.slideshow_preference_switches)
        restored = AppSettings()
        restored.load_from({'scroll_enabled': True})
        for key in keys:
            with self.subTest(key=key):
                self.assertTrue(restored.get(key))
                self.ctx.config.reset_mock()
                self.settings.slideshow_preference_switches[key].setChecked(False)
                self.assertFalse(self.ctx.app_settings.get(key))
                self.ctx.config.save_later.assert_called_once()
                self.assertFalse(self.settings.is_dirty())
                self.assertEqual(self.ctx.tts_engine.export_persistent_state(), before)
        roundtrip = AppSettings()
        roundtrip.load_from(self.ctx.app_settings.to_dict())
        for key in keys:
            self.assertFalse(roundtrip.get(key))

    def test_master_import_and_mode_gate_controls_without_losing_preferences(self):
        controls = self.settings.slideshow_preference_switches
        self.assertTrue(all(not switch.isEnabled() for switch in controls.values()))
        self.settings.sync_playback_switch.setChecked(True)
        self.assertTrue(all(switch.isEnabled() for switch in controls.values()))
        controls['slideshow_auto_start'].setChecked(False)
        self.settings.page_turn_combo.setCurrentText('前台按键')
        self.assertTrue(self.settings.slideshow_preferences_card.isHidden())
        self.assertTrue(all(not switch.isEnabled() for switch in controls.values()))
        self.settings.page_turn_combo.setCurrentText('演示软件接口（COM）')
        self.assertFalse(self.settings.slideshow_preferences_card.isHidden())
        self.assertFalse(controls['slideshow_auto_start'].isChecked())
        self.page.project.audio_loaded = False
        self.page.check_import()
        self.assertTrue(all(not switch.isEnabled() for switch in controls.values()))
        self.ctx.config.reset_mock()
        controls['slideshow_auto_start'].setChecked(True)
        self.assertFalse(self.ctx.app_settings.get('slideshow_auto_start'))
        self.ctx.config.save_later.assert_not_called()

    def test_follow_off_ignores_manual_slide_events_but_page_jump_still_works(self):
        self.settings.sync_playback_switch.setChecked(True)
        self.settings.slideshow_preference_switches['slideshow_follow_page'].setChecked(False)
        self.page.on_slideshow_slide_changed(2)
        self.assertEqual(self.page.project.current_index, 0)
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 2
        self.page.playing_script_preview.page_requested.emit(2)
        self.assertEqual(self.page.project.current_index, 1)
        self.settings.slideshow_preference_switches['slideshow_follow_page'].setChecked(True)
        self.page.on_slideshow_slide_changed(1)
        self.assertEqual(self.page.project.current_index, 0)


class SyncPlaybackTests(SyncPlaybackFixture):
    def test_settings_toggle_updates_playback_and_saves_once_without_voice_regeneration(self):
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        engine_before = self.ctx.tts_engine.export_persistent_state()
        self.settings.sync_playback_switch.setChecked(True)
        self.assertTrue(self.page.sync_playback_enabled())
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_called_once()
        self.assertFalse(self.settings.is_dirty())
        self.assertEqual(self.ctx.tts_engine.export_persistent_state(), engine_before)
        regenerate.assert_not_called()
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertFalse(self.page.sync_playback_enabled())
        self.assertFalse(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_called_once()

    def test_playback_preference_updates_settings_without_a_signal_loop(self):
        settings_signal = Mock()
        self.settings.sync_playback_changed.connect(settings_signal)
        self.page.set_sync_playback_enabled(True)
        self.assertTrue(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()
        settings_signal.assert_not_called()
        self.ctx.config.reset_mock()
        self.page.set_sync_playback_enabled(False)
        self.assertFalse(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()

    def test_restricted_mode_disables_setting_and_preserves_preference(self):
        self.page.set_sync_playback_enabled(True)
        self.page.project.presentation_path = None
        self.page.refresh_presentation_state()
        self.assertFalse(self.page.can_sync_playback())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.assertTrue(self.page.sync_playback_enabled())
        self.assertTrue(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_not_called()
        self.page.set_presentation_link(presentation_link.describe(self.source))
        self.assertTrue(self.page.can_sync_playback())
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())

    def test_busy_generation_temporarily_disables_settings_control(self):
        with patch.object(self.page, 'is_busy', return_value=True):
            self.settings.refresh_sync_playback_state()
            self.assertFalse(self.settings.sync_playback_switch.isEnabled())
            self.settings.sync_playback_switch.setChecked(True)
            self.assertFalse(self.ctx.app_settings.get('scroll_enabled'))
            self.ctx.config.save_later.assert_not_called()
        self.settings.refresh_sync_playback_state()
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())

    def test_switch_controls_event_following_and_com_page_jump(self):
        self.settings.sync_playback_switch.setChecked(True)
        self.ctx.slideshow_watcher.start.assert_called_once_with(self.source)
        self.page.on_slideshow_slide_changed(2)
        self.assertEqual(self.page.project.current_index, 1)
        self.settings.sync_playback_switch.setChecked(False)
        self.ctx.slideshow_watcher.stop.assert_called()
        self.page.on_slideshow_slide_changed(1)
        self.assertEqual(self.page.project.current_index, 1)
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 2
        self.page.playing_script_preview.page_requested.emit(1)
        self.assertEqual(self.page.project.current_index, 0)
        self.settings.sync_playback_switch.setChecked(True)
        self.page.playing_script_preview.page_requested.emit(2)
        self.assertEqual(self.page.project.current_index, 1)

    def test_saved_preference_is_restored_but_cannot_change_before_import(self):
        page, settings, ctx = self.make_ui(enabled=True, ready=False)
        self.assertTrue(page.sync_playback_enabled())
        self.assertTrue(settings.sync_playback_switch.isChecked())
        self.assertFalse(settings.is_dirty())
        self.assertFalse(page.can_sync_playback())
        self.assertFalse(settings.sync_playback_switch.isEnabled())
        self.assertIn('导入讲稿', settings.sync_playback_switch.toolTip())
        settings.sync_playback_switch.setChecked(False)
        self.assertTrue(ctx.app_settings.get('scroll_enabled'))
        self.assertTrue(page.sync_playback_enabled())
        self.assertTrue(settings.sync_playback_switch.isChecked())
        ctx.config.save_later.assert_not_called()

    def test_import_readiness_updates_both_switches_without_changing_ppt_path(self):
        self.page.project.audio_loaded = False
        self.page.check_import()
        self.assertFalse(self.page.can_sync_playback())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.assertIn('音频尚未就绪', self.settings.sync_playback_switch.toolTip())
        self.page.project.audio_loaded = True
        self.page.check_import()
        self.assertTrue(self.page.can_sync_playback())
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())
        self.assertEqual(self.settings.sync_playback_switch.toolTip(), '')

    def test_failed_or_incomplete_generation_disables_control_and_event_following(self):
        self.settings.sync_playback_switch.setChecked(True)
        self.page.clean_and_reset()
        self.page.refresh_slideshow_watch()
        self.assertFalse(self.page.can_sync_playback())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.ctx.slideshow_watcher.stop.assert_called()
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_not_called()


if __name__ == '__main__':
    unittest.main()
