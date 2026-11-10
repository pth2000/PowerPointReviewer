"""讲稿时长分布、实时剩余时长，以及正文进入审听的回归场景。"""

from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from qt_test_case import QtTestCase
from test_sync_playback import SyncPlaybackFixture

from app.documents.statistics import build_statistics
from ui.playback_overview import PlaybackOverview


class PlaybackOverviewTests(QtTestCase):
    def setUp(self):
        self.widget = PlaybackOverview()
        self.widget.resize(880, 132)
        self.widget.show()
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.close)
        self.notes = {1: '甲●乙', 3: '丙', 7: '丁丁'}
        self.segments = [{'page': page} for page in (1, 1, 3, 7)]
        self.durations = [1.2, 2.3, 0, 60]

    def load(self, durations=True):
        values = self.durations if durations else None
        statistics = build_statistics(self.notes, self.segments, values)
        self.widget.set_statistics(statistics, self.segments, values or ())
        self.widget.set_available(True)
        self.qt.processEvents()

    def test_remaining_uses_segment_position_and_clamps_to_its_duration(self):
        self.load()
        self.widget.set_position(1, 1000)
        self.assertEqual(self.widget.current_page, 1)
        self.assertAlmostEqual(self.widget.remaining, 61.3)
        self.widget.set_position(1, 90000)
        self.assertAlmostEqual(self.widget.remaining, 60)
        self.widget.set_position(2)
        self.assertEqual(self.widget.current_page, 3)
        self.assertAlmostEqual(self.widget.remaining, 60)
        self.widget.set_position(3, 60000)
        self.assertEqual(self.widget.remaining, 0)

    def test_before_generation_retains_word_counts_without_making_up_durations(self):
        self.load(durations=False)
        self.widget.set_position(2)
        self.assertEqual(self.widget.statistics_button.characters, 5)
        self.assertEqual(self.widget.chart.pages[1].characters, 1)
        self.assertIsNone(self.widget.remaining)
        self.assertIsNone(self.widget.chart.pages[1].duration)
        self.assertFalse(self.widget.chart._bins)
        self.assertEqual(self.widget.current_page, 3)

    def test_dense_pages_have_bounded_bins_and_cover_every_page(self):
        notes = dict.fromkeys(range(1, 2001), '讲稿')
        segments = [{'page': page} for page in notes]
        durations = [page % 13 for page in notes]
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.widget.resize(640, 132)
        self.widget.set_position(1323)
        self.qt.processEvents()
        bins = self.widget.chart._bins
        self.assertLessEqual(len(bins), self.widget.chart.width() // 7)
        self.assertEqual([row.page for _, group in bins for row in group], list(notes))
        self.assertTrue(all(rect.width() > 0 for rect, _ in bins))
        self.assertLessEqual(bins[-1][0].right(), self.widget.chart.width() - 21)
        self.assertEqual(self.widget.current_page, 1324)
        self.widget.grab()

    def test_wide_pages_stay_individual_and_only_adjacent_short_pages_are_grouped(self):
        notes = dict.fromkeys(range(1, 205), '讲稿')
        segments = [{'page': page} for page in notes]
        durations = [100, 200, 300, 400] + [0.1] * 200
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.qt.processEvents()
        bins = self.widget.chart._bins
        self.assertEqual([tuple(row.page for row in group) for _, group in bins[:4]], [(1,), (2,), (3,), (4,)])
        self.assertTrue(any(len(group) > 1 for _, group in bins[4:]))
        self.assertEqual([row.page for _, group in bins for row in group], list(notes))
        self.assertTrue(all(rect.width() >= 6 - 1e-6 for rect, _ in bins))

    def test_zero_duration_pages_remain_reachable_and_width_stays_within_the_chart(self):
        notes = dict.fromkeys(range(1, 301), '')
        segments = [{'page': page} for page in notes]
        durations = [0] * len(notes)
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.qt.processEvents()
        bins = self.widget.chart._bins
        self.assertEqual([row.page for _, group in bins for row in group], list(notes))
        self.assertTrue(all(rect.width() >= 6 - 1e-6 for rect, _ in bins))
        self.assertAlmostEqual(bins[-1][0].right(), self.widget.chart.width() - 22)

    def test_widening_the_chart_splits_short_page_groups_without_merging_long_pages(self):
        notes = dict.fromkeys(range(1, 123), '讲稿')
        segments = [{'page': page} for page in notes]
        durations = [100] + [1] * 120 + [100]
        self.widget.resize(640, 132)
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.qt.processEvents()
        narrow_count = len(self.widget.chart._bins)
        self.widget.resize(1200, 132)
        self.qt.processEvents()
        self.assertGreater(len(self.widget.chart._bins), narrow_count)
        self.assertEqual([row.page for row in self.widget.chart._bins[0][1]], [1])
        self.assertEqual([row.page for row in self.widget.chart._bins[-1][1]], [122])

    def test_summary_can_be_opened_with_keyboard_and_is_disabled_when_unavailable(self):
        self.load()
        button = self.widget.statistics_button
        self.assertEqual(button.height(), self.widget.height())
        clicks = QSignalSpy(button.clicked)
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Return)
        self.assertEqual(clicks.count(), 1)
        self.widget.set_available(False)
        button.click()
        self.assertEqual(clicks.count(), 1)
        self.assertNotIn('尚未导入', button.footer())

    def test_click_selects_the_page_and_dragging_outside_cancels(self):
        self.load()
        self.widget.set_navigation_enabled(True)
        chart = self.widget.chart
        requested = QSignalSpy(self.widget.page_requested)
        point = chart._bins[1][0].center().toPoint()
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(requested.at(0), [3])
        QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        self.assertEqual(requested.count(), 1)
        self.widget.set_navigation_enabled(False)
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(requested.count(), 1)
        self.assertEqual(chart.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_keyboard_selects_a_page_without_a_mouse(self):
        self.load()
        self.widget.set_navigation_enabled(True)
        chart = self.widget.chart
        requested = QSignalSpy(self.widget.page_requested)
        chart.setFocus()
        QTest.keyClick(chart, Qt.Key.Key_Right)
        QTest.keyClick(chart, Qt.Key.Key_Return)
        self.assertEqual(requested.at(0), [3])

    def test_clicking_chart_background_gives_focus_for_real_keyboard_events(self):
        self.load()
        self.widget.set_navigation_enabled(True)
        self.widget.set_position(0)
        self.widget.activateWindow()
        self.widget.statistics_button.setFocus()
        chart = self.widget.chart
        requested = QSignalSpy(self.widget.page_requested)
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(110, 30))
        self.assertIs(self.qt.focusWidget(), chart)
        self.assertEqual(requested.count(), 0)
        QTest.keyClick(self.qt.focusWidget(), Qt.Key.Key_Right)
        self.assertEqual(chart._keyboard_index, 1)
        self.assertIn('第 3 页', chart.accessibleDescription())
        QTest.mouseMove(chart, QPoint(1, 1))
        QTest.keyClick(self.qt.focusWidget(), Qt.Key.Key_Return)
        self.assertEqual(requested.at(0), [3])

    def test_keyboard_can_select_an_exact_page_inside_a_merged_bar(self):
        notes = dict.fromkeys(range(1, 301), '讲稿')
        segments = [{'page': page} for page in notes]
        durations = [1] * len(notes)
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.widget.set_navigation_enabled(True)
        self.widget.set_position(149)
        chart = self.widget.chart
        requested = QSignalSpy(self.widget.page_requested)
        chart.setFocus()
        QTest.keyClick(chart, Qt.Key.Key_Right)
        QTest.keyClick(chart, Qt.Key.Key_Return)
        self.assertEqual(requested.at(0), [151])
        self.assertIsNone(chart._page_menu)

    def test_merged_bar_opens_exact_page_choices_and_menu_is_closed_when_disabled(self):
        notes = dict.fromkeys(range(1, 301), '讲稿')
        segments = [{'page': page} for page in notes]
        durations = [1] * len(notes)
        self.widget.set_statistics(build_statistics(notes, segments, durations), segments, durations)
        self.widget.set_navigation_enabled(True)
        self.qt.processEvents()
        chart = self.widget.chart
        rect, group = chart._bins[3]
        self.assertGreater(len(group), 1)
        requested = QSignalSpy(self.widget.page_requested)
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        menu = chart._page_menu
        self.assertIsNotNone(menu)
        self.assertEqual(requested.count(), 0)
        actions = menu.menuActions()
        self.assertEqual(len(actions), len(group))
        actions[-1].trigger()
        self.assertEqual(requested.at(0), [group[-1].page])
        self.widget.set_navigation_enabled(False)
        self.assertIsNone(chart._page_menu)


class HomeOverviewTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.page.project.media_list = [self.source.with_suffix('.wav')] * 2
        self.page.project.notes_duration_list = [10, 20]
        self.page.check_import()

    def test_home_updates_position_without_rebuilding_statistics_or_reading_files(self):
        self.page.project.current_index = 1
        self.page.set_current_label_text()
        self.page.is_play_notes = True
        with patch.object(self.page.player, 'position', return_value=5000), \
             patch('ui.pages.reviewer_page.build_statistics') as build:
            self.page._refresh_overview_position()
            build.assert_not_called()
        overview = self.page.playbackOverview
        self.assertEqual(overview.current_page, 2)
        self.assertEqual(overview.remaining, 15)
        self.page.is_play_notes = False
        self.page._refresh_overview_position()
        self.assertEqual(overview.remaining, 20)

    def test_updates_follow_new_audio_and_comment_filtering(self):
        self.page.project.script_comments_enabled = True
        self.page.project.notes = {1: '// 不计入字数\n正文', 2: '结束'}
        self.page.project.notes_duration_list = [4, 6]
        self.page.audio_changed.emit()
        self.assertEqual(self.page.playbackOverview.statistics_button.characters, 4)
        self.assertEqual(self.page.playbackOverview.remaining, 10)
        self.page.project.audio_loaded = False
        self.page.check_import()
        self.assertIsNone(self.page.playbackOverview.statistics_button.duration)
        self.assertFalse(self.page.playbackOverview.chart._bins)
        self.assertTrue(self.page.infoPushButton.isEnabled())

    def test_body_opens_current_review_without_interfering_with_page_navigation(self):
        self.page.show()
        self.qt.processEvents()
        preview = self.page.playing_script_preview
        with patch('ui.pages.reviewer_page.AudioReviewDialog') as dialog:
            QTest.mouseClick(preview.script_area, Qt.MouseButton.LeftButton,
                             pos=preview.script_area.rect().center())
            dialog.return_value.focus_segment.assert_called_once_with(0)
            dialog.return_value.show.assert_called_once()
            QTest.mouseClick(preview.page_position, Qt.MouseButton.LeftButton,
                             pos=QPoint(10, 10))
            self.assertEqual(dialog.return_value.show.call_count, 1)

    def test_body_cannot_open_review_before_generation_or_while_busy(self):
        preview = self.page.playing_script_preview
        with patch.object(self.page, 'is_busy', return_value=True):
            self.page.refresh_presentation_state()
            self.assertFalse(preview.script_area.isEnabled())
        self.page.project.audio_loaded = False
        self.page.check_import()
        self.assertFalse(preview.script_area.isEnabled())
        self.assertEqual(preview.script_area.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def click_page(self, index):
        chart = self.page.playbackOverview.chart
        self.page.show()
        self.qt.processEvents()
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart._bins[index][0].center().toPoint())

    def test_bar_click_keeps_stopped_state_and_records_return_position(self):
        with patch.object(self.page, 'play_audio') as audio:
            self.click_page(1)
        self.assertEqual(self.page.project.current_index, 1)
        self.assertEqual(self.page.playing_script_preview.text(), '第二页')
        self.assertFalse(self.page._playing_sequence)
        self.assertTrue(self.page.returnPositionButton.isEnabled())
        audio.assert_not_called()

    def test_playing_bar_click_restarts_target_without_repeating_countdown(self):
        self.page._playing_sequence = True
        with patch.object(self.page, 'play_audio') as audio, patch.object(self.page, 'play_wait') as countdown:
            self.click_page(1)
        self.assertEqual(self.page.project.current_index, 1)
        audio.assert_called_once()
        countdown.assert_not_called()

    def test_clicking_current_page_restarts_at_its_first_segment(self):
        self.page._playing_sequence = True
        with patch.object(self.page, 'play_audio') as audio:
            self.click_page(0)
        self.assertEqual(self.page.project.current_index, 0)
        audio.assert_called_once()

    def test_busy_state_blocks_bar_navigation(self):
        with patch.object(self.page, 'is_busy', return_value=True):
            self.page.refresh_presentation_state()
            self.click_page(1)
            self.assertEqual(self.page.project.current_index, 0)

    def test_clicked_bar_keeps_focus_for_following_keyboard_page_selection(self):
        self.page.show()
        self.page.activateWindow()
        self.qt.processEvents()
        chart = self.page.playbackOverview.chart
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart._bins[0][0].center().toPoint())
        self.assertIs(self.qt.focusWidget(), chart)
        QTest.keyClick(self.qt.focusWidget(), Qt.Key.Key_Right)
        self.assertEqual(self.page.project.current_index, 0)
        QTest.keyClick(self.qt.focusWidget(), Qt.Key.Key_Return)
        self.assertEqual(self.page.project.current_index, 1)
