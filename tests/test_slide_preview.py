"""页面预览的文稿复用、自动加载、请求合并与资源释放。"""

import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qt_test_case import QtTestCase

from app import paths
from app.presentation.images import SlideRenderCancelled, SlideSourceChanged
from ui.dialogs.slide_preview import SlidePreview


class SlidePreviewTests(QtTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / '讲稿.pptx'
        self.source.write_bytes(b'presentation')
        self.calls = []
        self.opens = []
        self.closed = []
        self.workdirs = []
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.open_entered = Event()
        self.open_release = Event()
        self.open_release.set()
        self.source_changes = 0
        patcher = patch.object(paths, 'PREVIEW_DIR', self.root / 'preview')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('tasks.slide_preview_task.open_slide_images', side_effect=self.open_source)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.preview = SlidePreview()
        self.preview.resize(600, 210)
        self.addCleanup(self.dispose)

    @contextmanager
    def open_source(self, source, directory, width, height, *, preference, cancelled):
        self.opens.append((source, preference))
        folder = Path(directory)
        self.workdirs.append(folder)
        self.open_entered.set()
        try:
            if not self.open_release.wait(5):
                raise TimeoutError('预览打开测试超时')
            if cancelled():
                raise SlideRenderCancelled()

            def export(page):
                if cancelled():
                    raise SlideRenderCancelled()
                if self.source_changes:
                    self.source_changes -= 1
                    raise SlideSourceChanged()
                path = folder / f'{page}.png'
                if not path.is_file():
                    self.calls.append(page)
                    self.entered.set()
                    if not self.release.wait(5):
                        raise TimeoutError('预览任务测试超时')
                    if cancelled():
                        raise SlideRenderCancelled()
                    image = QImage(width, height, QImage.Format.Format_RGB32)
                    image.fill(0xff223300 + page)
                    image.save(str(path))
                return path

            yield SimpleNamespace(export=export)
        finally:
            self.closed.append(folder)

    def dispose(self):
        self.preview.hide()
        self.preview.release()
        self.release.set()
        self.open_release.set()
        QThreadPool.globalInstance().waitForDone(6000)
        self.qt.processEvents()
        self.preview.deleteLater()
        self.qt.processEvents()

    def wait(self, condition):
        deadline = time.monotonic() + 6
        while not condition():
            if time.monotonic() > deadline:
                self.fail('预览状态等待超时')
            QTest.qWait(5)

    def ready(self):
        self.wait(lambda: not self.preview._loading and not self.preview.image.isNull())

    def test_collapsed_preview_does_not_render_and_reuses_document(self):
        self.preview.set_page(self.source, 1, 'wps')
        self.qt.processEvents()
        self.assertFalse(self.opens)
        self.preview.show()
        self.ready()
        self.preview.set_page(self.source, 2, 'wps')
        self.ready()
        self.preview.set_page(self.source, 1, 'wps')
        self.assertEqual(self.opens, [(self.source, 'wps')])
        self.assertEqual(self.calls, [1, 2])
        self.assertEqual(self.preview.image.pixel(0, 0), 0xff223301)
        self.assertTrue(all(path.exists() for path in self.workdirs))
        self.assertEqual(self.source.read_bytes(), b'presentation')
        self.preview.hide()
        self.wait(lambda: self.preview._task is None)
        self.assertEqual(self.closed, self.workdirs)
        self.assertTrue(all(not path.exists() for path in self.workdirs))

    def test_fast_selection_during_render_only_processes_latest_pending_page(self):
        self.release.clear()
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.wait(self.entered.is_set)
        self.preview.set_page(self.source, 2)
        self.preview.set_page(self.source, 3)
        self.release.set()
        self.ready()
        self.assertEqual(self.calls, [1, 3])
        self.assertEqual(len(self.opens), 1)
        self.assertEqual(self.preview.caption, '第 3 页')
        self.assertEqual(self.preview.image.pixel(0, 0), 0xff223303)

    def test_fast_selection_during_open_does_not_reopen_large_document(self):
        self.open_release.clear()
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.wait(self.open_entered.is_set)
        self.preview.set_page(self.source, 2)
        self.preview.set_page(self.source, 3)
        self.qt.processEvents()
        self.assertIn('正在打开', self.preview.message)
        self.assertTrue(self.preview.progress.isVisible())
        self.open_release.set()
        self.ready()
        self.assertEqual(self.calls, [3])
        self.assertEqual(len(self.opens), 1)

    def test_file_changes_reopen_the_document(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        self.source.write_bytes(b'updated presentation')
        self.preview.set_page(self.source, 1)
        self.ready()
        self.assertEqual(len(self.opens), 2)
        self.assertEqual(self.calls, [1, 1])
        self.assertFalse(self.workdirs[0].exists())

    def test_closing_during_export_cancels_and_removes_files(self):
        self.release.clear()
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.wait(self.entered.is_set)
        self.preview.close()
        self.release.set()
        self.wait(lambda: self.preview._task is None)
        self.assertTrue(self.preview.image.isNull())
        self.assertFalse(self.preview._cache)
        self.assertTrue(all(not path.exists() for path in self.workdirs))
        self.preview.show()
        self.ready()
        self.assertEqual(len(self.opens), 2)

    def test_missing_source_and_render_failure_allow_recovery(self):
        self.preview.set_page(self.root / 'missing.pptx', 1)
        self.preview.show()
        self.qt.processEvents()
        self.assertFalse(self.opens)
        self.assertIn('关联 PPT', self.preview.message)
        with patch('tasks.slide_preview_task.open_slide_images', side_effect=RuntimeError('failed')), \
             patch('tasks.slide_preview_task.get_logger'):
            self.preview.set_page(self.source, 1)
            self.wait(lambda: not self.preview._loading)
        self.assertIn('重试', self.preview.message)
        self.assertFalse(list(paths.PREVIEW_DIR.iterdir()))
        self.preview._activate()
        self.ready()

    def test_evicted_images_reuse_temporary_png_without_exporting_again(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        for page in range(2, self.preview.CACHE_SIZE + 3):
            self.preview.set_page(self.source, page)
            self.ready()
        self.assertEqual(len(self.preview._cache), self.preview.CACHE_SIZE)
        self.preview.set_page(self.source, 1)
        self.ready()
        self.assertEqual(len(self.calls), self.preview.CACHE_SIZE + 2)
        self.assertEqual(len(self.opens), 1)

    def test_preview_shown_with_its_parent_loads_without_click(self):
        parent = QWidget()
        layout = QVBoxLayout(parent)
        layout.addWidget(self.preview)
        self.preview.set_page(self.source, 1)
        try:
            parent.show()
            self.ready()
            self.assertEqual(self.calls, [1])
            self.assertFalse(self.preview.progress.isVisible())
        finally:
            self.preview.setParent(None)
            parent.close()
            parent.deleteLater()

    def test_click_while_loading_does_not_queue_another_request(self):
        self.release.clear()
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.wait(self.entered.is_set)
        self.preview._activate()
        self.preview._activate()
        self.release.set()
        self.ready()
        self.assertEqual(self.calls, [1])
        self.assertEqual(len(self.opens), 1)

    def test_shutdown_does_not_restart_a_visible_preview(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        self.preview._shutdown()
        self.wait(lambda: self.preview._task is None)
        self.preview.set_page(self.source, 2)
        self.qt.processEvents()
        self.assertEqual(len(self.opens), 1)
        self.assertTrue(all(not path.exists() for path in self.workdirs))

    def test_tab_switch_can_keep_the_document_until_explicit_release(self):
        self.preview._keep_session_on_hide = True
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        self.preview.hide()
        self.preview.set_page(self.source, 2)
        self.qt.processEvents()
        self.assertEqual(self.calls, [1])
        self.assertFalse(self.closed)
        self.preview.show()
        self.ready()
        self.assertEqual(self.calls, [1, 2])
        self.assertEqual(len(self.opens), 1)
        self.preview.hide()
        self.preview.release()
        self.wait(lambda: self.preview._task is None)
        self.assertTrue(all(not path.exists() for path in self.workdirs))
        self.preview._keep_session_on_hide = False

    def test_saved_file_updates_visible_preview_without_changing_selection(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        self.source.write_bytes(b'updated saved presentation')
        self.wait(lambda: len(self.opens) == 2 and not self.preview._loading)
        self.assertEqual(self.calls, [1, 1])
        self.assertEqual(len(self.preview._cache), 1)

    def test_atomic_save_restores_file_watch_and_updates_again(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        replacement = self.root / 'replacement.pptx'
        replacement.write_bytes(b'new saved presentation')
        replacement.replace(self.source)
        self.wait(lambda: len(self.opens) == 2 and not self.preview._loading)
        self.source.write_bytes(b'another saved presentation')
        self.wait(lambda: len(self.opens) == 3 and not self.preview._loading)
        self.assertEqual(self.calls, [1, 1, 1])

    def test_changes_to_unrelated_files_do_not_reload_preview(self):
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        (self.root / 'unrelated.txt').write_text('changed', encoding='utf-8')
        QTest.qWait(500)
        self.assertEqual(len(self.opens), 1)

    def test_changed_borrowed_source_is_reopened_automatically(self):
        self.source_changes = 1
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.ready()
        self.assertEqual(len(self.opens), 2)
        self.assertEqual(self.calls, [1])
        self.assertFalse(self.workdirs[0].exists())

    def test_continuously_changing_source_stops_retrying(self):
        self.source_changes = 3
        self.preview.set_page(self.source, 1)
        self.preview.show()
        self.wait(lambda: self.preview.message.startswith('文稿正在更新'))
        self.assertFalse(self.preview._loading)
        self.assertEqual(len(self.opens), 2)
