"""验证逐页汇总、时长缺失处理及统计入口的播放状态隔离。"""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget

from app.documents.statistics import build_statistics, count_characters, format_duration
from app.settings_store import AppSettings
from app.audio.playback import PlaybackBus
from ui.dialogs.script_statistics_dialog import ScriptStatisticsDialog
from ui.pages.reviewer_page import PPTReviewer

from qt_test_case import QtTestCase


class StatisticsTests(unittest.TestCase):
    def test_character_count_excludes_multichar_marker_and_whitespace(self):
        self.assertEqual(count_characters('你 好[分段]\nPPT！\t\x00', '[分段]'), 6)
        self.assertEqual(count_characters('●●', '●'), 0)

    def test_sparse_pages_and_page_local_segment_numbers(self):
        result = build_statistics({1: '你 好●世界！', 7: '第七页'},
                                  [{'page': 1}, {'page': 1}, {'page': 7}], [1.1, 60, 0.4])
        self.assertEqual(result.characters, 8)
        self.assertEqual(result.segments, 3)
        self.assertEqual([row.page for row in result.pages], [1, 7])
        self.assertEqual([row.segments for row in result.pages], [2, 1])
        self.assertAlmostEqual(result.pages[0].duration, 61.1)
        self.assertAlmostEqual(result.duration, 61.5)
        self.assertEqual((result.longest.page, result.longest.number), (1, 2))
        self.assertEqual((result.shortest.page, result.shortest.number), (7, 1))

    def test_missing_or_invalid_duration_keeps_text_statistics(self):
        for durations in (None, [], [1, 2], [float('nan')], [-1], ['invalid'], [None]):
            with self.subTest(durations=durations):
                result = build_statistics({3: '讲稿'}, [{'page': 3}], durations)
                self.assertEqual(result.characters, 2)
                self.assertEqual(result.segments, 1)
                self.assertIsNone(result.duration)
                self.assertIsNone(result.pages[0].duration)
                self.assertIsNone(result.longest)

    def test_duration_rounding_keeps_minutes_and_hours_consistent(self):
        self.assertEqual(format_duration(None), '待生成')
        self.assertEqual(format_duration(0), '0 秒')
        self.assertEqual(format_duration(0.4), '0.4 秒')
        self.assertEqual(format_duration(35.2), '35.2 秒')
        self.assertEqual(format_duration(62), '1 分 2 秒')
        self.assertEqual(format_duration(62.4), '1 分 2 秒')
        self.assertEqual(format_duration(62.6), '1 分 3 秒')
        self.assertEqual(format_duration(59.99), '59.99 秒')
        self.assertEqual(format_duration(59.996), '1 分')
        self.assertEqual(format_duration(3599.996), '1 小时')
        self.assertEqual(format_duration(4800), '1 小时 20 分')
        self.assertEqual(format_duration(4800.4), '1 小时 20 分')
        self.assertEqual(format_duration(4800.6), '1 小时 20 分 1 秒')


class StatisticsInterfaceTests(QtTestCase):

    def setUp(self):
        self.root = QWidget()
        self.root.resize(760, 560)
        self.root.show()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def make_page(self):
        ctx = NS(version='1.6.3', tts_engine=Mock(), app_settings=AppSettings(), config=Mock(),
                 playback_bus=PlaybackBus(), hotkeys=Mock(), slideshow_watcher=Mock())
        with patch('app.paths.ensure_runtime_dirs'):
            page = PPTReviewer(ctx, self.root)
        self.addCleanup(page.presentation_monitor.shutdown)
        page.notes = {1: '第一段●第二段', 3: '第三页'}
        page.mark_split()
        return page

    def test_statistics_available_before_audio_but_page_jump_stays_disabled(self):
        page = self.make_page()
        page.check_import()
        self.assertTrue(page.infoPushButton.isEnabled())
        self.assertFalse(page.pageJumpSpinBox.isEnabled())
        self.assertFalse(page.pageJumpToolButton.isEnabled())
        self.assertFalse(page.playButton.isEnabled())
        page.notes_duration_list = [9, 9, 9]  # 尚未就绪时不能展示旧配音时长。
        with patch('ui.pages.reviewer_page.ScriptStatisticsDialog') as dialog, \
             patch.object(page, 'refresh_notes_duration_list') as read_audio:
            page.show_info_dialog()
        read_audio.assert_not_called()
        self.assertIsNone(dialog.call_args.args[0].duration)
        self.assertIs(dialog.call_args.args[1], self.root)
        self.assertFalse(page.is_import)

    def test_ready_statistics_does_not_reset_playback_position_or_audio(self):
        page = self.make_page()
        page.is_import = True
        page.current_index = 2
        page.notes_duration_list = [1.5, 2.5, 3]
        page.check_import()
        with patch('ui.pages.reviewer_page.ScriptStatisticsDialog') as dialog, \
             patch.object(page, 'refresh_notes_duration_list') as read_audio, \
             patch.object(page, 'stop_audio') as stop:
            page.show_info_dialog()
        self.assertEqual(dialog.call_args.args[0].duration, 7)
        self.assertEqual(page.current_index, 2)
        self.assertTrue(page.is_import)
        self.assertTrue(page.pageJumpSpinBox.isEnabled())
        stop.assert_not_called()
        read_audio.assert_not_called()

    def test_large_table_is_scrollable_and_fits_small_window(self):
        result = build_statistics({page: '讲稿' for page in range(1, 201)},
                                  [{'page': page} for page in range(1, 201)], [20] * 200)
        dialog = ScriptStatisticsDialog(result, self.root)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.table.rowCount(), 200)
        self.assertGreater(dialog.table.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(dialog.widget.height(), self.root.height() - 20)
        self.assertLessEqual(dialog.widget.width(), self.root.width() - 40)
        self.assertTrue(dialog.widget.rect().contains(dialog.yesButton.mapTo(dialog.widget,
                                                                          dialog.yesButton.rect().center())))
        dialog.hide()

    def test_long_duration_with_units_wraps_without_clipping_in_small_window(self):
        result = build_statistics({page: '讲稿' for page in range(1, 201)},
                                  [{'page': page} for page in range(1, 201)], [34000.29] * 200)
        dialog = ScriptStatisticsDialog(result, self.root)
        dialog.show()
        self.qt.processEvents()
        duration = dialog.summary_values['配音总时长']
        self.assertIn('小时', duration.text())
        self.assertGreaterEqual(duration.height(), duration.heightForWidth(duration.width()))
        self.assertLessEqual(dialog.widget.height(), self.root.height() - 20)
        text = dialog.table.item(0, 3).text()
        self.assertLessEqual(dialog.table.fontMetrics().horizontalAdvance(text),
                             dialog.table.columnWidth(3) - 28)
        dialog.hide()


if __name__ == '__main__':
    unittest.main()
