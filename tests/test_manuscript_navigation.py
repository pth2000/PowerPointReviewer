"""进度原位输入的鼠标、焦点与播放位置联动。"""

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QSignalSpy, QTest
from qt_test_case import QtTestCase
from test_sync_playback import SyncPlaybackFixture

from ui.manuscript_preview import ManuscriptPreview


class NavigationInputTests(QtTestCase):
    def setUp(self):
        self.widget = ManuscriptPreview()
        self.widget.resize(700, self.widget.height())
        self.widget.set_position(page=4, page_count=18, segment=12, segment_count=56)
        self.widget.show()
        self.qt.processEvents()
        self.page = self.widget.page_position
        self.segment = self.widget.segment_position
        self.pages = QSignalSpy(self.widget.page_requested)
        self.segments = QSignalSpy(self.widget.segment_requested)
        self.errors = QSignalSpy(self.widget.navigation_error)
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)

    def edit(self, control, text):
        QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
        self.assertTrue(control.editor.isVisible())
        QTest.keyClicks(control.editor, text)

    def click_body(self):
        QTest.mouseClick(self.widget.script_area, Qt.MouseButton.LeftButton,
                         pos=self.widget.script_area.rect().center())

    def test_nonfocusable_body_click_confirms_once(self):
        self.edit(self.page, '7')
        self.click_body()
        self.click_body()
        self.assertEqual(self.pages.count(), 1)
        self.assertEqual(self.pages.at(0), [7])
        self.assertFalse(self.page.editor.isVisible())

    def test_clicking_the_current_group_keeps_the_draft(self):
        self.edit(self.page, '7')
        QTest.mouseClick(self.page, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        self.assertTrue(self.page.editor.isVisible())
        self.assertEqual(self.page.editor.text(), '7')
        self.assertTrue(self.page.editor.hasFocus())
        self.assertEqual(self.pages.count(), 0)

    def test_switching_groups_confirms_first_and_edits_second(self):
        self.edit(self.page, '7')
        self.edit(self.segment, '25')
        self.assertEqual(self.pages.at(0), [7])
        self.assertFalse(self.page.editor.isVisible())
        self.click_body()
        self.assertEqual(self.segments.at(0), [25])

    def test_enter_confirms_and_escape_cancels(self):
        self.edit(self.page, '7')
        QTest.keyClick(self.page.editor, Qt.Key.Key_Return)
        self.click_body()
        self.assertEqual(self.pages.count(), 1)
        self.edit(self.segment, '25')
        QTest.keyClick(self.segment.editor, Qt.Key.Key_Escape)
        self.click_body()
        self.assertEqual(self.segments.count(), 0)
        self.assertEqual(self.segment.value, 12)

    def test_tab_confirms_but_window_deactivation_does_not(self):
        self.edit(self.page, '7')
        QCoreApplication.sendEvent(
            self.page.editor, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertEqual(self.pages.count(), 0)
        self.assertTrue(self.page.editor.isVisible())
        QTest.keyClick(self.page.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.pages.count(), 1)
        self.assertFalse(self.page.editor.isVisible())

    def test_invalid_or_empty_input_preserves_position_and_reports_the_range(self):
        for text in ('0', '99', ''):
            self.edit(self.page, text)
            if not text:
                self.page.editor.clear()
            self.click_body()
            self.assertEqual(self.page.value, 4)
        self.assertEqual(self.pages.count(), 0)
        self.assertEqual(self.errors.count(), 3)
        self.assertIn('1 到 18', self.errors.at(0)[0])

    def test_playback_updates_do_not_replace_draft_or_move_the_text(self):
        self.edit(self.page, '7')
        geometry = self.segment.geometry()
        self.widget.set_position(page=5, page_count=18, segment=13, segment_count=56)
        self.assertEqual(self.page.editor.text(), '7')
        self.assertEqual(self.segment.geometry(), geometry)
        self.click_body()
        self.assertEqual(self.pages.at(0), [7])

    def test_unchanged_input_does_not_rewind_after_playback_advances(self):
        self.edit(self.page, '4')
        self.widget.set_position(page=5, page_count=18, segment=13, segment_count=56)
        self.click_body()
        self.assertEqual(self.pages.count(), 0)
        self.assertEqual(self.page.value, 5)

    def test_clear_or_disable_cancels_the_pending_navigation(self):
        self.edit(self.page, '7')
        self.widget.set_navigation_enabled(False)
        self.click_body()
        self.assertEqual(self.pages.count(), 0)
        self.assertFalse(self.page.editor.isVisible())
        self.widget.set_navigation_enabled(True)
        self.edit(self.page, '7')
        self.widget.set_position()
        self.assertFalse(self.page.editor.isVisible())
        self.assertFalse(self.page.isEnabled())
        self.assertEqual(self.pages.count(), 0)


class HomeNavigationTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.page.project.notes_list = [
            {'page': 1, 'text': '第一段'}, {'page': 1, 'text': '第二段'},
            {'page': 2, 'text': '第三段'}, {'page': 2, 'text': '第四段'},
        ]
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 4
        self.page.project.current_index = 1
        self.page._refresh_playing_script()

    def test_page_navigation_while_stopped_selects_the_first_segment_without_playing(self):
        with patch.object(self.page, 'play_audio') as audio:
            self.page.playing_script_preview.page_requested.emit(2)
        self.assertEqual(self.page.project.current_index, 2)
        self.assertFalse(self.page._playing_sequence)
        self.assertEqual(self.page.playing_script_preview.text(), '第三段')
        audio.assert_not_called()

    def test_segment_navigation_uses_global_index_and_keeps_stopped_state(self):
        self.page.playing_script_preview.segment_requested.emit(4)
        self.assertEqual(self.page.project.current_index, 3)
        self.assertEqual(self.page.playing_script_preview.text(), '第四段')
        self.assertEqual(self.page._segment_offset(), 1)
        self.assertFalse(self.page._playing_sequence)

    def test_playing_navigation_restarts_at_target_without_countdown(self):
        self.page._playing_sequence = True
        with patch.object(self.page, 'play_audio') as audio, patch.object(self.page, 'play_wait') as countdown:
            self.page.playing_script_preview.page_requested.emit(2)
        self.assertEqual(self.page.project.current_index, 2)
        self.assertTrue(self.page.is_play_notes)
        audio.assert_called_once()
        countdown.assert_not_called()

    def test_com_navigation_prepares_the_requested_animation_offset(self):
        self.page.set_sync_playback_enabled(True)
        self.page._playing_sequence = True
        self.ctx.app_settings.set('page_turn_mode', 'com')
        self.ctx.app_settings.set('slideshow_sync_navigation', True)
        with patch.object(self.page, 'can_sync_playback', return_value=True), \
             patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.playing_script_preview.segment_requested.emit(4)
        self.assertEqual(self.page.project.current_index, 3)
        self.assertEqual(self.page._segment_offset(), 1)
        prepare.assert_called_once_with(countdown=False)

    def test_page_without_notes_only_notifies_and_keeps_position(self):
        self.page.project.notes_list = [{'page': 1, 'text': '第一页'}]
        self.page.project.current_index = 0
        with patch.object(self.page, 'create_warning_info_bar') as warning:
            self.page.playing_script_preview.page_requested.emit(2)
        self.assertEqual(self.page.project.current_index, 0)
        warning.assert_called_once_with('此页没有讲稿', '当前讲稿不包含第 2 页')

    def test_preparation_disables_navigation_and_restores_it_when_finished(self):
        with patch.object(self.page, 'refresh_presentation_state'), \
             patch.object(self.page.presentation_monitor, 'poll'):
            self.page._prepare_playback_show()
            self.assertFalse(self.page.playing_script_preview.page_position.isEnabled())
            self.page.playing_script_preview.page_requested.emit(2)
            self.assertEqual(self.page.project.current_index, 1)
            self.page.playback_show.cancel_preparation()
            self.page._finish_playback_preparation()
            self.assertTrue(self.page.playing_script_preview.page_position.isEnabled())

    def test_replacing_the_script_cancels_input_even_if_counts_match(self):
        preview = self.page.playing_script_preview
        self.page.show()
        self.qt.processEvents()
        preview.page_position.begin_edit()
        preview.page_position.editor.setText('2')
        self.page.project.notes_list = list(self.page.project.notes_list)
        self.page._refresh_playing_script()
        self.assertFalse(preview.page_position.editor.isVisible())
        self.assertEqual(self.page.project.current_index, 1)


class PageNavigationButtonsTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.page.project.notes = {1: '第一页', 3: '第三页', 5: '第五页'}
        self.page.project.notes_list = [
            {'page': page, 'text': f'第 {page} 页第 {segment} 段'}
            for page in (1, 3, 5) for segment in (1, 2)
        ]
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 6
        self.page.project.current_index = 3
        self.page.set_current_label_text()

    def test_buttons_move_between_pages_instead_of_individual_segments(self):
        with patch.object(self.page, 'play_audio') as audio:
            self.page.previousPageButton.click()
            self.assertEqual(self.page.project.current_index, 0)
            self.page.nextPageButton.click()
            self.assertEqual(self.page.project.current_index, 2)
            self.page.nextPageButton.click()
            self.assertEqual(self.page.project.current_index, 4)
        self.assertFalse(self.page._playing_sequence)
        audio.assert_not_called()

    def test_boundaries_disable_buttons_without_wrapping_or_restarting(self):
        self.page.project.current_index = 0
        self.page.set_current_label_text()
        self.assertFalse(self.page.previousPageButton.isEnabled())
        self.assertTrue(self.page.nextPageButton.isEnabled())
        self.page.previousPageButton.click()
        self.assertEqual(self.page.project.current_index, 0)
        self.page.project.current_index = 5
        self.page.set_current_label_text()
        self.assertTrue(self.page.previousPageButton.isEnabled())
        self.assertFalse(self.page.nextPageButton.isEnabled())
        self.page.nextPageButton.click()
        self.assertEqual(self.page.project.current_index, 5)

    def test_playing_navigation_keeps_playing_and_skips_countdown(self):
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        with patch.object(self.page.player, 'setSource'), patch.object(self.page.player, 'play') as play, \
             patch.object(self.page, 'play_wait') as countdown:
            self.page.nextPageButton.click()
        self.assertEqual(self.page.project.current_index, 4)
        self.assertTrue(self.page._playing_sequence)
        self.assertEqual(self.page.playButton.accessibleName(), '停止')
        play.assert_called_once()
        countdown.assert_not_called()

    def test_countdown_navigation_starts_target_page_without_replaying_countdown(self):
        self.page._playing_sequence = True
        self.page.is_play_notes = False
        self.page.next_timer.start(100)
        with patch.object(self.page.player, 'setSource'), patch.object(self.page.player, 'play') as play, \
             patch.object(self.page, 'play_wait') as countdown:
            self.page.previousPageButton.click()
        self.assertEqual(self.page.project.current_index, 0)
        self.assertTrue(self.page.is_play_notes)
        self.assertTrue(self.page._playing_sequence)
        self.assertFalse(self.page.next_timer.isActive())
        play.assert_called_once()
        countdown.assert_not_called()

    def test_com_navigation_prepares_the_target_page_from_its_first_animation(self):
        self.page.set_sync_playback_enabled(True)
        self.page._playing_sequence = True
        self.ctx.app_settings.set('page_turn_mode', 'com')
        self.ctx.app_settings.set('slideshow_sync_navigation', True)
        with patch.object(self.page, 'can_sync_playback', return_value=True), \
             patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.nextPageButton.click()
        self.assertEqual(self.page.project.notes_list[self.page.project.current_index]['page'], 5)
        self.assertEqual(self.page._segment_offset(), 0)
        prepare.assert_called_once_with(countdown=False)

    def test_single_page_or_missing_audio_disables_both_buttons(self):
        self.page.project.notes_list = self.page.project.notes_list[:2]
        self.page.project.media_list = self.page.project.media_list[:2]
        self.page.project.current_index = 1
        self.page.check_import()
        self.assertFalse(self.page.previousPageButton.isEnabled())
        self.assertFalse(self.page.nextPageButton.isEnabled())
        self.page.project.clear_audio()
        self.page.check_import()
        self.assertFalse(self.page.previousPageButton.isEnabled())
        self.assertFalse(self.page.nextPageButton.isEnabled())

    def test_show_preparation_disables_page_buttons_until_ready(self):
        with patch.object(self.page, 'refresh_presentation_state'), \
             patch.object(self.page.presentation_monitor, 'poll'):
            self.page._prepare_playback_show()
            self.assertFalse(self.page.previousPageButton.isEnabled())
            self.assertFalse(self.page.nextPageButton.isEnabled())
            self.page.playback_show.cancel_preparation()
            self.page._finish_playback_preparation()
        self.assertTrue(self.page.previousPageButton.isEnabled())
        self.assertTrue(self.page.nextPageButton.isEnabled())
