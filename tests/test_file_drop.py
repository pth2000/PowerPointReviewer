"""验证文件拖放路由、输入框保护和原有讲稿/工程包导入流程。"""

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QWidget

from app import exporters, paths, presentation_link, project_package, script_io
from app.file_drop import FileDropImport
from app.playback import PlaybackBus
from app.settings_store import AppSettings
from app.window import Window
from test_presentation_link import make_ppt
from ui.pages.reviewer_page import PPTReviewer


class FileDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = QWidget()
        self.edit = QLineEdit(self.root)
        self.edit.setText('原有输入')
        self.received = Mock()
        self.filter = FileDropImport(self.root, self.received)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def drop(self, target, urls, actions=Qt.DropAction.CopyAction | Qt.DropAction.MoveAction):
        mime = QMimeData()
        mime.setUrls(urls)
        enter = QDragEnterEvent(QPoint(10, 10), actions, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, enter)
        event = QDropEvent(QPointF(10, 10), actions, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, event)
        return enter, event

    def test_child_input_imports_unicode_path_as_copy_after_drop_finishes(self):
        source = 'D:/课程资料/讲稿 空格.PPTX'
        enter, event = self.drop(self.edit, [QUrl.fromLocalFile(source)])
        self.assertTrue(enter.isAccepted())
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.assertEqual(self.edit.text(), '原有输入')
        self.received.assert_not_called()
        self.qt.processEvents()
        self.received.assert_called_once_with([source])

    def test_multiple_files_are_passed_together_for_validation(self):
        files = ['D:/first.pptx', 'D:/second.docx']
        self.drop(self.root, [QUrl.fromLocalFile(name) for name in files])
        self.qt.processEvents()
        self.received.assert_called_once_with(files)

    def test_other_windows_and_dialogs_do_not_trigger_main_import(self):
        other = QWidget()
        other.setAcceptDrops(True)
        dialog = QDialog(self.root)
        dialog.setAcceptDrops(True)
        self.addCleanup(other.deleteLater)
        self.addCleanup(dialog.deleteLater)
        for target in (other, dialog):
            self.drop(target, [QUrl.fromLocalFile('D:/sample.pptx')])
        self.qt.processEvents()
        self.received.assert_not_called()

    def test_remote_urls_and_move_only_drags_are_not_imported(self):
        self.drop(self.root, [QUrl('https://example.invalid/lesson.pptx')])
        enter, event = self.drop(self.root, [QUrl.fromLocalFile('D:/sample.pptx')],
                                 Qt.DropAction.MoveAction)
        self.assertFalse(enter.isAccepted())
        self.assertFalse(event.isAccepted())
        self.qt.processEvents()
        self.received.assert_not_called()

    def test_main_handler_returns_to_home_and_rejects_multiple_files(self):
        host = NS(ppt_r=Mock(), switchTo=Mock())
        Window._import_dropped_files(host, ['D:/lesson.pptx'])
        host.switchTo.assert_called_once_with(host.ppt_r)
        host.ppt_r.import_file.assert_called_once_with('D:/lesson.pptx')
        host.ppt_r.reset_mock()
        Window._import_dropped_files(host, ['D:/one.pptx', 'D:/two.pptx'])
        host.ppt_r.import_file.assert_not_called()
        host.ppt_r.create_warning_info_bar.assert_called_once()

    def enter(self, files, target=None):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(name) for name in files])
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target if target is not None else self.root, event)

    def test_hover_shows_hint_without_importing_and_drop_hides_it(self):
        self.enter(['D:/课程/讲稿.pptx'])
        self.assertFalse(self.filter.overlay.isHidden())
        self.assertEqual(self.filter.overlay.state, 'ready')
        self.assertEqual(self.filter.overlay.filename, '讲稿.pptx')
        self.received.assert_not_called()
        self.drop(self.root, [QUrl.fromLocalFile('D:/课程/讲稿.pptx')])
        self.assertTrue(self.filter.overlay.isHidden())
        self.qt.processEvents()
        self.received.assert_called_once()

    def test_embedded_modal_color_dialog_blocks_drop_without_replacing_input(self):
        from PySide6.QtGui import QColor
        from app import theme
        self.root.resize(850, 750)
        self.root.show()
        color = theme.create_color_dialog(QColor('#B7472A'), self.root)
        color.setModal(True)
        color.show()
        self.qt.processEvents()
        self.assertIsNone(self.qt.activeModalWidget())
        self.drop(self.edit, [QUrl.fromLocalFile('D:/lesson.pptx')])
        self.qt.processEvents()
        self.received.assert_not_called()
        self.assertTrue(self.filter.overlay.isHidden())
        self.assertEqual(self.edit.text(), '原有输入')
        color.hide()
        color.setModal(False)

    def test_leave_hides_overlay_and_child_transition_preserves_it(self):
        self.enter(['D:/lesson.pptx'])
        QApplication.sendEvent(self.root, QDragLeaveEvent())
        self.enter(['D:/lesson.pptx'], self.edit)
        self.qt.processEvents()
        self.assertFalse(self.filter.overlay.isHidden())
        QApplication.sendEvent(self.edit, QDragLeaveEvent())
        self.qt.processEvents()
        self.assertTrue(self.filter.overlay.isHidden())
        self.received.assert_not_called()

    def test_hover_distinguishes_multiple_unsupported_and_busy(self):
        self.enter(['D:/first.pptx', 'D:/second.pptx'])
        self.assertEqual(self.filter.overlay.state, 'multiple')
        self.enter(['D:/first.pdf'])
        self.assertEqual(self.filter.overlay.state, 'unsupported')
        self.filter.busy_check = lambda: True
        self.enter(['D:/first.pptx'])
        self.assertEqual(self.filter.overlay.state, 'busy')
        self.received.assert_not_called()

    def test_linked_word_and_json_hover_describes_updating(self):
        self.filter.linked_check = lambda: True
        for suffix in ('docx', 'json'):
            self.enter([f'D:/lecture.{suffix}'])
            self.assertIn('更新讲稿', self.filter.overlay.title)
        self.enter(['D:/lecture.pptx'])
        self.assertEqual(self.filter.overlay.title, '松开鼠标即可导入')

    def test_overlay_does_not_intercept_pointer_and_fits_content(self):
        from qfluentwidgets import FluentIcon, FluentWindow
        root = FluentWindow()
        page = QWidget(root)
        page.setObjectName('drop-test-page')
        root.addSubInterface(page, FluentIcon.HOME, '主页')
        drop = FileDropImport(root, Mock(), overlay_parent=root.stackedWidget)
        self.addCleanup(root.deleteLater)
        root.resize(760, 560)
        root.show()
        self.qt.processEvents()
        drop.overlay.show_files(['D:/lesson.pptx'])
        self.assertEqual(drop.overlay.geometry(), root.stackedWidget.rect())
        self.assertTrue(drop.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        root.resize(900, 800)
        self.qt.processEvents()
        self.assertEqual(drop.overlay.geometry(), root.stackedWidget.rect())
        # 实际绘制完成且主窗口销毁后，过滤器不会访问已销毁的内容区。
        self.assertFalse(root.grab().isNull())
        root.hide()
        self.assertTrue(drop.overlay.isHidden())


class FileImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        context = NS(app_settings=AppSettings(), config=Mock(), playback_bus=PlaybackBus(),
                     slideshow_watcher=Mock(), hotkeys=Mock(), tts_engine=Mock())
        context.tts_engine.snapshot.return_value = context.tts_engine
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(context)
        self.addCleanup(self.dispose)
        for name, target in [('AUDIO_CACHE_DIR', 'audio'), ('SESSION_DIR', 'sessions')]:
            destination = self.root / target
            destination.mkdir()
            patcher = patch.object(paths, name, destination)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.page.session_root_path = paths.SESSION_DIR
        self.page.notes = {1: '原有讲稿'}
        for method in ('create_warning_info_bar', 'create_error_info_bar',
                       'create_success_info_bar', 'init_general_play', '_bind_presentation_monitor'):
            patcher = patch.object(self.page, method)
            patcher.start()
            self.addCleanup(patcher.stop)

    def dispose(self):
        self.page.presentation_monitor.shutdown()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def test_pptx_import_loads_notes_and_prepares_audio_without_picker(self):
        self.page.notes = {}
        source = make_ppt(self.root / '讲稿 空格.PPTX')
        original = source.read_bytes()
        with patch('ui.pages.reviewer_page.QFileDialog.getOpenFileName') as picker:
            self.assertTrue(self.page.import_file(str(source)))
        picker.assert_not_called()
        self.assertEqual(self.page.notes, script_io.load_script(source).notes)
        self.assertEqual(self.page.presentation_path, source)
        self.page.init_general_play.assert_called_once()
        self.assertEqual(source.read_bytes(), original)

    def test_json_and_word_use_existing_script_loader(self):
        source = self.root / '讲稿.json'
        source.write_text(json.dumps({'format': script_io.JSON_FORMAT, 'mark': '|',
                                     'pages': [{'page': 2, 'segments': ['第一段', '第二段']}]}),
                          encoding='utf-8')
        self.assertTrue(self.page.import_file(source))
        self.assertEqual(self.page.notes, {2: '第一段|第二段'})
        self.assertEqual(self.page.mark, '|')
        self.assertIsNone(self.page.presentation_path)
        word = self.root / '讲稿.DOCX'
        word.write_bytes(b'word fixture')
        data = script_io.ScriptData(notes={3: 'Word 讲稿'})
        with patch.object(script_io, 'load_script', return_value=data) as loader:
            self.assertTrue(self.page.import_file(word))
        loader.assert_called_once_with(word)
        self.assertEqual(self.page.notes, {3: 'Word 讲稿'})

    def test_package_restores_audio_without_regenerating(self):
        audio = self.root / 'source.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(bytes(4800))
        package = self.root / '课程.PPRPKG'
        project_package.export_package(
            package, items=[{'page': 1, 'index': 0, 'text': '配音', 'duration': 0.1,
                             'cache_key': 'sample', 'cache_ext': 'wav', 'media': str(audio)}],
            notes={1: '配音'}, mark='●', source_name='课程', generation_profile={}, speaker='test')
        with patch.object(paths, 'ensure_runtime_dirs'):
            self.assertTrue(self.page.import_file(package))
        self.assertEqual(self.page.notes, {1: '配音'})
        self.assertTrue(self.page.is_import)
        self.assertEqual(self.page.media_list, [paths.AUDIO_CACHE_DIR / 'sample.wav'])
        self.page.init_general_play.assert_not_called()

    def test_busy_invalid_missing_and_directory_imports_preserve_current_notes(self):
        source = self.root / 'sample.pptx'
        source.write_bytes(b'unused while busy')
        with patch.object(self.page.save_thread, 'isRunning', return_value=True), \
                patch.object(script_io, 'load_script') as loader:
            self.assertFalse(self.page.import_file(source))
        loader.assert_not_called()
        with patch.object(self.page.playback_show, 'preparing', True):
            self.assertFalse(self.page.import_file(source))
        folder = self.root / 'folder.pptx'
        folder.mkdir()
        for invalid in (self.root / 'notes.pdf', self.root / 'missing.pptx', folder):
            self.assertFalse(self.page.import_file(invalid))
        self.assertEqual(self.page.notes, {1: '原有讲稿'})
        self.page.init_general_play.assert_not_called()

    def test_parse_failure_preserves_current_notes_and_import_button(self):
        source = self.root / 'broken.pptx'
        source.write_bytes(b'broken')
        self.assertFalse(self.page.import_file(source))
        self.assertEqual(self.page.notes, {1: '原有讲稿'})
        self.assertTrue(self.page.getFileButton.isEnabled())
        self.page.create_error_info_bar.assert_called_once()
        self.page.init_general_play.assert_not_called()

    def test_picker_uses_the_same_file_import_entry(self):
        source = self.root / 'lecture.pptx'
        with patch('ui.pages.reviewer_page.QFileDialog.getOpenFileName',
                   return_value=(str(source), '')), patch.object(self.page, 'import_file') as importer:
            self.page.import_pptx()
        importer.assert_called_once_with(str(source))

    def link_current_ppt(self):
        source = make_ppt(self.root / '原 PPT.pptx', count=3)
        self.page.note_file_path = self.page.presentation_path = source
        self.page.note_file_name = '原 PPT'
        self.page.presentation_link = presentation_link.describe(source)
        self.page.notes = {1: '第一段●第二段', 2: '原第二页', 3: '第三页保留'}
        self.page.presentation_monitor.baseline = script_io.load_script(source).notes
        self.page.notes_path_text.set_text(str(source))
        return source

    def script_json(self, notes, mark='●'):
        target = self.root / '修改后的讲稿.json'
        target.write_text(json.dumps({'format': script_io.JSON_FORMAT, 'mark': mark,
                                     'pages': [{'page': page, 'text': text}
                                               for page, text in notes.items()]}), encoding='utf-8')
        return target

    def test_word_updates_linked_ppt_by_page_and_keeps_omitted_pages(self):
        source = self.link_current_ppt()
        original = source.read_bytes()
        baseline = dict(self.page.presentation_monitor.baseline)
        link = dict(self.page.presentation_link)
        word = self.root / '外部编辑.docx'
        exporters.write_docx(word, [{'page': 2, 'text': '修改后的第二页'}], [1])
        self.assertTrue(self.page.import_file(word))
        self.assertEqual(self.page.notes, {1: '第一段●第二段', 2: '修改后的第二页', 3: '第三页保留'})
        self.assertEqual(self.page.presentation_link, link)
        self.assertEqual(self.page.note_file_path, source)
        self.assertEqual(self.page.note_file_name, '原 PPT')
        self.assertEqual(self.page.notes_path_text.text, str(source))
        self.assertEqual(self.page.presentation_monitor.baseline, baseline)
        self.page._bind_presentation_monitor.assert_not_called()
        self.assertEqual(source.read_bytes(), original)
        self.page.init_general_play.assert_called_once()

    def test_json_updates_keep_current_separator_settings_and_history(self):
        source = self.link_current_ppt()
        old_record = paths.SESSION_DIR / 'original.json'
        old_record.write_text('{"original": true}', encoding='utf-8')
        self.page.session_record_path = old_record
        before = self.page.ctx.app_settings.to_dict()
        with patch.object(self.page, 'persist_preferences') as persist:
            self.assertTrue(self.page.import_file(self.script_json({2: '新第一段|新第二段'}, '|')))
        self.assertEqual(self.page.mark, '●')
        self.assertEqual(self.page.notes[2], '新第一段●新第二段')
        self.assertEqual(self.page.notes[1], '第一段●第二段')
        self.assertEqual(self.page.presentation_path, source)
        self.assertEqual(self.page.ctx.app_settings.to_dict(), before)
        self.assertEqual(old_record.read_text(encoding='utf-8'), '{"original": true}')
        self.assertIsNone(self.page.session_record_path)
        persist.assert_not_called()

    def test_unchanged_json_does_not_regenerate_or_clear_record(self):
        self.link_current_ppt()
        self.page.is_import = True
        self.page.session_record_path = self.root / 'original.json'
        self.assertTrue(self.page.import_file(self.script_json({1: '第一段|第二段'}, '|')))
        self.page.init_general_play.assert_not_called()
        self.assertEqual(self.page.session_record_path, self.root / 'original.json')

    def test_unchanged_import_retries_when_audio_is_not_ready(self):
        self.link_current_ppt()
        self.page.is_import = False
        self.assertTrue(self.page.import_file(self.script_json({1: '第一段|第二段'}, '|')))
        self.page.init_general_play.assert_called_once()
        self.assertEqual(self.page.create_success_info_bar.call_args.args[0], '正在准备配音')

    def test_update_does_not_report_success_when_generation_cannot_start(self):
        self.link_current_ppt()
        self.page.init_general_play.return_value = False
        self.assertFalse(self.page.import_file(self.script_json({2: '新的第二页'})))
        self.page.create_success_info_bar.assert_not_called()

    def test_invalid_page_updates_leave_link_notes_baseline_and_record_unchanged(self):
        source = self.link_current_ppt()
        notes = dict(self.page.notes)
        baseline = dict(self.page.presentation_monitor.baseline)
        self.page.session_record_path = self.root / 'original.json'
        for page in (0, -1, 4):
            with self.subTest(page=page):
                self.assertFalse(self.page.import_file(self.script_json({page: '错页讲稿'})))
                self.assertEqual(self.page.notes, notes)
                self.assertEqual(self.page.presentation_monitor.baseline, baseline)
                self.assertEqual(self.page.presentation_path, source)
                self.assertEqual(self.page.session_record_path, self.root / 'original.json')
                self.assertTrue(self.page.getFileButton.isEnabled())
        self.page.init_general_play.assert_not_called()

    def test_missing_associated_ppt_allows_standalone_import_in_restricted_mode(self):
        source = self.link_current_ppt()
        source.unlink()
        self.assertTrue(self.page.import_file(self.script_json({1: '独立讲稿'})))
        self.assertEqual(self.page.notes, {1: '独立讲稿'})
        self.assertTrue(self.page.is_restricted())
        self.assertEqual(self.page.presentation_link, {})

    def test_ppt_link_choice_preserves_audio_and_saves_real_notes_baseline(self):
        source = make_ppt(self.root / '待关联.pptx')
        record = paths.SESSION_DIR / 'standalone.json'
        existing = {'notes': {'1': '原有讲稿'}, 'generation_profile': {'voice': 'original'},
                    'items': [{'page': 1, 'text': '原有讲稿', 'cache_key': 'original'}]}
        record.write_text(json.dumps(existing), encoding='utf-8')
        self.page.session_record_path = record
        self.page.note_file_path = self.root / '原讲稿.docx'
        self.page.media_list = [paths.AUDIO_CACHE_DIR / 'original.wav']
        self.page.notes_list = [{'page': 1, 'text': '原有讲稿'}]
        self.page.is_import = True
        with patch.object(self.page, '_choose_ppt_import', return_value='link'):
            self.assertTrue(self.page.import_file(source))
        self.assertEqual(self.page.notes, {1: '原有讲稿'})
        self.assertEqual(self.page.media_list, [paths.AUDIO_CACHE_DIR / 'original.wav'])
        self.assertEqual(self.page.note_file_path, self.root / '原讲稿.docx')
        self.assertEqual(self.page.presentation_path, source.resolve())
        self.assertFalse(self.page.is_restricted())
        self.page.init_general_play.assert_not_called()
        baseline = script_io.load_script(source).notes
        self.page._bind_presentation_monitor.assert_called_once_with(baseline=baseline)
        updated_record = json.loads(record.read_text(encoding='utf-8'))
        self.assertEqual(updated_record['notes'], existing['notes'])
        self.assertEqual(updated_record['items'], existing['items'])
        self.assertEqual(updated_record['generation_profile'], existing['generation_profile'])
        self.assertEqual(updated_record['presentation_notes'], {str(k): v for k, v in baseline.items()})

    def test_ppt_replace_choice_loads_notes_and_regenerates(self):
        source = make_ppt(self.root / '新 PPT.pptx')
        with patch.object(self.page, '_choose_ppt_import', return_value='replace'):
            self.assertTrue(self.page.import_file(source))
        self.assertEqual(self.page.notes, script_io.load_script(source).notes)
        self.assertEqual(self.page.note_file_path, source)
        self.page.init_general_play.assert_called_once()

    def test_ppt_choice_cancel_and_incompatible_link_preserve_independent_script(self):
        source = make_ppt(self.root / '待关联.pptx')
        self.page.notes = {3: '第三页讲稿'}
        for choice in (None, 'link'):
            with patch.object(self.page, '_choose_ppt_import', return_value=choice):
                self.assertFalse(self.page.import_file(source))
            self.assertEqual(self.page.notes, {3: '第三页讲稿'})
            self.assertIsNone(self.page.presentation_path)
            self.assertTrue(self.page.getFileButton.isEnabled())
        self.page.init_general_play.assert_not_called()

    def test_roundtrip_generation_reuses_audio_for_unchanged_segments(self):
        self.link_current_ppt()
        engine = self.page.ctx.tts_engine
        engine.get_generation_profile.return_value = {'voice': 'same'}
        engine.get_output_extension.return_value = 'wav'
        engine.can_parallel_generate.return_value = False
        engine.build_audio_cache_key.side_effect = lambda text, profile: hashlib.sha256(text.encode()).hexdigest()
        from tasks.audio_generation_task import AudioGenerationTask
        engine.save_file.side_effect = lambda text, path: AudioGenerationTask._write_silence(Path(path), 'wav')
        task = self.page.save_thread
        task.audio_cache_path = paths.AUDIO_CACHE_DIR
        task.output_path = self.root / 'output'
        task.output_path.mkdir()
        self.page.mark_split()
        task.configure(self.page.notes_list, 0)
        first = task.save_wav()
        self.assertEqual(first.cache_hit_count, 0)
        engine.save_file.reset_mock()
        self.assertTrue(self.page.import_file(self.script_json({2: '只修改这一段'})))
        self.page.mark_split()
        task.configure(self.page.notes_list, 0)
        second = task.save_wav()
        self.assertEqual(second.cache_hit_count, 3)
        self.assertEqual(engine.save_file.call_count, 1)
        self.assertEqual(engine.save_file.call_args.args[0], '只修改这一段')

    def test_ppt_import_dialog_has_two_exclusive_choices_and_cancel(self):
        from qfluentwidgets import FluentIcon, FluentWindow
        from ui.dialogs.ppt_import_dialog import PptImportDialog
        root = FluentWindow()
        root.addSubInterface(self.page, FluentIcon.HOME, '主页')
        root.resize(760, 560)
        root.show()
        self.addCleanup(root.deleteLater)
        dialog = PptImportDialog(self.page)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selected_action(), 'link')
        dialog.replace_radio.setChecked(True)
        self.assertFalse(dialog.link_radio.isChecked())
        self.assertEqual(dialog.selected_action(), 'replace')
        self.assertEqual(dialog.cancelButton.text(), '取消')
        dialog.show()
        self.qt.processEvents()
        self.assertLessEqual(dialog.widget.width(), 520)
        self.assertLessEqual(dialog.widget.height(), root.height())


if __name__ == '__main__':
    unittest.main()
