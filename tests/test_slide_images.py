"""共享 PPT 页面读取的副本隔离与演示软件选择验证。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.presentation.images import (
    SlideRenderCancelled,
    SlideSourceChanged,
    _com_apartment,
    export_slide_images,
    open_slide_images,
)


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
                        patch('app.presentation.com._rot_applications', return_value=()),
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

    def test_powerpoint_opens_untitled_without_disk_copy_and_keeps_existing_application(self):
        images = export_slide_images(self.source, [1, 1, 3], self.root / 'work', 1920, 1080)
        self.assertEqual(set(images), {1, 3})
        self.assertEqual(self.calls, [(1, 'PNG', 1440, 1080), (3, 'PNG', 1440, 1080)])
        opened = self.application.Presentations.Open.call_args.args
        self.assertEqual(Path(opened[0]), self.source)
        self.assertEqual(opened[1:], (-1, -1, 0))
        self.assertFalse((self.root / 'work' / 'source.pptx').exists())
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
        opened = self.application.Presentations.Open.call_args.args
        self.assertNotEqual(Path(opened[0]), self.source)
        self.assertEqual(opened[1:], (-1, 0, 0))
        self.assertEqual(Path(opened[0]).read_bytes(), self.source.read_bytes())

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

    def test_open_session_reuses_document_and_exported_pages(self):
        with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
            first = reader.export(1)
            reader.export(2)
            self.assertEqual(reader.export(1), first)
            self.assertEqual(reader.page_count, 3)
            self.assertEqual(self.application.AutomationSecurity, 2)
            self.presentation.Close.assert_not_called()
        self.application.Presentations.Open.assert_called_once()
        self.assertEqual([page for page, *_ in self.calls], [1, 2])
        self.presentation.Close.assert_called_once()
        self.presentation.Save.assert_not_called()

    def test_error_inside_session_closes_document_without_reopening_it(self):
        with self.assertRaisesRegex(RuntimeError, 'consumer error'):
            with open_slide_images(self.source, self.root / 'work', 1280, 720):
                raise RuntimeError('consumer error')
        self.application.Presentations.Open.assert_called_once()
        self.presentation.Close.assert_called_once()

    def saved_document(self, *, saved=-1, source=None):
        document = Mock(FullName=str(source or self.source), Saved=saved)
        document.Slides = self.presentation.Slides
        document.PageSetup = self.presentation.PageSetup
        self.application.Presentations.Count = 1
        self.application.Presentations.Item.return_value = document
        return document

    def test_clean_open_document_is_borrowed_without_opening_or_closing(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                self.assertIs(reader.presentation, document)
                reader.export(1)
        self.application.Presentations.Open.assert_not_called()
        self.application.Quit.assert_not_called()
        document.Close.assert_not_called()
        document.Save.assert_not_called()
        self.assertEqual(document.Saved, -1)
        self.assertEqual(self.application.AutomationSecurity, 2)

    def test_dirty_open_document_uses_independent_saved_content(self):
        document = self.saved_document(saved=0)
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Presentations.Open.assert_called_once()
        self.presentation.Close.assert_called_once()
        document.Close.assert_not_called()
        document.Save.assert_not_called()
        self.assertEqual(document.Saved, 0)

    def test_same_filename_in_other_folder_is_not_borrowed(self):
        document = self.saved_document(source=self.root / 'other' / self.source.name)
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Presentations.Open.assert_called_once()
        document.Close.assert_not_called()

    def test_borrowed_source_is_rechecked_before_and_after_export(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                reader.export(1)
                document.Saved = 0
                with self.assertRaises(SlideSourceChanged):
                    reader.export(2)
        self.assertEqual([page for page, *_ in self.calls], [1])
        document.Close.assert_not_called()

    def test_unsaved_edit_during_export_discards_result(self):
        document = self.saved_document()
        original = self.presentation.Slides.Item.side_effect

        def slide(page):
            exporter = original(page).Export

            def export(*args):
                exporter(*args)
                document.Saved = 0

            return NS(Export=export)

        self.presentation.Slides.Item.side_effect = slide
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                with self.assertRaises(SlideSourceChanged):
                    reader.export(1)
        document.Close.assert_not_called()

    def test_save_as_during_session_requires_new_source(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                document.FullName = str(self.root / 'renamed.pptx')
                with self.assertRaises(SlideSourceChanged):
                    reader.export(1)
        document.Close.assert_not_called()

    def test_clean_wps_document_can_also_be_borrowed(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('Kwpp.Application', 'WPS 演示', self.application)]):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Presentations.Open.assert_not_called()
        self.assertFalse((self.root / 'work' / 'source.pptx').exists())
        document.Close.assert_not_called()

    def test_matching_document_in_another_instance_is_preferred(self):
        document = self.saved_document()
        unrelated = Mock()
        unrelated.Presentations.Count = 0
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', unrelated),
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        unrelated.Presentations.Open.assert_not_called()
        self.application.Presentations.Open.assert_not_called()
        document.Close.assert_not_called()

    def test_closed_document_requires_new_source(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                document.FullName = None
                with self.assertRaises(SlideSourceChanged):
                    reader.export(1)
        document.Close.assert_not_called()

    def test_saved_file_change_invalidates_borrowed_session(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]):
            with open_slide_images(self.source, self.root / 'work', 1280, 720) as reader:
                self.source.write_bytes(b'updated presentation')
                with self.assertRaises(SlideSourceChanged):
                    reader.export(1)
        document.Close.assert_not_called()

    def test_document_changed_during_acquisition_falls_back_in_same_application(self):
        document = self.saved_document()
        with patch('app.presentation.images.running_applications', return_value=[
                ('PowerPoint.Application', 'PowerPoint', self.application)]), \
             patch('app.presentation.images.SlideImageSource._check_source',
                   side_effect=[SlideSourceChanged(), None, None]):
            export_slide_images(self.source, [1], self.root / 'work', 1280, 720)
        self.application.Presentations.Open.assert_called_once()
        self.presentation.Close.assert_called_once()
        document.Close.assert_not_called()


class ComApartmentTests(unittest.TestCase):
    def test_nested_initialization_only_releases_its_own_reference(self):
        ole32 = Mock()
        ole32.CoInitializeEx.return_value = 1  # S_FALSE：线程已有 COM 初始化。
        with patch('app.presentation.images.ctypes.WinDLL', return_value=ole32):
            with _com_apartment():
                ole32.CoUninitialize.assert_not_called()
        ole32.CoUninitialize.assert_called_once()

    def test_existing_different_apartment_is_not_uninitialized(self):
        ole32 = Mock()
        ole32.CoInitializeEx.return_value = -2147417850
        with patch('app.presentation.images.ctypes.WinDLL', return_value=ole32):
            with _com_apartment():
                pass
        ole32.CoUninitialize.assert_not_called()

    def test_initialization_failure_is_reported_without_cleanup(self):
        ole32 = Mock()
        ole32.CoInitializeEx.return_value = -2147024882
        with patch('app.presentation.images.ctypes.WinDLL', return_value=ole32):
            with self.assertRaisesRegex(OSError, '无法初始化'):
                with _com_apartment():
                    self.fail('初始化失败后不应继续操作文稿')
        ole32.CoUninitialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
