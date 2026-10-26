"""验证关联操作保留已有配音，且作用于播放和视频的同一文稿。"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

import zipfile
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent

from app.presentation import link as presentation_link, slideshow
from app.audio.playback import PlaybackBus
from app.settings_store import AppSettings
from test_presentation_link import make_ppt

from qt_test_case import QtTestCase


class LinkInterfaceTests(QtTestCase):

    def setUp(self):
        from ui.pages.reviewer_page import PPTReviewer
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = make_ppt(self.root / '原文件.pptx')
        self.copy = self.root / '新的位置.pptx'
        self.copy.write_bytes(self.source.read_bytes())
        audio_dir = self.root / 'audio'
        audio_dir.mkdir()
        self.audio = audio_dir / 'original-key.wav'
        self.audio.write_bytes(b'keep existing audio')
        self.record = {
            'source_file': str(self.source), 'source_name': '原文件', 'mark': '●',
            'notes': {'1': '已经修改的讲稿'},
            'items': [{'page': 1, 'text': '已经修改的讲稿', 'duration': 1,
                       'cache_key': 'original-key', 'cache_ext': 'wav'}],
            'generation_profile': {'voice': 'original voice'},
        }
        self.record_path = self.root / 'history.json'
        self.record_path.write_text(json.dumps(self.record), encoding='utf-8')
        self.context = NS(app_settings=AppSettings(), config=Mock(), playback_bus=PlaybackBus(),
                          slideshow_watcher=Mock(), hotkeys=Mock(), tts_engine=Mock())
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(self.context)
        self.page.audio_cache_path = audio_dir
        self.page.session_root_path = self.root
        self.addCleanup(self.dispose)
        from ui.pages.tools_page import ToolsInterface
        self.tools = ToolsInterface(self.context, self.page)
        self.addCleanup(self.tools.deleteLater)
        with patch('app.presentation.slideshow.list_open_presentations', return_value=[]):
            self.page.load_session_record(self.record_path)

    def dispose(self):
        self.page.presentation_monitor.shutdown()
        self.page.stop_audio()
        self.page.ctx.slideshow_watcher.stop()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def test_loading_another_record_keeps_the_project_shared_with_tools(self):
        shared = self.tools.project
        record = json.loads(json.dumps(self.record))
        record['notes'] = {'1': '另一份讲稿'}
        record['items'][0]['text'] = '另一份讲稿'
        target = self.root / 'another.json'
        target.write_text(json.dumps(record), encoding='utf-8')
        self.page.load_session_record(target)
        self.assertIs(self.page.project, shared)
        self.assertIs(self.tools.project, shared)
        self.assertEqual(self.tools.project.notes, {1: '另一份讲稿'})
        self.assertEqual(self.tools.project.session_record_path, target)

    def test_busy_state_refreshes_tool_buttons_even_without_ppt_association(self):
        from qfluentwidgets import PushButton
        self.restrict()
        word = next(button for button in self.tools.findChildren(PushButton) if button.text() == 'Word')
        self.assertTrue(word.isEnabled())
        with patch.object(self.page, 'is_busy', return_value=True):
            self.page.refresh_presentation_state()
            self.assertFalse(word.isEnabled())
        self.page.refresh_presentation_state()
        self.assertTrue(word.isEnabled())

    def test_relink_keeps_script_audio_source_and_historical_voice_settings(self):
        notes, media = dict(self.page.project.notes), list(self.page.project.media_list)
        with patch.object(self.page.save_thread, 'start') as generate:
            self.page.set_presentation_link(presentation_link.validate(self.copy, [1]))
        generate.assert_not_called()
        self.assertEqual(self.page.project.notes, notes)
        self.assertEqual(self.page.project.media_list, media)
        self.assertEqual(self.page.project.note_file_path, self.source)
        self.assertEqual(self.page.project.presentation_path, self.copy)
        self.assertEqual(self.page.notes_path_text.text, str(self.copy))
        record = json.loads(self.record_path.read_text(encoding='utf-8'))
        self.assertEqual(record['generation_profile'], self.record['generation_profile'])
        self.assertEqual(record['items'], self.record['items'])
        self.assertEqual(record['notes'], self.record['notes'])
        with patch('app.presentation.slideshow.advance', return_value=True) as advance:
            self.page.send_page_down()
        advance.assert_called_once_with(self.copy)

    def test_deleted_original_allows_history_audio_and_relink(self):
        self.source.unlink()
        self.record_path.write_text(json.dumps(self.record), encoding='utf-8')
        with patch('app.presentation.slideshow.list_open_presentations', return_value=[]):
            self.page.load_session_record(self.record_path)
        self.assertTrue(self.page.project.audio_loaded)
        self.assertIsNone(self.page.project.presentation_path)
        self.assertTrue(self.page.is_restricted())
        self.assertTrue(self.page.playButton.isEnabled())
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        self.assertEqual(self.page.project.media_list, [self.audio])
        self.assertEqual(self.page.notes_path_text.text, f'{self.source}（文件不可用）')
        self.page.set_presentation_link(presentation_link.validate(self.copy, [1]))
        self.assertEqual(self.page.project.presentation_path, self.copy)
        self.assertEqual(self.page.notesPathLabel.toolTip(), str(self.copy))

    def test_history_finds_renamed_copy_by_content_and_saves_new_location(self):
        record = dict(self.record, presentation=presentation_link.describe(self.source))
        self.record_path.write_text(json.dumps(record), encoding='utf-8')
        self.source.unlink()
        with patch('app.presentation.slideshow.list_open_presentations', return_value=[]):
            self.page.load_session_record(self.record_path)
        self.assertEqual(self.page.project.presentation_path, self.copy)
        self.assertEqual(self.page.notes_path_text.text, str(self.copy))
        self.assertEqual(json.loads(self.record_path.read_text(encoding='utf-8'))['presentation']['path'], str(self.copy))

    def test_loaded_history_displays_source_path_instead_of_record_filename(self):
        self.assertEqual(self.page.notes_path_text.text, str(self.source))
        self.assertEqual(self.page.notesPathLabel.toolTip(), str(self.source))

    def test_standalone_history_shows_original_script_path(self):
        source = self.root / '讲稿.docx'
        source.write_bytes(b'original script')
        self.record_path.write_text(json.dumps(dict(self.record, source_file=str(source), presentation={})), encoding='utf-8')
        self.page.load_session_record(self.record_path)
        self.assertEqual(self.page.notes_path_text.text, str(source))

    def test_history_without_source_shows_name_and_association_status(self):
        self.record_path.write_text(json.dumps(dict(self.record, source_file='', presentation={})), encoding='utf-8')
        self.page.load_session_record(self.record_path)
        self.assertEqual(self.page.notes_path_text.text, '原文件（未关联原文件）')
        self.assertNotIn('history.json', self.page.notesPathLabel.toolTip())

    def test_missing_associated_ppt_path_takes_priority_over_original_script_path(self):
        source = self.root / '讲稿.docx'
        source.write_bytes(b'original script')
        linked = self.root / '已移动的文稿.pptx'
        self.page.project.note_file_path = source
        self.page.project.presentation_path = None
        self.page.project.presentation_link = {'path': str(linked)}
        self.page.refresh_presentation_state()
        self.assertEqual(self.page.notes_path_text.text, f'{linked}（文件不可用）')

    def test_persist_failure_preserves_previous_association(self):
        previous = self.page.project.presentation_path
        with patch('app.presentation.link.update_record', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                self.page.set_presentation_link(presentation_link.describe(self.copy))
        self.assertEqual(self.page.project.presentation_path, previous)

    def test_choose_open_document_displays_and_uses_its_saved_path(self):
        from ui.dialogs.presentation_link_dialog import PresentationLinkDialog
        opened = [{'path': str(self.copy), 'name': self.copy.name, 'provider': 'WPS 演示'}]
        with patch('app.presentation.slideshow.list_open_presentations', return_value=opened):
            dialog = PresentationLinkDialog(self.page.project.presentation_link, [1])
        try:
            self.assertIn(self.copy.name, dialog.open_combo.currentText())
            dialog.choose_open()
            self.assertEqual(Path(dialog.selected['path']), self.copy)
        finally:
            dialog.deleteLater()

    def test_project_export_only_packs_audio_and_preserves_ppt_identity(self):
        self.context.version = '1.6.3'
        self.context.tts_engine.get_generation_profile.return_value = {'voice': 'original voice'}
        self.context.tts_engine.get_selected_voice_name.return_value = 'original voice'
        destination = self.root / 'export'
        with patch('ui.pages.tools_page.MessageBox') as dialog, \
             patch.object(self.tools, 'choose_export_dir', return_value=destination):
            dialog.return_value.exec.return_value = True
            self.tools.export_project_package()
            self.assertNotIn('包含 PPT', dialog.call_args.args[1])
        package = next(destination.glob('*.pprpkg'))
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json', 'audio/original-key.wav'})
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['presentation']['sha256'], presentation_link.fingerprint(self.source))
        self.assertNotIn('entry', manifest['presentation'])
        self.assertNotIn('path', manifest['presentation'])

    def test_watcher_reconnects_to_app_that_has_the_linked_document(self):
        watcher = slideshow.SlideshowWatcher()
        wrong = NS(Presentations=Mock(Count=1))
        wrong.Presentations.Item.return_value = NS(FullName='other.pptx')
        selected = NS(Presentations=Mock(Count=1))
        selected.Presentations.Item.return_value = NS(FullName=str(self.copy))
        watcher.source_path = self.copy
        watcher._app = wrong
        try:
            self.assertFalse(watcher._alive())
            with patch('app.presentation.slideshow._running_apps', return_value=iter([('PP', wrong), ('WPS', selected)])), \
                 patch('win32com.client.WithEvents', return_value=Mock()) as events:
                self.assertTrue(watcher._try_attach())
            self.assertIs(events.call_args.args[0], selected)
            self.assertTrue(watcher._alive())
        finally:
            watcher.stop()
            watcher.deleteLater()

    def test_home_retains_original_card_size_and_layout(self):
        from qfluentwidgets import FluentIcon, FluentWindow
        from ui import theme
        theme.apply_theme('light', theme.DEFAULT_COLOR)
        theme.apply_font('')
        window = FluentWindow()
        window.setMinimumSize(760, 560)
        window.addSubInterface(self.page, FluentIcon.HOME, '主页')
        window.show()
        try:
            for linked in (False, True):
                self.page.project.presentation_path = self.source if linked else None
                self.page.project.presentation_link = presentation_link.describe(self.source) if linked else {}
                self.page.refresh_presentation_state()
                for width, height in ((850, 750), (760, 560)):
                    with self.subTest(linked=linked, width=width, height=height):
                        window.resize(width, height)
                        for _ in range(5):
                            self.qt.processEvents()
                        self.assertEqual(self.page.importCardWidget.height(), 80)
                        self.assertFalse(hasattr(self.page, 'presentation_link_panel'))
                        self.assertEqual(self.page.getFileButton.visibleRegion().boundingRect(),
                                         self.page.getFileButton.rect())
        finally:
            # 测试页面由 fixture 统一销毁，先从导航栈移出以免窗口关闭时重复销毁。
            window.stackedWidget.blockSignals(True)
            self.page.setParent(None)
            window.close()
            window.deleteLater()

    def history_dialog(self):
        from ui.dialogs.session_history_dialog import SessionHistoryDialog
        dialog = SessionHistoryDialog(self.root, self.page)
        self.wait_for_history(dialog)
        dialog.presentation_link_changed.connect(self.page._history_presentation_changed)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def restrict(self):
        self.page.project.presentation_path = None
        self.page.project.presentation_link = {}
        self.page.refresh_presentation_state()

    def test_video_entries_open_their_own_export_method(self):
        from qfluentwidgets import PushButton
        buttons = {button.property('video_export_mode'): button for button in self.tools.findChildren(PushButton)
                   if button.property('video_export_mode')}
        self.assertEqual(set(buttons), {'native', 'obs'})
        for mode, button in buttons.items():
            self.assertTrue(button.isVisibleTo(self.tools.video_export_card), mode)
        self.assertFalse(hasattr(self.tools, 'video_other_button'))
        with patch.object(self.tools, 'export_video') as export:
            for mode, button in buttons.items():
                button.click()
                export.assert_called_with(mode)

    def test_restricted_mode_keeps_audio_and_documents_but_disables_ppt_features(self):
        from qfluentwidgets import PushButton
        self.restrict()
        self.assertIn('受限模式', self.page.statusLabel.text())
        self.assertIn('历史记录', self.page.IconInfoBadge.toolTip())
        self.assertTrue(self.page.playButton.isEnabled())
        self.assertTrue(self.page.pageJumpSpinBox.isEnabled())
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        buttons = {button.text(): button for card in self.tools.export_cards + self.tools.processing_cards
                   for button in card.findChildren(PushButton)}
        ppt_buttons = [button for card in self.tools.export_cards for button in card.findChildren(PushButton)
                       if button.property('requires_ppt')]
        self.assertEqual(len(ppt_buttons), 3)
        for button in ppt_buttons:
            self.assertFalse(button.isEnabled(), button.text())
        for name in ('Word', 'Markdown', 'JSON', 'SRT 字幕', '逐条音频', '合并音频', '导出工程包', '前往优化'):
            self.assertTrue(buttons[name].isEnabled(), name)
        with patch('app.presentation.slideshow.advance') as advance, \
             patch('app.presentation.window_target.send_scroll') as window_turn, \
             patch('ui.pages.reviewer_page._press_page_down') as keyboard:
            for mode in ('com', 'window', 'foreground'):
                self.context.app_settings.set('page_turn_mode', mode)
                self.page.send_page_down()
            advance.assert_not_called()
            window_turn.assert_not_called()
            keyboard.assert_not_called()
        with patch('app.presentation.slideshow.goto') as jump:
            self.page.jump_page()
            jump.assert_not_called()
        with patch('ui.dialogs.video_recording_dialog.VideoRecordingDialog') as video, \
             patch.object(self.tools, 'get_ppt_path') as write_target, \
             patch.object(self.tools, 'create_warning_info_bar'):
            self.tools.record_obs_video()
            self.tools.export_powerpoint_video()
            self.tools.write_to_ppt()
            video.assert_not_called()
            write_target.assert_not_called()

    def test_native_export_entry_uses_linked_ppt_and_current_audio(self):
        with patch('ui.dialogs.video_recording_dialog.VideoRecordingDialog') as video, \
                patch.object(self.page, 'stop_audio') as stop, \
                patch.object(self.page, 'release_playback_show') as release, \
                patch.object(self.page, 'refresh_hotkeys') as hotkeys, \
                patch.object(self.page, 'refresh_slideshow_watch') as watch:
            self.tools.export_powerpoint_video()
            video.assert_called_once_with(self.page.project.notes_list, self.page.project.media_list,
                                          self.page.project.presentation_path, self.tools,
                                          ctx=self.context, initial_mode='native')
            video.return_value.exec.assert_called_once()
            stop.assert_called_once()
            release.assert_called_once()
            hotkeys.assert_called_once()
            watch.assert_called_once()

    def test_history_association_restores_active_session_without_regeneration(self):
        self.restrict()
        dialog = self.history_dialog()
        notes, media = dict(self.page.project.notes), list(self.page.project.media_list)
        self.page.project.current_index = 1
        selected = presentation_link.validate(self.copy, [1])
        picker = Mock(selected=selected)
        picker.exec.return_value = True
        with patch('ui.dialogs.session_history_dialog.PresentationLinkDialog', return_value=picker), \
             patch.object(self.page.save_thread, 'start') as generate:
            dialog.associate_selected()
        self.wait_for_history(dialog)
        generate.assert_not_called()
        self.assertFalse(self.page.is_restricted())
        self.assertEqual(self.page.project.presentation_path, self.copy)
        self.assertEqual(self.page.project.current_index, 1)
        self.assertEqual(self.page.project.notes, notes)
        self.assertEqual(self.page.project.media_list, media)
        self.assertTrue(self.page.scrollEnableSwitch.isEnabled())
        from qfluentwidgets import PushButton
        ppt_buttons = [button for button in self.tools.findChildren(PushButton) if button.property('requires_ppt')]
        self.assertEqual(len(ppt_buttons), 3)
        for button in ppt_buttons:
            self.assertTrue(button.isEnabled(), button.text())
        self.assertEqual(dialog.table.item(0, 6).text(), '已关联')
        self.assertEqual(dialog.link_ppt_button.text(), '更换 PPT')
        record = json.loads(self.record_path.read_text(encoding='utf-8'))
        self.assertEqual(record['generation_profile'], self.record['generation_profile'])
        self.assertEqual(record['items'], self.record['items'])

    def test_history_unlinked_record_remains_loadable_and_multi_selection_cannot_associate(self):
        unlinked = dict(self.record, source_file='', presentation={})
        self.record_path.write_text(json.dumps(unlinked), encoding='utf-8')
        dialog = self.history_dialog()
        self.assertEqual(dialog.table.item(0, 6).text(), '未关联')
        self.assertTrue(dialog.open_button.isEnabled())
        dialog.accept_selected()
        self.page.load_session_record(dialog.get_selected_record_path())
        self.assertTrue(self.page.project.audio_loaded)
        self.assertTrue(self.page.is_restricted())
        second = self.root / 'other.json'
        second.write_text(json.dumps(unlinked), encoding='utf-8')
        dialog = self.history_dialog()
        dialog.table.selectAll()
        self.assertFalse(dialog.link_ppt_button.isEnabled())
        self.assertFalse(dialog.open_button.isEnabled())
        with patch('ui.dialogs.session_history_dialog.PresentationLinkDialog') as picker:
            dialog.associate_selected()
            picker.assert_not_called()

    def test_cancelled_or_failed_history_association_preserves_record(self):
        dialog = self.history_dialog()
        before = self.record_path.read_bytes()
        selected = presentation_link.validate(self.copy, [1])
        picker = Mock(selected=selected)
        picker.exec.return_value = False
        with patch('ui.dialogs.session_history_dialog.PresentationLinkDialog', return_value=picker):
            dialog.associate_selected()
        self.assertEqual(self.record_path.read_bytes(), before)
        picker.exec.return_value = True
        with patch('ui.dialogs.session_history_dialog.PresentationLinkDialog', return_value=picker), \
             patch('app.presentation.link.update_record', side_effect=OSError('read only')), \
             patch.object(dialog, '_show_error') as error:
            dialog.associate_selected()
        error.assert_called_once()
        self.assertEqual(self.record_path.read_bytes(), before)
        self.assertEqual(self.page.project.presentation_path, self.source)

    def test_associating_another_history_record_does_not_change_current_session(self):
        another = self.root / 'another.json'
        another.write_text(json.dumps(self.record), encoding='utf-8')
        before_notes, before_media = dict(self.page.project.notes), list(self.page.project.media_list)
        self.page._history_presentation_changed(another, presentation_link.describe(self.copy))
        self.assertEqual(self.page.project.presentation_path, self.source)
        self.assertEqual(self.page.project.notes, before_notes)
        self.assertEqual(self.page.project.media_list, before_media)

    def test_deleted_ppt_switches_to_restricted_mode_without_stopping_audio(self):
        self.source.unlink()
        with patch.object(self.page, 'stop_audio') as stop:
            self.page._poll_presentation()
        stop.assert_not_called()
        self.assertTrue(self.page.is_restricted())
        self.assertIn('受限模式', self.page.statusLabel.text())
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())

    def test_different_content_requires_confirmation_and_bad_page_count_is_rejected(self):
        from ui.dialogs.presentation_link_dialog import PresentationLinkDialog
        wrong = make_ppt(self.root / 'other.pptx', 1)
        with patch('app.presentation.slideshow.list_open_presentations', return_value=[]):
            dialog = PresentationLinkDialog(presentation_link.describe(self.source), [1, 2])
            try:
                dialog.select_path(wrong)
                self.assertIsNone(dialog.selected)
                self.assertIn('只有 1 页', dialog.error_label.text())
                dialog.pages = [1]
                confirm = Mock()
                confirm.exec.return_value = False
                with patch('ui.dialogs.presentation_link_dialog.MessageBox', return_value=confirm):
                    dialog.select_path(wrong)
                    self.assertIsNone(dialog.selected)
                    confirm.exec.assert_called_once()
                    confirm.exec.return_value = True
                    dialog.select_path(wrong)
                self.assertEqual(Path(dialog.selected['path']), wrong)
            finally:
                dialog.deleteLater()


class LinkedSlideshowTests(unittest.TestCase):
    def test_only_associated_document_receives_com_page_turn(self):
        path = Path('linked.pptx').resolve()
        wrong_view, selected_view = Mock(), Mock()
        pp = NS(SlideShowWindows=Mock(Count=1))
        pp.SlideShowWindows.side_effect = lambda index: NS(Presentation=NS(FullName='other.pptx'), View=wrong_view)
        wps = NS(SlideShowWindows=Mock(Count=1))
        wps.SlideShowWindows.side_effect = lambda index: NS(Presentation=NS(FullName=str(path)), View=selected_view)
        with patch('app.presentation.slideshow._running_apps', side_effect=lambda: iter([('PowerPoint', pp), ('WPS', wps)])):
            self.assertTrue(slideshow.advance(path))
            self.assertTrue(slideshow.goto(2, path))
        wrong_view.Next.assert_not_called()
        wrong_view.GotoSlide.assert_not_called()
        selected_view.Next.assert_called_once()
        selected_view.GotoSlide.assert_called_once_with(2)

    def test_unrelated_document_events_do_not_move_audio_position(self):
        watcher = NS(source_path=Path('linked.pptx').resolve(), show_began=Mock(),
                     slide_changed=Mock(), show_ended=Mock())
        handler = slideshow._make_handler(watcher)()
        wrong = NS(Presentation=NS(FullName='wrong.pptx'), View=NS(CurrentShowPosition=2))
        selected = NS(Presentation=NS(FullName=str(watcher.source_path)), View=NS(CurrentShowPosition=3))
        handler.OnSlideShowBegin(wrong)
        handler.OnSlideShowNextSlide(wrong)
        handler.OnSlideShowEnd(wrong.Presentation)
        watcher.show_began.emit.assert_not_called()
        watcher.slide_changed.emit.assert_not_called()
        watcher.show_ended.emit.assert_not_called()
        handler.OnSlideShowBegin(selected)
        handler.OnSlideShowNextSlide(selected)
        handler.OnSlideShowEnd(selected.Presentation)
        watcher.show_began.emit.assert_called_once()
        watcher.slide_changed.emit.assert_called_once_with(3)
        watcher.show_ended.emit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
