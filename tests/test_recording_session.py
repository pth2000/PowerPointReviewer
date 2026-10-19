"""自动放映来源绑定、资源所有权和取消流程验证。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent

from app.presentation.session import PresentationSession as RecordingSession

from qt_test_case import QtTestCase


class RecordingSessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name) / 'lesson.pptx'
        self.source.write_bytes(b'original presentation')
        self.view = Mock(Slide=NS(SlideIndex=2))
        self.view.GetClickIndex.return_value = 1
        self.settings = NS(ShowType=2, RangeType=3, AdvanceMode=2,
                           ShowWithAnimation=0, ShowPresenterView=-1, Run=Mock())
        self.document = Mock(SlideShowSettings=self.settings)
        self.show = NS(View=self.view, _oleobj_=object())
        self.settings.Run.return_value = self.show
        self.adapter = Mock(window=self.show, hwnd=0, name=self.source.name)
        self.application = Mock(AutomationSecurity=1)
        self.application.Presentations.Count = 0
        self.application.Presentations.Open.return_value = self.document
        self.application.SlideShowWindows.Count = 1
        self.application.SlideShowWindows.Item.return_value = self.show
        self.patchers = [
            patch('app.presentation.com._rot_applications', return_value=[]),
            patch('app.presentation.session.ensure_com', return_value=True),
            patch('app.presentation.session.list_presentations', return_value=[]),
            patch('app.presentation.session.application_pid', return_value=567),
            patch('win32process.EnumProcesses', return_value=[1]),
            patch('win32com.client.GetActiveObject', side_effect=RuntimeError('not running')),
            patch('win32com.client.DispatchEx', return_value=self.application),
            patch('win32com.client.dynamic.Dispatch', side_effect=lambda app: app),
            patch('app.presentation.session.PresentationControl', return_value=self.adapter),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_new_show_opens_copy_fullscreen_and_closes_only_owned_resources(self):
        session = RecordingSession.start(self.source)
        copy = Path(self.application.Presentations.Open.call_args.args[0])
        self.assertNotEqual(copy, self.source)
        self.assertNotEqual(copy.name, self.source.name)
        self.assertEqual(copy.read_bytes(), self.source.read_bytes())
        self.application.Presentations.Open.assert_called_once_with(str(copy), -1, 0, 0)
        self.assertEqual(self.settings.ShowType, 1)
        self.assertEqual(self.settings.AdvanceMode, 1)
        self.assertEqual(self.settings.RangeType, 1)
        self.assertEqual(self.settings.ShowPresenterView, 0)
        self.assertEqual(self.application.AutomationSecurity, 1)
        self.assertEqual(self.adapter.source_path, str(self.source))
        self.assertTrue(session.refresh_window())
        session.close()
        session.close()
        self.view.Exit.assert_called_once()
        self.document.Close.assert_called_once()
        self.application.Quit.assert_called_once()
        self.assertFalse(copy.exists())
        self.assertEqual(self.source.read_bytes(), b'original presentation')
        self.document.Save.assert_not_called()

    def application_for(self, selected):
        def active(prog_id):
            if prog_id == selected:
                return self.application
            raise RuntimeError('not running')
        return active

    def test_new_windowed_recording_uses_copy_without_scrollbar(self):
        session = RecordingSession.start(self.source, windowed=True)
        self.assertEqual(self.settings.ShowType, 2)
        self.assertEqual(self.settings.ShowScrollbar, 0)
        session.close()
        self.assertEqual(self.source.read_bytes(), b'original presentation')

    def test_open_document_in_second_wps_instance_is_used_without_starting_another(self):
        idle = Mock()
        idle.Presentations.Count = 0
        self.document.FullName = str(self.source)
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = self.document
        with patch('app.presentation.session.running_applications', return_value=iter([
                ('Kwpp.Application', 'WPS 演示', idle),
                ('Kwpp.Application', 'WPS 演示', self.application)])):
            session = RecordingSession.start(self.source, reuse_open=True)
        self.assertIs(session.application, self.application)
        self.settings.Run.assert_called_once()
        idle.Presentations.Open.assert_not_called()
        self.application.Presentations.Open.assert_not_called()
        session.close(restore_position=False)
        self.document.Close.assert_not_called()

    def test_windowed_playback_restores_open_document_settings_and_dirty_state(self):
        self.settings.ShowType = 1
        self.settings.ShowScrollbar = -1
        self.document.FullName = str(self.source)
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = self.document
        launched = []
        self.settings.Run.side_effect = lambda: launched.append(
            (self.settings.ShowType, self.settings.ShowScrollbar)) or self.show
        for saved in (True, False):
            self.document.Saved = saved
            with patch('win32com.client.GetActiveObject', side_effect=self.application_for('PowerPoint.Application')):
                session = RecordingSession.start(self.source, reuse_open=True, windowed=True)
            self.assertEqual(self.settings.ShowType, 1)
            self.assertEqual(self.settings.ShowScrollbar, -1)
            self.assertEqual(self.document.Saved, saved)
            session.close(restore_position=False)
        self.assertEqual(launched, [(2, 0), (2, 0)])
        self.document.Save.assert_not_called()
        self.document.Close.assert_not_called()

    def test_windowed_launch_failure_restores_settings_and_unsaved_edits(self):
        self.document.Saved = False
        self.settings.ShowType = 1
        self.settings.ShowScrollbar = -1
        self.settings.Run.side_effect = RuntimeError('failed')
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            RecordingSession._run_open_document(self.document, True)
        self.assertEqual(self.settings.ShowType, 1)
        self.assertEqual(self.settings.ShowScrollbar, -1)
        self.assertFalse(self.document.Saved)

    def test_reuses_matching_show_and_restores_position_without_exit(self):
        unrelated = Mock(matches_source=Mock(return_value=False))
        self.adapter.matches_source.return_value = True
        with patch('app.presentation.session.list_presentations', return_value=[unrelated, self.adapter]), \
             patch('win32com.client.DispatchEx') as launch:
            session = RecordingSession.start(self.source)
        launch.assert_not_called()
        self.assertIs(session.presentation, self.adapter)
        session.close()
        self.view.GotoSlide.assert_called_once_with(2, True)
        self.view.GotoClick.assert_called_once_with(1)
        self.view.Exit.assert_not_called()
        self.document.Close.assert_not_called()

    def test_running_wps_is_preferred_and_never_quit(self):
        def active(prog_id):
            if prog_id == 'Kwpp.Application': return self.application
            raise RuntimeError('not running')
        with patch('win32com.client.GetActiveObject', side_effect=active), \
             patch('win32com.client.DispatchEx') as launch, \
             patch('app.presentation.session.PresentationControl', return_value=self.adapter) as adapter:
            session = RecordingSession.start(self.source)
        launch.assert_not_called()
        self.assertEqual(adapter.call_args.args[1:3], ('WPS 演示', 'Kwpp.Application'))
        session.close()
        self.application.Quit.assert_not_called()

    def test_playback_cleanup_keeps_borrowed_show_at_current_page(self):
        self.adapter.matches_source.return_value = True
        with patch('app.presentation.session.list_presentations', return_value=[self.adapter]):
            session = RecordingSession.start(self.source)
        session.close(restore_position=False)
        self.view.GotoSlide.assert_not_called()
        self.view.GotoClick.assert_not_called()
        self.view.Exit.assert_not_called()

    def test_connect_only_reuses_a_show_but_never_opens_a_document_or_starts_office(self):
        with patch('win32com.client.DispatchEx') as launch, \
             patch('win32com.client.GetActiveObject') as running:
            with self.assertRaisesRegex(RuntimeError, '先.*开始放映'):
                RecordingSession.start(self.source, reuse_open=True, allow_start=False)
            self.adapter.matches_source.return_value = True
            with patch('app.presentation.session.list_presentations', return_value=[self.adapter]):
                session = RecordingSession.start(self.source, reuse_open=True, allow_start=False)
            launch.assert_not_called()
            running.assert_not_called()
        self.settings.Run.assert_not_called()
        self.application.Presentations.Open.assert_not_called()
        session.close(restore_position=False)
        self.view.Exit.assert_not_called()

    def test_playback_starts_the_open_unsaved_document_and_only_owns_its_show(self):
        self.document.FullName = str(self.source)
        self.document.Saved = False
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = self.document
        settings_before = {key: value for key, value in vars(self.settings).items() if key != 'Run'}
        with patch('win32com.client.GetActiveObject', side_effect=self.application_for('Kwpp.Application')), \
             patch('win32com.client.DispatchEx') as launch:
            session = RecordingSession.start(self.source, reuse_open=True)
        launch.assert_not_called()
        self.application.Presentations.Open.assert_not_called()
        self.settings.Run.assert_called_once()
        self.assertEqual({key: value for key, value in vars(self.settings).items() if key != 'Run'}, settings_before)
        self.assertFalse(self.document.Saved)
        self.assertIsNone(session.document)
        self.assertIsNone(session.directory)
        self.assertFalse(session.presentation.require_fullscreen)
        session.close(restore_position=False)
        self.view.Exit.assert_called_once()
        self.document.Close.assert_not_called()
        self.document.Save.assert_not_called()
        self.application.Quit.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'original presentation')

    def test_opened_document_launch_failure_does_not_fall_back_to_a_stale_copy(self):
        self.document.FullName = str(self.source)
        self.document.Saved = False
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = self.document
        self.settings.Run.side_effect = RuntimeError('Run failed')
        with patch('win32com.client.GetActiveObject', side_effect=self.application_for('PowerPoint.Application')):
            with self.assertRaisesRegex(RuntimeError, '已打开'):
                RecordingSession.start(self.source, reuse_open=True)
        self.application.Presentations.Open.assert_not_called()
        self.document.Close.assert_not_called()
        self.application.Quit.assert_not_called()
        self.assertFalse(self.document.Saved)

    def test_same_name_in_another_directory_does_not_match_the_open_document(self):
        other = NS(FullName=str(self.source.parent / 'another' / self.source.name), Saved=False)
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = other
        with patch('win32com.client.GetActiveObject', side_effect=self.application_for('PowerPoint.Application')):
            session = RecordingSession.start(self.source, reuse_open=True)
        opened_copy = Path(self.application.Presentations.Open.call_args.args[0])
        self.assertNotEqual(opened_copy, self.source)
        self.assertEqual(opened_copy.read_bytes(), self.source.read_bytes())
        self.assertFalse(other.Saved)
        session.close()

    def test_reused_wps_show_restores_page_without_blocking_gotoclick(self):
        self.adapter.matches_source.return_value = True
        self.adapter.provider = 'WPS 演示'
        self.view.GetClickIndex.return_value = 0
        with patch('app.presentation.session.list_presentations', return_value=[self.adapter]):
            session = RecordingSession.start(self.source)
        session.close()
        self.view.GotoSlide.assert_called_once_with(2, True)
        self.view.GotoClick.assert_not_called()
        self.view.Exit.assert_not_called()

    def test_running_app_with_matching_document_is_preferred(self):
        other = Mock()
        other.Presentations.Count = 0
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = NS(FullName=str(self.source))
        with patch('win32com.client.GetActiveObject', side_effect=lambda prog:
                   self.application if prog == 'Kwpp.Application' else other):
            session = RecordingSession.start(self.source)
        other.Presentations.Open.assert_not_called()
        session.close()
        self.application.Quit.assert_not_called()

    def test_new_process_does_not_quit_when_user_has_opened_another_document(self):
        session = RecordingSession.start(self.source)
        self.application.Presentations.Count = 1
        session.close()
        self.application.Quit.assert_not_called()

    def test_launch_failure_restores_security_closes_copy_and_leaves_original(self):
        self.settings.Run.side_effect = RuntimeError('Run failed')
        with self.assertRaisesRegex(RuntimeError, '无法启动'):
            RecordingSession.start(self.source)
        self.assertEqual(self.application.AutomationSecurity, 1)
        self.assertEqual(self.document.Close.call_count, 2)
        for call in self.application.Presentations.Open.call_args_list:
            self.assertFalse(Path(call.args[0]).exists())
        self.assertEqual(self.source.read_bytes(), b'original presentation')

    def test_missing_source_does_not_launch_office(self):
        with patch('win32com.client.DispatchEx') as launch:
            with self.assertRaisesRegex(RuntimeError, '找不到'):
                RecordingSession.start(self.source.with_name('missing.pptx'))
        launch.assert_not_called()


class AutomatedDialogTests(QtTestCase):

    def setUp(self):
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / 'lesson.pptx'
        self.source.write_bytes(b'ppt')
        self.unrelated = NS(matches_source=lambda path: False)
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[self.unrelated]):
            self.dialog = VideoRecordingDialog([{'page': 1, 'text': '讲稿'}], ['speech.wav'], self.source)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def session(self):
        adapter = NS(name='lesson.pptx', hwnd=123, slide_count=3, capture_mode='auto',
                     target={'title': 'show'}, matches_source=lambda path: True,
                     capturable_window=Mock(), bind_capture_window=Mock())
        return NS(presentation=adapter, refresh_window=Mock(), close=Mock())

    def test_imported_ppt_ready_without_show_and_unrelated_show_excluded(self):
        self.assertEqual(self.dialog.presentations, [])
        self.assertEqual(self.dialog.presentation_combo.currentText(), 'lesson.pptx')
        self.assertFalse(self.dialog.presentation_combo.isEnabled())
        self.assertTrue(self.dialog.presentation_combo.isHidden())
        self.assertFalse(self.dialog.presentation_file_label.isHidden())
        self.assertEqual(self.dialog.presentation_file_label.text(), 'lesson.pptx')
        self.assertEqual(self.dialog.status_badge.text(), '自动放映')
        self.assertTrue(self.dialog.start_button.isEnabled())
        self.assertTrue(self.dialog.window_button.isEnabled())

    def test_audio_prepared_then_show_launched_and_recording_continues(self):
        session = self.session()
        self.dialog._set_busy(True)
        with patch('ui.dialogs.video_recording_dialog.RecordingSession.start', return_value=session) as start, \
             patch.object(self.dialog, '_start_live_recording') as record:
            self.dialog._record(['ready.wav'])
            start.assert_called_once_with(self.source, windowed=True)
            self.assertTrue(self.dialog.show_timer.isActive())
            self.dialog._await_show()
            record.assert_called_once_with(['ready.wav'])
        self.dialog._cleanup()
        session.close.assert_called_once()

    def test_save_cancel_does_not_launch_or_open_ppt(self):
        with patch('ui.dialogs.video_recording_dialog.recording_format'), \
             patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName', return_value=('', '')), \
             patch('ui.dialogs.video_recording_dialog.RecordingSession.start') as start:
            self.dialog.start()
        start.assert_not_called()
        self.assertFalse(self.dialog.busy)

    def test_window_timeout_keeps_show_for_manual_selection_until_dialog_closes(self):
        session = self.session()
        session.presentation.hwnd = 0
        session.refresh_window.side_effect = RuntimeError('not found yet')
        self.dialog._set_busy(True)
        with patch('ui.dialogs.video_recording_dialog.RecordingSession.start', return_value=session):
            self.dialog._record(['ready.wav'])
        self.dialog.show_attempts = 39
        self.dialog._await_show()
        self.assertFalse(self.dialog.busy)
        self.assertFalse(self.dialog.show_timer.isActive())
        self.assertTrue(self.dialog.window_button.isEnabled())
        session.close.assert_not_called()
        self.dialog.reject()
        session.close.assert_called_once()

    def test_cancel_while_waiting_releases_show_and_prevents_recording(self):
        session = self.session()
        self.dialog._set_busy(True)
        with patch('ui.dialogs.video_recording_dialog.RecordingSession.start', return_value=session), \
             patch.object(self.dialog, '_start_live_recording') as record:
            self.dialog._record(['ready.wav'])
            self.dialog.reject()
            record.assert_not_called()
        session.close.assert_called_once()
        self.assertFalse(self.dialog.show_timer.isActive())

    def test_exited_owned_show_is_released_and_refresh_returns_to_automatic(self):
        session = self.session()
        session.presentation.is_active = Mock(return_value=True)
        self.dialog.live_session = session
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[]):
            self.dialog.reload()
            self.assertEqual(self.dialog.status_badge.text(), '放映中')
            session.presentation.is_active.return_value = False
            self.dialog.reload()
            self.dialog.reload()
        self.assertIsNone(self.dialog.live_session)
        self.assertEqual(self.dialog.presentations, [])
        self.assertEqual(self.dialog.status_badge.text(), '自动放映')
        self.assertTrue(self.dialog.start_button.isEnabled())
        session.close.assert_called_once()

    def test_refresh_detects_external_show_exit_and_reopening(self):
        show = self.session().presentation
        with patch('ui.dialogs.video_recording_dialog.list_presentations', side_effect=[[show], [], [show]]):
            self.dialog.reload()
            self.assertEqual(self.dialog.status_badge.text(), '放映中')
            self.dialog.reload()
            self.assertEqual(self.dialog.status_badge.text(), '自动放映')
            self.dialog.reload()
            self.assertEqual(self.dialog.status_badge.text(), '放映中')
        self.assertEqual(self.dialog.presentation_file_label.text(), 'lesson.pptx')
        self.assertIn('show', self.dialog.capture_label.text())

    def test_closed_session_does_not_hide_a_new_matching_show(self):
        session = self.session()
        session.presentation.is_active = Mock(return_value=False)
        replacement = self.session().presentation
        self.dialog.live_session = session
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[replacement]):
            self.dialog.reload()
        self.assertIs(self.dialog._selected_presentation(), replacement)
        self.assertEqual(self.dialog.status_badge.text(), '放映中')
        session.close.assert_called_once()

    def test_automatic_polling_preserves_export_error_and_pauses_for_recording_or_picker(self):
        self.dialog.show()
        self.dialog._failed('模拟导出失败')
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[]):
            self.dialog._poll_source()
        self.assertIn('模拟导出失败', self.dialog.status.text())
        self.assertFalse(self.dialog.status_panel.isHidden())
        with patch.object(self.dialog, 'reload') as reload:
            self.dialog.busy = True
            self.dialog._poll_source()
            self.dialog.busy = False
            self.dialog.window_picker_open = True
            self.dialog._poll_source()
            reload.assert_not_called()
        self.dialog.window_picker_open = False

    def test_closed_show_is_dropped_before_starting_next_recording(self):
        session = self.session()
        session.presentation.is_active = Mock(return_value=False)
        self.dialog.live_session = session
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[]), \
             patch('ui.dialogs.video_recording_dialog.recording_format'), \
             patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName', return_value=('', '')):
            self.dialog.start()
        self.assertIsNone(self.dialog.live_session)
        session.presentation.capturable_window.assert_not_called()


if __name__ == '__main__':
    unittest.main()
