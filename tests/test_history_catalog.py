"""历史列表异步读取、搜索、关闭和摘要缓存的回归测试。"""

import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer

from app import paths
from app.project import history_catalog
from qt_test_case import QtTestCase
from ui.dialogs.session_history_dialog import SessionHistoryDialog


class HistoryCatalogTests(QtTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.history = self.root / 'history'
        self.history.mkdir()
        cache = self.root / 'audio'
        cache.mkdir()
        patcher = patch.object(paths, 'AUDIO_CACHE_DIR', cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        history_catalog._summary.cache_clear()

    def record(self, name, **values):
        path = self.history / f'{name}.json'
        data = {'source_name': name, 'speaker': '测试音色', 'items': [], **values}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def dialog(self):
        dialog = SessionHistoryDialog(self.history)
        self.addCleanup(dialog.deleteLater)
        self.wait_for_history(dialog)
        return dialog

    def test_search_and_select_all_only_target_visible_records(self):
        self.record('数学课', speaker='声音甲')
        self.record('英语课', speaker='声音乙')
        dialog = self.dialog()
        for query, expected in [('数学', '数学课.json'), ('声音乙', '英语课.json')]:
            dialog.search_edit.setText(query)
            dialog.select_all_records()
            self.assertEqual([p.name for p in dialog.selected_record_paths()], [expected])
            self.assertEqual(dialog.count_label.text(), '1 / 2 条')
        dialog.search_edit.setText('无匹配')
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertFalse(dialog.delete_button.isEnabled())

    def test_reopening_reuses_parsed_summary_but_refresh_sees_record_changes(self):
        source = self.record('课程', speaker='旧音色')
        with patch.object(history_catalog.json, 'load', wraps=json.load) as read:
            dialog = self.dialog()
            first = read.call_count
            dialog.reload_records()
            self.wait_for_history(dialog)
            self.assertEqual(read.call_count, first)
            self.record('课程', speaker='更新后的音色')
            dialog.reload_records(selected_path=source)
            self.wait_for_history(dialog)
            self.assertEqual(dialog.table.item(0, 4).text(), '更新后的音色')
            self.assertGreater(read.call_count, first)

    def test_association_availability_is_rechecked_without_reparsing_json(self):
        source = self.root / '课程.pptx'
        source.write_bytes(b'fixture')
        record = self.record('课程', presentation={'path': str(source)})
        self.assertEqual(history_catalog.read_record_summary(record)['presentation_state'], '已关联')
        source.unlink()
        self.assertEqual(history_catalog.read_record_summary(record)['presentation_state'], '文件缺失')

    def test_refresh_and_cache_statistics_preserve_search_and_selection(self):
        self.record('英语课')
        path = self.record('数学课')
        dialog = self.dialog()
        dialog.search_edit.setText('数学')
        dialog.select_all_records()
        dialog.refresh_cache_label()
        self.wait_for_history(dialog)
        self.assertEqual(dialog.selected_record_paths(), [path])
        dialog.refresh_button.click()
        self.wait_for_history(dialog)
        self.assertEqual(dialog.search_edit.text(), '数学')
        self.assertEqual(dialog.selected_record_paths(), [path])

    def test_corrupt_record_can_be_deleted_but_not_loaded_or_associated(self):
        (self.history / 'invalid.json').write_text('[]', encoding='utf-8')
        dialog = self.dialog()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertFalse(dialog.link_ppt_button.isEnabled())
        self.assertTrue(dialog.delete_button.isEnabled())

    def test_background_scan_keeps_event_loop_responsive_and_can_close_early(self):
        for index in range(200):
            self.record(str(index))
        read = history_catalog.read_record_summary
        def slow_read(*args):
            time.sleep(.005)
            return read(*args)
        with patch('tasks.history_scan_task.read_record_summary', side_effect=slow_read):
            dialog = SessionHistoryDialog(self.history)
            self.addCleanup(dialog.deleteLater)
            timer = QTimer()
            ticks = []
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(5)
            loop = QEventLoop()
            QTimer.singleShot(80, dialog.reject)
            dialog.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            timer.stop()
            self.wait_for_history(dialog)
            self.assertGreater(len(ticks), 3)
            self.assertTrue(dialog._closing)
            self.assertLess(dialog.table.rowCount(), 200)

