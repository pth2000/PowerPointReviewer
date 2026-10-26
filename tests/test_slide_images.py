"""共享 PPT 页面读取的副本隔离与演示软件选择验证。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.presentation.images import SlideRenderCancelled, export_slide_images


class SlideImageIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / '原文稿.pptx'
        self.source.write_bytes(b'original PPT content')
        self.presentation = Mock()
        self.presentation.Slides.Count = 3
        self.presentation.PageSetup.SlideWidth = 720
        self.presentation.PageSetup.SlideHeight = 540
        self.application = Mock(AutomationSecurity=2)
        self.application.Presentations.Open.return_value = self.presentation
        self.application.Presentations.Count = 0
        self.calls = []
        def slide(page):
            def export(path, format_name, width, height):
                self.calls.append((page, format_name, width, height))
                image = QImage(width, height, QImage.Format.Format_RGB32)
                image.fill(0xff4477cc)
                image.save(path)
            return NS(Export=export)
        self.presentation.Slides.Item.side_effect = slide
        self.patches = [patch('win32com.client.GetActiveObject', side_effect=OSError('No running Office')),
                        patch('win32com.client.DispatchEx', return_value=self.application),
                        patch('win32com.client.dynamic.Dispatch', side_effect=lambda value: value),
                        patch('win32process.EnumProcesses', return_value=[123]),
                        patch('app.presentation.images.application_pid', return_value=123)]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.directory.cleanup()

    def test_reads_copy_exports_only_used_pages_and_keeps_existing_application(self):
        images = export_slide_images(self.source, [1, 1, 3], self.root / 'work', 1920, 1080)
        self.assertEqual(set(images), {1, 3})
        self.assertEqual(self.calls, [(1, 'PNG', 1440, 1080), (3, 'PNG', 1440, 1080)])
        opened = self.application.Presentations.Open.call_args.args
        self.assertNotEqual(Path(opened[0]), self.source)
        self.assertEqual(opened[1:], (-1, 0, 0))
        self.assertEqual(self.source.read_bytes(), b'original PPT content')
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()
        self.presentation.Save.assert_not_called()
        self.presentation.SaveAs.assert_not_called()
        self.assertEqual(self.application.AutomationSecurity, 2)

    def test_out_of_range_page_closes_copy_without_exporting_or_saving(self):
        with self.assertRaisesRegex(ValueError, '只有 3 页'):
            export_slide_images(self.source, [4], self.root / 'work', 1280, 720)
        self.assertFalse(self.calls)
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()

    def test_cancel_during_export_closes_copy_and_preserves_original(self):
        stopped = [False]
        with self.assertRaises(SlideRenderCancelled):
            export_slide_images(self.source, [1, 3], self.root / 'work', 1280, 720,
                                progress=lambda index, total: stopped.__setitem__(0, True),
                                cancelled=lambda: stopped[0])
        self.assertEqual(len(self.calls), 1)
        self.presentation.Close.assert_called_once()
        self.application.Quit.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'original PPT content')

    def test_only_new_empty_application_is_quit(self):
        with patch('app.presentation.images.application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Quit.assert_called_once()

    def test_running_application_is_reused_and_never_quit(self):
        with patch('win32com.client.GetActiveObject', return_value=self.application), \
             patch('win32com.client.DispatchEx') as start_application, \
             patch('app.presentation.images.application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        start_application.assert_not_called()
        self.application.Quit.assert_not_called()
        self.presentation.Close.assert_called_once()
        self.presentation.Save.assert_not_called()
        self.assertEqual(self.application.AutomationSecurity, 2)

    def test_running_wps_is_used_before_starting_powerpoint(self):
        def running(prog_id):
            if prog_id == 'Kwpp.Application':
                return self.application
            raise OSError('PowerPoint is not running')
        with patch('win32com.client.GetActiveObject', side_effect=running), \
             patch('win32com.client.DispatchEx') as start_application:
            images = export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        start_application.assert_not_called()
        self.application.Quit.assert_not_called()
        self.assertEqual(images[1].parent.name, 'Kwpp.Application')

    def test_explicit_wps_preference_starts_wps_before_a_running_powerpoint(self):
        running = Mock()
        def active(prog_id):
            if prog_id == 'PowerPoint.Application':
                return running
            raise OSError('not running')
        with patch('win32com.client.GetActiveObject', side_effect=active), \
                patch('win32com.client.DispatchEx', return_value=self.application) as launch:
            images = export_slide_images(self.source, [1], self.root / 'work', 1280, 720, preference='wps')
        launch.assert_called_once_with('Kwpp.Application')
        self.assertEqual(images[1].parent.name, 'Kwpp.Application')
        running.Presentations.Open.assert_not_called()

    def test_new_application_with_another_document_is_kept(self):
        self.application.Presentations.Count = 1
        with patch('app.presentation.images.application_pid', return_value=456):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Quit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
