"""首页讲稿预览跟随片段和播放状态，三行溢出保持固定高度。"""

from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from qt_test_case import QtTestCase
from test_sync_playback import SyncPlaybackFixture

from ui.manuscript_preview import ManuscriptPreview


class ManuscriptPreviewTests(QtTestCase):
    def setUp(self):
        self.widget = ManuscriptPreview()
        self.widget.resize(500, self.widget.height())
        self.widget.show()
        self.qt.processEvents()
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)

    def test_long_text_is_faded_without_growing_and_has_a_body_only_tooltip(self):
        height = self.widget.height()
        text = '这是一段需要完整保留、仅在显示区域渐隐的较长讲稿。' * 25
        self.widget.set_content(text)
        self.widget.grab()
        self.assertTrue(self.widget.has_overflow)
        self.assertEqual(self.widget.height(), height)
        self.assertEqual(self.widget.text(), text)
        self.assertEqual(self.widget.toolTip(), '')
        self.assertEqual(self.widget.script_area.toolTip(), text)
        self.assertNotEqual(self.widget.page_position.toolTip(), text)
        self.assertEqual(self.widget.accessibleDescription(), text)
        self.widget.set_content('短句完整显示。')
        self.widget.grab()
        self.assertFalse(self.widget.has_overflow)
        self.assertEqual(self.widget.height(), height)
        self.assertEqual(self.widget.script_area.toolTip(), '短句完整显示。')

    def test_resize_and_font_changes_update_wrapping(self):
        self.widget.set_content('请对照当前讲稿检查正在播放的内容。' * 6)
        self.widget.resize(240, self.widget.height())
        self.widget.grab()
        self.assertTrue(self.widget.has_overflow)
        self.widget.resize(1600, self.widget.height())
        self.widget.grab()
        self.assertFalse(self.widget.has_overflow)
        font = QFont(self.widget.font())
        font.setPixelSize(24)
        self.widget.setFont(font)
        self.widget.resize(500, self.widget.height())
        self.widget.grab()
        self.assertTrue(self.widget.has_overflow)
        self.assertGreater(self.widget.height(), 80)

    def test_body_focus_does_not_draw_an_extra_frame(self):
        self.widget.set_content('点击讲稿可进入配音审听。')
        self.widget.set_review_available(True)
        before = self.widget.grab().toImage()
        self.widget.script_area.setFocus(Qt.FocusReason.MouseFocusReason)
        after = self.widget.grab().toImage()
        self.assertEqual(before, after)
        self.assertEqual(self.widget.script_area.focusPolicy(), Qt.FocusPolicy.TabFocus)

    def test_overflow_image_has_transparent_lower_edge(self):
        self.widget.set_content('用于确认渐隐透明度的讲稿文字。' * 50)
        image = self.widget._text_image(240)
        self.assertTrue(self.widget.has_overflow)
        upper = [image.pixelColor(x, y).alpha() for y in range(image.height() // 2)
                 for x in range(image.width())]
        lower = [image.pixelColor(x, y).alpha()
                 for y in range(image.height() - 2, image.height())
                 for x in range(image.width())]
        self.assertGreater(max(upper), 200)
        self.assertLess(max(lower), 90)
        self.assertEqual(self.widget.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_progress_keeps_text_width_stable_as_position_changes(self):
        self.widget.set_content('正在播放的讲稿。')
        self.widget.set_position(page=1, page_count=18, segment=9, segment_count=56)
        width = self.widget._progress_column_width()
        self.widget.set_position(page=4, page_count=18, segment=10, segment_count=56)
        self.assertEqual(self.widget._progress_column_width(), width)
        self.assertEqual(self.widget.progress_text(), '页码 4 / 18 · 片段 10 / 56')
        self.assertIn(self.widget.progress_text(), self.widget.accessibleName())
        self.assertEqual(self.widget.accessibleDescription(), '正在播放的讲稿。')
        self.widget.resize(240, self.widget.height())
        self.widget.grab()
        self.assertLess(self.widget._progress_column_width(), self.widget.width() / 2)

    def test_remaining_paragraphs_share_fixed_space_and_full_page_tooltip(self):
        height = self.widget.height()
        self.widget.set_content('当前段', following=('稍后朗读', '下一段'),
                                page_text='已经朗读\n\n当前段\n\n稍后朗读\n\n下一段')
        self.widget.grab()
        self.assertEqual(self.widget.text(), '当前段')
        self.assertEqual(self.widget.remaining_text(), '当前段\n\n稍后朗读\n\n下一段')
        self.assertNotIn('已经朗读', self.widget.accessibleDescription())
        self.assertIn('已经朗读', self.widget.script_area.toolTip())
        self.assertEqual(self.widget.height(), height)
        self.assertFalse(self.widget.has_overflow)
        self.widget.set_content('当前段', following=('一段新的后文',))
        self.widget.resize(1600, height)
        self.widget.grab()
        self.assertFalse(self.widget.has_overflow)
        self.assertEqual(self.widget.height(), height)

    def test_current_paragraph_remains_more_prominent_in_light_and_dark_themes(self):
        font = QFont(self.widget.font())
        font.setPixelSize(18)
        self.widget.setFont(font)
        self.widget.set_content('WWWW', following=('WWWW',))
        for dark in (False, True):
            with self.subTest(dark=dark), patch('ui.manuscript_preview.isDarkTheme', return_value=dark):
                image = self.widget._text_image(400)
                split = round(self.widget._line_height())
                tones = []
                for start, end in ((0, split), (split + 2, image.height())):
                    colors = [image.pixelColor(x, y).lightness() for y in range(start, end)
                              for x in range(image.width()) if image.pixelColor(x, y).alpha() > 230]
                    self.assertTrue(colors)
                    tones.append(max(colors))
                self.assertGreater((tones[0] - tones[1]) if dark else (tones[1] - tones[0]), 40)

    def test_following_changes_invalidate_render_and_long_current_segment_stays_first(self):
        self.widget.set_content('当前', following=('后文',))
        first = self.widget._text_image(400)
        self.widget.set_content('当前', following=('改后的后文',))
        self.assertIsNot(first, self.widget._text_image(400))
        self.widget.set_content('很长的当前段。' * 50, following=('下一段',))
        self.widget._text_image(240)
        self.assertTrue(self.widget.has_overflow)
        self.assertTrue(self.widget.remaining_text().endswith('下一段'))
        self.widget.set_content('', following=('', '之后的正文'))
        self.widget.grab()
        self.assertEqual(self.widget.remaining_text(), '之后的正文')
        self.widget.set_content('', empty_text='')
        self.widget.grab()
        self.assertFalse(self.widget.has_overflow)


class HomeManuscriptPreviewTests(SyncPlaybackFixture):
    def load_page_segments(self):
        self.page.project.notes = {1: '第一页第一段●第一页第二段●第一页第三段', 2: '第二页第一段●第二页第二段'}
        self.page.project.split_notes(comments_enabled=False)
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 5
        self.page.project.current_index = 0
        self.page.check_import()

    def test_preview_shows_only_remaining_segments_on_current_page_and_restores_on_rewind(self):
        self.load_page_segments()
        preview = self.page.playing_script_preview
        full_page = '第一页第一段\n\n第一页第二段\n\n第一页第三段'
        self.assertEqual(preview.remaining_text(), full_page)
        self.page._jump_to_position('segment', 2)
        self.assertEqual(preview.remaining_text(), '第一页第二段\n\n第一页第三段')
        self.assertEqual(preview.script_area.toolTip(), full_page)
        self.page._jump_to_position('page', 2)
        self.assertEqual(preview.remaining_text(), '第二页第一段\n\n第二页第二段')
        self.assertNotIn('第一页', preview.script_area.toolTip())
        self.page._jump_to_position('page', 1)
        self.assertEqual(preview.remaining_text(), full_page)

    def test_stopping_preserves_remaining_context_and_reset_restores_first_page(self):
        self.load_page_segments()
        self.page.project.current_index = 1
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        self.page._refresh_playing_script()
        preview = self.page.playing_script_preview
        text = preview.remaining_text()
        self.page.stop_audio()
        self.assertEqual(preview.remaining_text(), text)
        self.assertEqual(preview.text(), '第一页第二段')
        self.page.reset_audio(align_show=False)
        self.assertTrue(preview.remaining_text().startswith('第一页第一段'))

    def test_adopted_spoken_text_updates_current_upcoming_and_full_page_context(self):
        self.load_page_segments()
        self.page.project.audio_edits[1] = {'spoken_text': '第二段实际读法'}
        self.page.audio_changed.emit()
        preview = self.page.playing_script_preview
        self.assertIn('第二段实际读法', preview.remaining_text())
        self.page.project.current_index = 1
        self.page.set_current_label_text()
        self.assertEqual(preview.text(), '第二段实际读法')
        self.page.project.audio_edits[0] = {'spoken_text': '第一段实际读法'}
        self.page.audio_changed.emit()
        self.assertNotIn('第一段实际读法', preview.remaining_text())
        self.assertIn('第一段实际读法', preview.script_area.toolTip())

    def test_comments_and_previous_page_are_not_added_to_preview(self):
        self.page.project.notes = {1: '// 只给讲者看的注释\n当前讲稿●后续讲稿', 2: '另一页'}
        self.page.project.split_notes(comments_enabled=True)
        self.page.check_import()
        preview = self.page.playing_script_preview
        self.assertEqual(preview.remaining_text(), '当前讲稿\n\n后续讲稿')
        self.assertEqual(preview.script_area.toolTip(), '当前讲稿\n\n后续讲稿')

    def test_preview_follows_position_and_actual_spoken_text(self):
        preview = self.page.playing_script_preview
        self.assertFalse(preview.isHidden())
        self.assertEqual(preview.text(), '第一页')
        self.assertEqual(preview.progress_text(), '页码 1 / 2 · 片段 1 / 2')
        self.page.project.current_index = 1
        self.page.set_current_label_text()
        self.assertEqual(preview.text(), '第二页')
        self.assertEqual(preview.progress_text(), '页码 2 / 2 · 片段 2 / 2')
        self.page.project.audio_edits[1] = {'spoken_text': '第二页采用的实际配音正文'}
        self.page.audio_changed.emit()
        self.assertEqual(preview.text(), '第二页采用的实际配音正文')
        self.page.reset_audio(align_show=False)
        self.assertEqual(preview.text(), '第一页')
        self.assertEqual(preview.progress_text(), '页码 1 / 2 · 片段 1 / 2')

    def test_playing_countdown_and_stopped_states_keep_correct_text(self):
        preview = self.page.playing_script_preview
        self.page._playing_sequence = True
        self.page.is_play_notes = False
        self.page._refresh_playing_script()
        self.assertEqual(preview.caption(), '即将朗读')
        self.assertEqual(preview.text(), '第一页')
        self.page.is_play_notes = True
        self.page._refresh_playing_script()
        self.assertEqual(preview.caption(), '正在朗读')
        self.page.stop_audio()
        self.assertEqual(preview.caption(), '当前讲稿')
        self.assertEqual(preview.text(), '第一页')

    def test_preview_keeps_space_when_audio_is_no_longer_ready(self):
        self.page.resize(1040, 850)
        self.page.show()
        self.qt.processEvents()
        preview = self.page.playing_script_preview
        geometry = preview.geometry()
        controls_y = self.page.playCardWidget_3.y()
        self.page.project.clear_audio()
        self.page.check_import()
        self.qt.processEvents()
        self.assertFalse(preview.isHidden())
        self.assertEqual(preview.geometry(), geometry)
        self.assertEqual(self.page.playCardWidget_3.y(), controls_y)
        self.assertEqual(preview.text(), '')
        self.assertEqual(preview.progress_text(), '')
        self.assertEqual(preview.script_area.toolTip(), '')
        self.assertEqual(preview.accessibleDescription(), '播放时显示当前讲稿')
        page, _settings, _ctx = self.make_ui(ready=False)
        self.assertFalse(page.playing_script_preview.isHidden())
        self.assertEqual(page.playing_script_preview.accessibleDescription(), '播放时显示当前讲稿')


class PlaybackToggleTests(SyncPlaybackFixture):
    def test_button_starts_playback_and_stops_between_audio_files(self):
        with patch.object(self.page, 'init_play') as start:
            self.page.playButton.click()
            start.assert_called_once()
            self.page._playing_sequence = True
            self.page.next_timer.start(100)
            self.page._refresh_playing_script()
            self.assertEqual(self.page.playButton.accessibleName(), '停止')
            self.assertTrue(self.page.playButton.isEnabled())
            self.page.check_import()
            self.assertEqual(self.page.playButton.accessibleName(), '停止')
            self.page.playButton.click()
            self.assertFalse(self.page._playing_sequence)
            self.assertFalse(self.page.next_timer.isActive())
            self.assertEqual(self.page.playButton.accessibleName(), '播放')
            self.assertTrue(self.page.playButton.isEnabled())
            start.assert_called_once()

    def test_preparing_show_can_be_stopped_with_the_same_button(self):
        with patch.object(self.page, 'refresh_presentation_state'), \
             patch.object(self.page, 'init_play') as start:
            self.page._prepare_playback_show()
            self.assertTrue(self.page.playback_show.preparing)
            self.assertEqual(self.page.playButton.accessibleName(), '停止')
            self.assertTrue(self.page.playButton.isEnabled())
            self.page.playButton.click()
            self.assertFalse(self.page.playback_show.preparing)
            self.assertFalse(self.page.playback_show.timer.isActive())
            self.assertEqual(self.page.playButton.accessibleName(), '播放')
            self.assertTrue(self.page.getFileButton.isEnabled())
            start.assert_not_called()

    def test_countdown_and_completion_restore_the_button_without_losing_position(self):
        self.page.project.current_index = 1
        self.page._playing_sequence = True
        self.page.is_play_notes = False
        self.page._refresh_playing_script()
        self.assertEqual(self.page.playButton.accessibleName(), '停止')
        self.page.playButton.click()
        self.assertEqual(self.page.project.current_index, 1)
        self.assertEqual(self.page.playButton.accessibleName(), '播放')
        self.page._playing_sequence = True
        self.page.is_play_notes = True
        self.page.timeout_play_next_audio()
        self.assertFalse(self.page._playing_sequence)
        self.assertEqual(self.page.project.current_index, 0)
        self.assertEqual(self.page.playButton.accessibleName(), '播放')
        self.assertEqual(self.page.playing_script_preview.progress_text(), '页码 1 / 2 · 片段 1 / 2')
