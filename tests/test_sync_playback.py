"""设置和主页的同步翻页偏好、禁用状态及播放行为验证。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent

from app.presentation import link as presentation_link
from ui import theme
from app.audio.playback import PlaybackBus
from app.settings_store import AppSettings
from app.window import Window
from tts_engine import TTSEngine
from test_presentation_link import make_ppt
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.settings_page import SettingInterface

from qt_test_case import QtTestCase


class SyncPlaybackFixture(QtTestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = make_ppt(Path(folder.name) / 'lesson.pptx')
        self.page, self.settings, self.ctx = self.make_ui()

    def make_ui(self, enabled=False, ready=True, resume=True):
        engine = TTSEngine()
        engine.set_mode('edge')
        prefs = AppSettings()
        prefs.set('scroll_enabled', enabled)
        prefs.set('resume_last_session', resume)
        ctx = NS(version='1.6.3', tts_engine=engine, app_settings=prefs, config=Mock(),
                 playback_bus=PlaybackBus(), hotkeys=Mock(), slideshow_watcher=Mock())
        with patch('app.paths.ensure_runtime_dirs'), \
             patch.object(engine, 'get_voices_list', return_value=['zh-CN-XiaoxiaoNeural']), \
             patch('ui.theme.available_fonts', return_value=(('', theme.DEFAULT_FONT_LABEL),)):
            page = PPTReviewer(ctx)
            if ready:
                page.notes = {1: '第一页', 2: '第二页'}
                page.notes_list = [{'page': 1, 'text': '第一页'}, {'page': 2, 'text': '第二页'}]
                page.presentation_path = self.source
                page.presentation_link = presentation_link.describe(self.source)
                page.is_import = True
                page.pageJumpSpinBox.setMaximum(2)
                page.check_import()
            settings = SettingInterface(ctx)
        Window._bind_sync_playback(NS(ppt_r=page, setting_interface=settings))
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

class StartupPreferencesTests(SyncPlaybackFixture):
    def test_script_options_defaults_and_immediate_save_do_not_change_existing_audio(self):
        self.assertFalse(self.settings.script_comments_switch.isChecked())
        self.assertTrue(self.settings.animation_check_switch.isChecked())
        old_segments = list(self.page.notes_list)
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        self.settings.script_comments_switch.setChecked(True)
        self.settings.animation_check_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('script_comments_enabled'))
        self.assertFalse(self.ctx.app_settings.get('animation_check_enabled'))
        self.assertEqual(self.ctx.config.save_later.call_count, 2)
        self.assertEqual(self.page.notes_list, old_segments)
        self.assertTrue(self.page.is_import)
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
        self.page.is_import = False
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
        self.assertEqual(self.page.current_index, 0)
        self.page.pageJumpSpinBox.setValue(2)
        with patch('app.presentation.slideshow.goto') as jump:
            self.page.jump_page()
            jump.assert_called_once_with(2, self.source)
        self.settings.slideshow_preference_switches['slideshow_follow_page'].setChecked(True)
        self.page.on_slideshow_slide_changed(1)
        self.assertEqual(self.page.current_index, 0)


class SyncPlaybackTests(SyncPlaybackFixture):
    def test_settings_toggle_updates_home_and_saves_once_without_voice_regeneration(self):
        regenerate = Mock()
        self.settings.regenerate_requested.connect(regenerate)
        engine_before = self.ctx.tts_engine.export_persistent_state()
        self.settings.sync_playback_switch.setChecked(True)
        self.assertTrue(self.page.scrollEnableSwitch.isChecked())
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_called_once()
        self.assertFalse(self.settings.is_dirty())
        self.assertEqual(self.ctx.tts_engine.export_persistent_state(), engine_before)
        regenerate.assert_not_called()
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertFalse(self.page.scrollEnableSwitch.isChecked())
        self.assertFalse(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_called_once()

    def test_home_toggle_updates_settings_without_a_signal_loop(self):
        settings_signal = Mock()
        self.settings.sync_playback_changed.connect(settings_signal)
        self.page.scrollEnableSwitch.setChecked(True)
        self.assertTrue(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()
        settings_signal.assert_not_called()
        self.ctx.config.reset_mock()
        self.page.scrollEnableSwitch.setChecked(False)
        self.assertFalse(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()

    def test_restricted_mode_disables_both_controls_and_preserves_preference(self):
        self.page.scrollEnableSwitch.setChecked(True)
        self.page.presentation_path = None
        self.page.refresh_presentation_state()
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.assertTrue(self.page.scrollEnableSwitch.isChecked())
        self.assertTrue(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_not_called()
        self.page.set_presentation_link(presentation_link.describe(self.source))
        self.assertTrue(self.page.scrollEnableSwitch.isEnabled())
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
        self.assertEqual(self.page.current_index, 1)
        self.settings.sync_playback_switch.setChecked(False)
        self.ctx.slideshow_watcher.stop.assert_called()
        self.page.on_slideshow_slide_changed(1)
        self.assertEqual(self.page.current_index, 1)
        self.page.pageJumpSpinBox.setValue(2)
        with patch('app.presentation.slideshow.goto') as jump:
            self.page.jump_page()
            jump.assert_not_called()
            self.settings.sync_playback_switch.setChecked(True)
            self.page.jump_page()
            jump.assert_called_once_with(2, self.source)

    def test_saved_preference_is_restored_but_cannot_change_before_import(self):
        page, settings, ctx = self.make_ui(enabled=True, ready=False)
        self.assertTrue(page.scrollEnableSwitch.isChecked())
        self.assertTrue(settings.sync_playback_switch.isChecked())
        self.assertFalse(settings.is_dirty())
        self.assertFalse(page.scrollEnableSwitch.isEnabled())
        self.assertFalse(settings.sync_playback_switch.isEnabled())
        self.assertIn('导入讲稿', settings.sync_playback_switch.toolTip())
        settings.sync_playback_switch.setChecked(False)
        self.assertTrue(ctx.app_settings.get('scroll_enabled'))
        self.assertTrue(page.scrollEnableSwitch.isChecked())
        self.assertTrue(settings.sync_playback_switch.isChecked())
        ctx.config.save_later.assert_not_called()

    def test_import_readiness_updates_both_switches_without_changing_ppt_path(self):
        self.page.is_import = False
        self.page.check_import()
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.assertIn('音频尚未就绪', self.settings.sync_playback_switch.toolTip())
        self.page.is_import = True
        self.page.check_import()
        self.assertTrue(self.page.scrollEnableSwitch.isEnabled())
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())
        self.assertEqual(self.settings.sync_playback_switch.toolTip(), '')

    def test_failed_or_incomplete_generation_disables_control_and_event_following(self):
        self.settings.sync_playback_switch.setChecked(True)
        self.page.clean_and_reset()
        self.page.refresh_slideshow_watch()
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.ctx.slideshow_watcher.stop.assert_called()
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_not_called()


if __name__ == '__main__':
    unittest.main()
