"""返回手动定位前的讲稿片段，自动连播不写入导航历史。"""

from unittest.mock import patch

from test_sync_playback import SyncPlaybackFixture


class PlaybackNavigationHistoryTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.page.project.notes = {page: f'第 {page} 页' for page in (1, 2, 3)}
        self.page.project.notes_list = [
            {'page': page, 'text': f'第 {page} 页第 {segment} 段'}
            for page in (1, 2, 3) for segment in (1, 2)
        ]
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 6
        self.page.project.current_index = 1
        self.page.set_current_label_text()

    def test_page_button_returns_to_original_segment_and_keeps_stopped(self):
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        with patch.object(self.page, 'play_audio') as play, \
             patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.nextPageButton.click()
            self.assertEqual(self.page.project.current_index, 2)
            self.assertTrue(self.page.returnPositionButton.isEnabled())
            self.assertEqual(self.page.returnPositionButton.toolTip(), '返回第 1 页 · 第 2 段')
            self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.assertFalse(self.page._playing_sequence)
        play.assert_not_called()
        prepare.assert_not_called()

    def test_segment_and_page_jump_and_reset_support_successive_returns(self):
        self.page.playing_script_preview.segment_requested.emit(4)
        self.page.playing_script_preview.page_requested.emit(3)
        self.page.resetButton.click()
        self.assertEqual(self.page.project.current_index, 0)
        for expected in (4, 3, 1):
            self.page.returnPositionButton.click()
            self.assertEqual(self.page.project.current_index, expected)
        self.assertFalse(self.page.returnPositionButton.isEnabled())

    def test_playing_return_starts_target_segment_without_countdown(self):
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        with patch.object(self.page.player, 'setSource'), \
             patch.object(self.page.player, 'play') as play, \
             patch.object(self.page, 'play_wait') as countdown:
            self.page.nextPageButton.click()
            self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertTrue(self.page._playing_sequence)
        self.assertTrue(self.page.is_play_notes)
        self.assertEqual(play.call_count, 2)
        countdown.assert_not_called()

    def test_playing_return_uses_existing_com_position_alignment(self):
        self.page._jump_to_position('segment', 4)
        self.page.set_sync_playback_enabled(True)
        self.ctx.app_settings.set('page_turn_mode', 'com')
        self.ctx.app_settings.set('slideshow_sync_navigation', True)
        self.page._playing_sequence = True
        with patch.object(self.page, '_prepare_playback_show') as prepare:
            self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertEqual(self.page._segment_offset(), 1)
        prepare.assert_called_once_with(countdown=False)

    def test_auto_advance_and_completion_do_not_add_return_locations(self):
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        with patch.object(self.page, 'play_audio'):
            self.page.timeout_play_next_audio()
        self.assertEqual(self.page.project.current_index, 2)
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.page.project.current_index = 5
        self.page.timeout_play_next_audio()
        self.assertEqual(self.page.project.current_index, 0)
        self.assertFalse(self.page.returnPositionButton.isEnabled())

    def test_non_com_return_only_repositions_narration(self):
        self.page.set_sync_playback_enabled(True)
        for mode in ('foreground', 'window'):
            with self.subTest(mode=mode), \
                 patch.object(self.page, '_prepare_playback_show') as prepare, \
                 patch.object(self.page.player, 'setSource'), \
                 patch.object(self.page.player, 'play') as play:
                self.ctx.app_settings.set('page_turn_mode', mode)
                self.page._playing_sequence = False
                self.page._jump_to_position('segment', 4)
                self.page._playing_sequence = True
                self.page.returnPositionButton.click()
                self.assertEqual(self.page.project.current_index, 1)
                self.assertTrue(self.page._playing_sequence)
                prepare.assert_not_called()
                play.assert_called_once()

    def test_segment_hotkeys_can_return_without_recording_a_replay(self):
        with patch.object(self.page.player, 'setSource'), patch.object(self.page.player, 'play'):
            self.page.next_segment()
            self.page.restart_segment()
            self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertFalse(self.page.returnPositionButton.isEnabled())

    def test_refreshing_at_a_saved_position_does_not_consume_it(self):
        self.page.nextPageButton.click()
        self.page.project.current_index = 1
        self.page.set_current_label_text()
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.page.project.current_index = 3
        self.page.set_current_label_text()
        self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)

    def test_no_op_and_invalid_jump_do_not_record_positions(self):
        self.page._jump_to_position('segment', 2)
        self.page._jump_to_position('segment', 100)
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.page.project.current_index = 0
        self.page.resetButton.click()
        self.assertFalse(self.page.returnPositionButton.isEnabled())

    def test_replacing_script_or_clearing_audio_invalidates_history(self):
        self.page.nextPageButton.click()
        self.page.project.notes_list = list(self.page.project.notes_list)
        self.page.set_current_label_text()
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.page.previousPageButton.click()
        self.assertTrue(self.page.returnPositionButton.isEnabled())
        self.page.project.clear_audio()
        self.page.check_import()
        self.assertFalse(self.page.returnPositionButton.isEnabled())
        self.page.project.audio_loaded = True
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 6
        self.page.check_import()
        self.assertFalse(self.page.returnPositionButton.isEnabled())

    def test_busy_tasks_disable_return_without_consuming_history(self):
        self.page.nextPageButton.click()
        with patch.object(self.page, 'is_busy', return_value=True):
            self.page.refresh_presentation_state()
            self.assertFalse(self.page.returnPositionButton.isEnabled())
            self.page.return_to_previous_position()
            self.assertEqual(self.page.project.current_index, 2)
        self.page.refresh_presentation_state()
        self.page.returnPositionButton.click()
        self.assertEqual(self.page.project.current_index, 1)

    def test_history_has_a_bounded_number_of_manual_locations(self):
        notes = [{'page': 1, 'text': f'第 {index} 段'} for index in range(26)]
        self.page.project.notes_list = notes
        self.page.project.media_list = [self.source.with_suffix('.wav')] * len(notes)
        self.page.project.current_index = 0
        self.page.set_current_label_text()
        for number in range(2, 27):
            self.page._jump_to_position('segment', number)
        for expected in range(24, 4, -1):
            self.page.returnPositionButton.click()
            self.assertEqual(self.page.project.current_index, expected)
        self.assertFalse(self.page.returnPositionButton.isEnabled())
