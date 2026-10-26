"""普通播放自动放映、运行期副本匹配和资源清理。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


from app.presentation import slideshow
from app.presentation.playback import SlideshowPlayback
from test_sync_playback import SyncPlaybackFixture

from qt_test_case import QtTestCase


def fake_session(source, provider='PowerPoint'):
    state = NS(page=1, click=0)
    view = Mock(Slide=NS(SlideIndex=1))
    def goto(page, reset=True):
        state.page, state.click = page, 0
        view.Slide.SlideIndex = page
    def click(index):
        state.click = index
    view.GotoSlide.side_effect = goto
    view.GotoClick.side_effect = click
    view.GetClickIndex.side_effect = lambda: state.click
    view.GetClickCount.return_value = 3
    view.State = 1
    view.Next.side_effect = lambda: setattr(state, 'click', state.click + 1)
    document = NS(FullName=str(source.with_name('runtime-copy.pptx')))
    window = NS(View=view, Presentation=document, IsFullScreen=True)
    app = Mock()
    app.Presentations.Count = 1
    app.Presentations.Item.return_value = document
    app.SlideShowWindows.Count = 1
    app.SlideShowWindows.Item.return_value = window
    presentation = NS(path=document.FullName, provider=provider, window=window,
                      slide_count=2, application=app, is_active=Mock(return_value=True),
                      resume_background=Mock())
    return NS(presentation=presentation, close=Mock()), state


class SlideshowPlaybackTests(QtTestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name) / 'lesson.pptx'
        self.source.write_bytes(b'source')
        self.session, self.state = fake_session(self.source)
        self.controller = SlideshowPlayback()
        self.addCleanup(self.controller.deleteLater)
        self.addCleanup(self.controller.release)

    def prepare(self, page=1, offset=0):
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session) as start:
            self.controller.prepare(self.source, page, offset)
            self.controller._prepare_step()
        return start

    def test_start_positions_page_before_emitting_ready_and_uses_runtime_path(self):
        ready = Mock()
        self.controller.ready.connect(ready)
        self.prepare(page=2)
        self.assertEqual(self.state.page, 2)
        ready.assert_called_once()
        self.assertFalse(self.controller.preparing)
        self.assertFalse(self.controller.timer.isActive())
        self.assertEqual(self.controller.target_path, Path(self.session.presentation.path))
        self.assertNotEqual(self.controller.target_path, self.source)

    def test_powerpoint_can_seek_an_existing_click_position(self):
        self.prepare(offset=2)
        self.session.presentation.window.View.GotoClick.assert_called_once_with(2)
        self.assertEqual(self.state.click, 2)

    def test_connecting_at_actual_position_preserves_running_animation(self):
        self.state.click = 1
        self.prepare(offset=1)
        view = self.session.presentation.window.View
        view.GotoSlide.assert_not_called()
        view.GotoClick.assert_not_called()
        self.assertEqual(self.controller.click_base, 0)

    def test_failed_resume_blocks_audio_without_repositioning(self):
        self.session.presentation.window.View.State = 2
        ready, failed = Mock(), Mock()
        self.controller.ready.connect(ready)
        self.controller.failed.connect(failed)
        self.prepare()
        ready.assert_not_called()
        self.assertIn('放映已暂停', failed.call_args.args[0])
        self.session.presentation.resume_background.assert_called_once()
        self.session.presentation.window.View.GotoSlide.assert_not_called()

    def test_paused_fullscreen_can_resume_before_audio_without_resetting_position(self):
        presentation = self.session.presentation
        presentation.window.View.State = 2
        self.state.click = 1
        presentation.resume_background.side_effect = lambda: setattr(presentation.window.View, 'State', 1)
        ready, failed = Mock(), Mock()
        self.controller.ready.connect(ready)
        self.controller.failed.connect(failed)
        self.prepare(offset=1)
        ready.assert_called_once()
        failed.assert_not_called()
        presentation.window.View.GotoSlide.assert_not_called()
        presentation.window.View.GotoClick.assert_not_called()

    def test_paused_show_does_not_consume_a_click_when_advancing(self):
        self.prepare()
        self.session.presentation.resume_background.reset_mock()
        view = self.session.presentation.window.View
        view.State = 2
        with self.assertRaisesRegex(RuntimeError, '已暂停'):
            self.controller.advance_to(1)
        view.Next.assert_not_called()
        self.session.presentation.resume_background.assert_not_called()

    def test_connect_only_and_no_alignment_preserve_existing_wps_page_and_animation(self):
        self.session.presentation.provider = 'WPS 演示'
        self.state.page = self.session.presentation.window.View.Slide.SlideIndex = 2
        self.state.click = 2
        ready, restart = Mock(), Mock()
        self.controller.ready.connect(ready)
        self.controller.restart_required.connect(restart)
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session) as start:
            self.controller.prepare(self.source, 1, 1, allow_start=False, align=False)
            self.controller._prepare_step()
        start.assert_called_once_with(self.source, reuse_open=True, allow_start=False, windowed=False, preference='auto')
        self.session.presentation.window.View.GotoSlide.assert_not_called()
        self.session.presentation.window.View.GotoClick.assert_not_called()
        self.assertEqual((self.state.page, self.state.click), (2, 2))
        ready.assert_called_once()
        restart.assert_not_called()

    def test_click_count_is_read_from_the_requested_page(self):
        self.session.presentation.window.View.GetClickCount.side_effect = (
            lambda: 0 if self.state.page == 1 else 3)
        ready = Mock()
        self.controller.ready.connect(ready)
        self.prepare(page=2, offset=2)
        ready.assert_called_once()
        self.assertEqual((self.state.page, self.state.click), (2, 2))

    def test_wps_requests_page_restart_without_calling_gotoclick(self):
        self.session.presentation.provider = 'WPS 演示'
        restart = Mock()
        self.controller.restart_required.connect(restart)
        self.prepare(offset=1)
        restart.assert_called_once_with(1)
        self.session.presentation.window.View.GotoClick.assert_not_called()
        self.session.presentation.window.View.GotoSlide.assert_not_called()
        self.controller.cancel_preparation()
        self.session.close.assert_called_once_with(restore_position=False)

    def test_stopped_wps_can_resume_its_known_segment_without_resetting_animation(self):
        self.session.presentation.provider = 'WPS 演示'
        self.prepare()
        self.controller.advance_to(1)
        self.controller.set_segment(1, 1)
        view = self.session.presentation.window.View
        view.GotoSlide.reset_mock()
        with patch('app.presentation.playback.PresentationSession.start') as start:
            self.controller.prepare(self.source, 1, 1)
            self.controller._prepare_step()
        start.assert_not_called()
        view.GotoSlide.assert_not_called()
        view.GotoClick.assert_not_called()
        self.assertFalse(self.controller.preparing)

    def test_show_readiness_wait_does_not_relaunch_the_application(self):
        self.session.presentation.is_active.return_value = False
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session) as start:
            self.controller.prepare(self.source, 1, 0)
            self.controller._prepare_step()
            self.controller._prepare_step()
            self.assertTrue(self.controller.preparing)
            self.session.presentation.is_active.return_value = True
            self.controller._prepare_step()
            start.assert_called_once()
        self.assertFalse(self.controller.timer.isActive())

    def test_cancel_before_launch_and_failure_never_emit_ready(self):
        ready, failed = Mock(), Mock()
        self.controller.ready.connect(ready)
        self.controller.failed.connect(failed)
        with patch('app.presentation.playback.PresentationSession.start', side_effect=RuntimeError('failed')) as start:
            self.controller.prepare(self.source, 1, 0)
            self.controller.cancel_preparation()
            self.controller._prepare_step()
            start.assert_not_called()
            self.controller.prepare(self.source, 1, 0)
            self.controller._prepare_step()
        ready.assert_not_called()
        failed.assert_called_once_with('failed')
        self.assertFalse(self.controller.preparing)

    def test_cleanup_keeps_borrowed_position_and_is_idempotent(self):
        self.prepare()
        self.controller.release()
        self.controller.release()
        self.session.close.assert_called_once_with(restore_position=False)
        self.assertIsNone(self.controller.target_path)

    def test_page_changes_use_goto_and_same_page_only_advances_one_animation(self):
        self.prepare()
        view = self.session.presentation.window.View
        view.GotoSlide.reset_mock()
        self.controller.advance_to(1)
        view.Next.assert_called_once()
        self.controller.advance_to(2)
        view.GotoSlide.assert_called_once_with(2, True)
        self.assertEqual(view.Next.call_count, 1)
        self.state.click = 3
        with self.assertRaisesRegex(RuntimeError, '点击动画数量'):
            self.controller.advance_to(2)


class ReviewerAutoShowTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.page.project.notes_list = [{'page': 1, 'text': '第一页'}, {'page': 2, 'text': '第二页'}]
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 2
        self.session, self.state = fake_session(self.source)
        self.addCleanup(self.page.playback_show.release)
        self.page.currentSwitch.setChecked(False)
        self.page.scrollEnableSwitch.setChecked(True)
        self.ctx.slideshow_watcher.reset_mock()

    def launch(self):
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session) as start, \
             patch.object(self.page, 'play_audio') as audio:
            self.page.init_play()
            audio.assert_not_called()
            self.assertEqual(self.page.currentStatusLabel.text(), '准备放映')
            self.page.playback_show._prepare_step()
            audio.assert_called_once()
        return start

    def test_enabled_com_prepares_before_audio_and_watches_actual_application(self):
        self.launch().assert_called_once_with(self.source, reuse_open=True, allow_start=True, windowed=False, preference='auto')
        self.ctx.slideshow_watcher.start.assert_called_with(
            Path(self.session.presentation.path), application=self.session.presentation.application)
        self.assertEqual(self.settings.page_turn_source(), Path(self.session.presentation.path))
        self.assertEqual(self.page.describe_slideshow(), 'PowerPoint｜全屏放映 · 第 1 页')

    def test_existing_show_reports_actual_mode_even_when_windowed_is_selected(self):
        self.settings.slideshow_display_combo.setCurrentIndex(1)
        self.launch()
        self.assertIn('全屏放映', self.page.describe_slideshow())
        self.session.presentation.window.View.State = 2
        self.assertIn('已暂停', self.page.describe_slideshow())

    def test_paused_show_stops_current_audio_once_and_keeps_borrowed_show(self):
        self.launch()
        self.page._playing_sequence = True
        self.session.presentation.window.View.State = 2
        with patch.object(self.page, 'create_warning_info_bar') as notice:
            self.page.on_slideshow_checked()
            self.page.on_slideshow_checked()
        self.assertFalse(self.page._playing_sequence)
        notice.assert_called_once()
        self.session.close.assert_not_called()
        self.assertIs(self.page.playback_show.session, self.session)

    def test_background_window_show_stays_running_without_focus_requirement(self):
        self.session.presentation.window.IsFullScreen = False
        self.launch()
        self.page._playing_sequence = True
        with patch.object(self.page, 'create_warning_info_bar') as notice:
            self.page.on_slideshow_checked()
        self.assertTrue(self.page._playing_sequence)
        notice.assert_not_called()

    def test_selected_windowed_display_is_used_when_starting_playback(self):
        self.settings.slideshow_display_combo.setCurrentIndex(1)
        self.assertTrue(self.ctx.app_settings.get('slideshow_windowed'))
        self.launch().assert_called_once_with(self.source, reuse_open=True, allow_start=True, windowed=True, preference='auto')

    def test_countdown_starts_after_show_and_stopping_keeps_show(self):
        self.page.currentSwitch.setChecked(True)
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session), \
             patch.object(self.page, 'play_wait') as countdown:
            self.page.init_play()
            countdown.assert_not_called()
            self.page.playback_show._prepare_step()
            countdown.assert_called_once()
        self.page.stop_audio()
        self.session.close.assert_not_called()
        self.assertIs(self.page.playback_show.session, self.session)

    def test_sync_off_or_manual_modes_play_without_starting_office(self):
        with patch('app.presentation.playback.PresentationSession.start') as start, \
             patch.object(self.page, 'play_audio') as audio:
            self.page.scrollEnableSwitch.setChecked(False)
            self.page.init_play()
            self.assertEqual(audio.call_count, 1)
            self.page.scrollEnableSwitch.setChecked(True)
            for mode in ('window', 'foreground'):
                self.ctx.app_settings.set('page_turn_mode', mode)
                self.page.init_play()
            self.assertEqual(audio.call_count, 3)
            start.assert_not_called()

    def test_cancel_preparation_stops_timer_and_prevents_audio(self):
        with patch('app.presentation.playback.PresentationSession.start') as start, \
             patch.object(self.page, 'play_audio') as audio:
            self.page.init_play()
            self.page.stop_audio()
            self.page.playback_show._prepare_step()
            start.assert_not_called()
            audio.assert_not_called()
        self.assertTrue(self.page.getFileButton.isEnabled())
        self.assertFalse(self.page.is_busy())

    def test_failed_show_only_notifies_and_waits_for_the_next_explicit_play(self):
        with patch('app.presentation.playback.PresentationSession.start',
                   side_effect=[RuntimeError('请先开始放映'), self.session]) as start, \
             patch.object(self.page, 'create_warning_info_bar') as notice, \
             patch.object(self.page, '_bring_playback_dialog_forward') as focus, \
             patch.object(self.page, 'play_audio') as audio, \
             patch.object(self.page, 'play_wait') as countdown, \
             patch('ui.pages.reviewer_page._press_page_down') as keys:
            self.page.init_play()
            self.page.playback_show._prepare_step()
            notice.assert_called_once_with('无法准备播放', '请先开始放映')
            focus.assert_not_called()
            start.assert_called_once()
            audio.assert_not_called()
            countdown.assert_not_called()
            keys.assert_not_called()
            self.assertFalse(self.page.is_busy())
            self.assertFalse(self.page._playing_sequence)
            self.assertFalse(self.page._audio_only_playback)
            self.assertTrue(self.page.playButton.isEnabled())
            self.assertTrue(self.page.getFileButton.isEnabled())
            self.assertEqual(self.page.currentStatusLabel.text(), '停止')
            self.assertFalse(self.page.playback_show.timer.isActive())
            # Starting a show and clicking play again remains the user's choice.
            self.page.init_play()
            self.page.playback_show._prepare_step()
            audio.assert_called_once()
            self.assertEqual(start.call_count, 2)
            keys.assert_not_called()
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))

    def test_audio_only_page_jump_does_not_control_a_retained_show(self):
        self.launch()
        self.page._audio_only_playback = True
        view = self.session.presentation.window.View
        view.GotoSlide.reset_mock()
        self.page.pageJumpSpinBox.setValue(2)
        self.page.jump_page()
        self.assertEqual(self.page.project.current_index, 1)
        view.GotoSlide.assert_not_called()
        with patch.object(self.page, 'play_audio') as audio, \
             patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.restart_segment()
            audio.assert_called_once()
            prepare.assert_not_called()

    def test_wps_restart_confirmation_rewinds_to_page_first_segment(self):
        self.session.presentation.provider = 'WPS 演示'
        self.page.project.notes_list = [{'page': 1, 'text': 'a'}, {'page': 1, 'text': 'b'}]
        self.page.project.current_index = 1
        with patch('app.presentation.playback.PresentationSession.start', return_value=self.session), \
             patch('ui.pages.reviewer_page.MessageBox') as box, \
             patch.object(self.page, 'play_audio') as audio:
            box.return_value.exec.return_value = True
            self.page.init_play()
            self.page.playback_show._prepare_step()
            self.assertEqual(self.page.project.current_index, 0)
            self.page.playback_show._prepare_step()
            audio.assert_called_once()
        self.session.presentation.window.View.GotoClick.assert_not_called()

    def test_show_exit_stops_audio_and_releases_only_managed_session(self):
        self.launch()
        self.page._playing_sequence = True
        self.session.presentation.is_active.return_value = False
        self.page.on_slideshow_ended()
        self.assertFalse(self.page._playing_sequence)
        self.assertIsNone(self.page.playback_show.session)
        self.session.close.assert_called_once_with(restore_position=False)

    def test_disabling_auto_start_connects_without_launching_and_keeps_countdown_order(self):
        self.ctx.app_settings.set('slideshow_auto_start', False)
        self.launch().assert_called_once_with(self.source, reuse_open=True, allow_start=False, windowed=False, preference='auto')

    def test_navigation_off_keeps_show_position_for_start_jump_reset_and_seek(self):
        self.settings.slideshow_preference_switches['slideshow_sync_navigation'].setChecked(False)
        self.state.page = self.session.presentation.window.View.Slide.SlideIndex = 2
        self.launch()
        view = self.session.presentation.window.View
        self.page.pageJumpSpinBox.setValue(1)
        self.page.jump_page()
        self.page.reset_audio()
        with patch.object(self.page, 'play_audio') as audio, \
             patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.next_segment()
            audio.assert_called_once()
            prepare.assert_not_called()
        view.GotoSlide.assert_not_called()
        view.GotoClick.assert_not_called()
        # Automatic advances remain controlled by the master switch.
        self.page.project.current_index = 0
        self.page.is_play_notes = True
        self.page._playing_sequence = True
        with patch.object(self.page, 'play_audio'):
            self.page.timeout_play_next_audio()
        self.assertEqual(self.page.project.current_index, 1)
        view.Next.assert_called_once()

    def test_exit_without_stop_continues_audio_and_never_controls_the_closed_show(self):
        self.launch()
        self.settings.slideshow_preference_switches['slideshow_stop_on_exit'].setChecked(False)
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        self.session.presentation.is_active.return_value = False
        with patch.object(self.page, 'stop_audio') as stop, \
             patch.object(self.page, 'play_audio') as audio, \
             patch('ui.pages.reviewer_page._press_page_down') as keys:
            self.page.on_slideshow_ended()
            stop.assert_not_called()
            self.assertTrue(self.page._playing_sequence)
            self.assertTrue(self.page._audio_only_playback)
            self.page.timeout_play_next_audio()
            audio.assert_called_once()
            keys.assert_not_called()
        self.assertEqual(self.page.project.current_index, 1)
        self.session.close.assert_called_once_with(restore_position=False)
        self.assertIsNone(self.page.playback_show.session)

    def test_exit_before_watcher_probe_also_continues_when_stop_is_disabled(self):
        self.launch()
        self.ctx.app_settings.set('slideshow_stop_on_exit', False)
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        self.session.presentation.is_active.return_value = False
        with patch.object(self.page, 'play_audio') as audio, \
             patch('ui.pages.reviewer_page._press_page_down') as keys:
            self.page.timeout_play_next_audio()
            audio.assert_called_once()
            keys.assert_not_called()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertTrue(self.page._audio_only_playback)
        self.assertIsNone(self.page.playback_show.session)

    def test_end_of_script_keeps_last_slide_and_stop_cancels_pending_next(self):
        self.launch()
        view = self.session.presentation.window.View
        self.page.project.current_index = 1
        self.state.page = view.Slide.SlideIndex = 2
        self.page.is_play_notes = True
        self.page._playing_sequence = True
        view.GotoSlide.reset_mock()
        self.page.timeout_play_next_audio()
        view.Next.assert_not_called()
        view.GotoSlide.assert_not_called()
        self.session.close.assert_not_called()
        self.assertEqual(self.state.page, 2)
        self.page.next_timer.start(100)
        self.page.stop_audio()
        self.assertFalse(self.page.next_timer.isActive())

    def test_switching_script_releases_runtime_show_without_changing_original_link(self):
        self.launch()
        self.page.clean_and_reset()
        self.session.close.assert_called_once_with(restore_position=False)
        self.assertEqual(self.page.project.presentation_path, self.source)


class PreferredWatcherTests(QtTestCase):

    def test_direct_application_is_used_and_existing_probe_detects_wps_exit(self):
        session, _ = fake_session(Path('lesson.pptx'))
        watcher = slideshow.SlideshowWatcher()
        self.addCleanup(watcher.stop)
        with patch('win32com.client.WithEvents', return_value=Mock()) as events, \
             patch('app.presentation.slideshow._running_apps') as lookup:
            watcher.start(session.presentation.path, application=session.presentation.application)
            events.assert_called_once()
            lookup.assert_not_called()
        self.assertTrue(watcher._had_show)
        ended = Mock()
        watcher.show_ended.connect(ended)
        watcher.show_ended.connect(watcher.stop)
        session.presentation.application.SlideShowWindows.Count = 0
        watcher._pump_ticks = watcher.PROBE_EVERY - 1
        with patch('pythoncom.PumpWaitingMessages'):
            watcher._pump()
        ended.assert_called_once()
        self.assertFalse(watcher._pump_timer.isActive())
        self.assertFalse(watcher._retry_timer.isActive())

    def test_existing_show_probe_checks_playback_without_an_extra_timer(self):
        session, _ = fake_session(Path('lesson.pptx'))
        watcher = slideshow.SlideshowWatcher()
        self.addCleanup(watcher.stop)
        watcher._app = session.presentation.application
        watcher.source_path = session.presentation.path
        checked = Mock()
        watcher.show_checked.connect(checked)
        with patch('pythoncom.PumpWaitingMessages'):
            for _ in range(watcher.PROBE_EVERY):
                watcher._pump()
        checked.assert_called_once()

    def test_custom_show_events_use_document_page_instead_of_show_order(self):
        watcher = slideshow.SlideshowWatcher()
        watcher.source_path = Path('lesson.pptx')
        changed = Mock()
        watcher.slide_changed.connect(changed)
        handler = slideshow._make_handler(watcher)()
        window = NS(Presentation=NS(FullName='lesson.pptx'),
                    View=NS(Slide=NS(SlideIndex=5), CurrentShowPosition=1))
        handler.OnSlideShowNextSlide(window)
        changed.assert_called_once_with(5)
        self.assertEqual(slideshow._slide_index(NS(CurrentShowPosition=2)), 2)
        watcher.stop()


if __name__ == '__main__':
    unittest.main()
