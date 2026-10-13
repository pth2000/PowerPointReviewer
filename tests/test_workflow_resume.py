"""验证 PPT 更新需确认、后台检测及恢复上次讲稿时保持停止。"""

import json
from pathlib import Path
import tempfile
import wave
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from pptx import Presentation
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget

from app import presentation_link, script_io
from app.playback import PlaybackBus
from app.presentation_monitor import PresentationMonitor, changed_pages
from app.settings_store import AppSettings
from test_presentation_link import make_ppt
from ui.pages.reviewer_page import PPTReviewer


class WorkflowResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = make_ppt(self.root / 'lesson.pptx')
        self.baseline = script_io.load_script(self.source).notes
        audio_dir = self.root / 'audio'
        audio_dir.mkdir()
        items = []
        for page, text in self.baseline.items():
            with wave.open(str(audio_dir / f'key{page}.wav'), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(bytes(4800))
            items.append({'page': page, 'text': text.strip(), 'cache_key': f'key{page}',
                          'cache_ext': 'wav', 'duration': 1})
        self.record = {'source_file': str(self.source), 'source_name': '测试讲稿',
                       'presentation': presentation_link.describe(self.source),
                       'presentation_notes': self.baseline, 'notes': self.baseline,
                       'items': items, 'mark': '●', 'generation_profile': {'voice': 'original'}}
        self.record_path = self.root / 'history.json'
        self.write_record()
        self.ctx = NS(app_settings=AppSettings(), config=Mock(), playback_bus=PlaybackBus(),
                      slideshow_watcher=Mock(), hotkeys=Mock(), tts_engine=Mock())
        self.ctx.tts_engine.get_selected_voice_name.return_value = '测试声音'
        self.ctx.tts_engine.snapshot.return_value = self.ctx.tts_engine
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(self.ctx)
        self.page.audio_cache_path = audio_dir
        self.page.session_root_path = self.root
        self.page.presentation_monitor.timer.stop()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.page.presentation_monitor.shutdown()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def write_record(self):
        self.record_path.write_text(json.dumps(self.record), encoding='utf-8')

    def load(self):
        self.page.load_session_record(self.record_path)

    def edit_ppt(self, text='新的第二页备注'):
        ppt = Presentation(str(self.source))
        ppt.slides[1].notes_slide.notes_text_frame.text = text
        ppt.save(str(self.source))

    def check_monitor(self):
        monitor = self.page.presentation_monitor
        monitor.poll()
        if not monitor.task.isRunning():
            self.qt.processEvents()
            return
        loop = QEventLoop()
        monitor.task.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        monitor.task.finished.disconnect(loop.quit)
        self.assertFalse(monitor.task.isRunning())
        self.qt.processEvents()

    def wait_until(self, condition):
        if condition():
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(lambda: loop.quit() if condition() else None)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timer.start()
        timeout.start(4000)
        loop.exec()
        timer.stop()
        timeout.stop()
        self.assertTrue(condition())

    def test_filesystem_events_handle_atomic_save_delete_and_restore_without_polling(self):
        self.load()
        monitor = self.page.presentation_monitor
        self.wait_until(lambda: monitor.stamp is not None and not monitor.task.isRunning()
                        and not monitor.timer.isActive())
        self.assertTrue(monitor.timer.isSingleShot())
        replacement = self.root / 'replacement.pptx'
        replacement.write_bytes(self.source.read_bytes())
        ppt = Presentation(str(replacement))
        ppt.slides[1].notes_slide.notes_text_frame.text = '原子保存后的备注'
        ppt.save(str(replacement))
        replacement.replace(self.source)
        self.wait_until(lambda: self.page._ppt_update_bar is not None)
        payload = self.source.read_bytes()
        self.source.unlink()
        self.wait_until(lambda: self.page.is_restricted() and
                        not self.page.scrollEnableSwitch.isEnabled())
        self.source.write_bytes(payload)
        self.wait_until(lambda: not self.page.is_restricted() and
                        str(self.source) in monitor.watcher.files())
        self.wait_until(lambda: not monitor.task.isRunning() and not monitor.timer.isActive())
        self.assertFalse(self.page.device_watcher._timer.isActive())

    def test_new_dialog_shows_page_summary_without_script_text(self):
        from qfluentwidgets import BodyLabel, FluentWindow, FluentIcon
        from ui.dialogs.ppt_update_dialog import PptUpdateDialog, page_ranges
        root = FluentWindow()
        root.resize(760, 560)
        root.addSubInterface(self.page, FluentIcon.HOME, '主页')
        root.show()
        self.addCleanup(root.deleteLater)
        before = {1: '保持不变', 2: '旧正文不能显示', 3: '要删除的正文'}
        after = {1: '保持不变', 2: '新正文不能显示', 4: '新增正文不能显示'}
        dialog = PptUpdateDialog('example.pptx', before, after, True,
                                 'zh-CN-YunyangNeural', self.page)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.summary_label.text(), '3 页讲稿有变化')
        self.assertEqual([label.text() for label in dialog.page_labels],
                         ['第 2 页', '第 4 页', '第 3 页'])
        labels = '\n'.join(label.text() for label in dialog.findChildren(BodyLabel))
        self.assertNotIn('正文不能显示', labels)
        self.assertNotIn('要删除的正文', labels)
        self.assertLessEqual(dialog.widget.height(), self.page.height())
        self.assertTrue(dialog.widget.rect().contains(
            dialog.yesButton.mapTo(dialog.widget, dialog.yesButton.rect().bottomRight())))
        self.assertEqual(page_ranges([1, 2, 3, 7, 9, 10]), '1–3、7、9–10')
        dialog.reject()

    def test_background_detects_only_note_changes_without_replacing_audio(self):
        self.load()
        old_notes, old_media = dict(self.page.notes), list(self.page.media_list)
        with patch.object(self.page, '_notify_ppt_update'):
            self.check_monitor()
            self.assertIsNone(self.page._ppt_update_bar)
            # 只改标题、图片等画面内容不会提示备注更新。
            ppt = Presentation(str(self.source))
            ppt.core_properties.title = 'new title'
            ppt.save(str(self.source))
            self.check_monitor()
            self.assertIsNone(self.page._ppt_update_bar)
        self.edit_ppt()
        with patch.object(self.page, 'regenerate') as generate:
            self.check_monitor()
            self.assertIsNotNone(self.page._ppt_update_bar)
            generate.assert_not_called()
        self.assertEqual(self.page.notes, old_notes)
        self.assertEqual(self.page.media_list, old_media)
        self.assertEqual(self.page.current_index, 0)
        bar = self.page._ppt_update_bar
        self.check_monitor()
        self.assertIs(self.page._ppt_update_bar, bar)

    def test_cancel_update_preserves_ai_notes_audio_and_record(self):
        self.load()
        self.page.notes[1] = 'AI 改写后的第一段'
        old_record = self.record_path.read_bytes()
        old_notes = dict(self.page.notes)
        self.edit_ppt()
        with patch('ui.dialogs.ppt_update_dialog.PptUpdateDialog') as dialog, \
             patch.object(self.page, 'regenerate') as generate:
            dialog.return_value.exec.return_value = False
            self.page.update_ppt_notes()
            self.assertTrue(dialog.call_args.args[3])
            generate.assert_not_called()
        self.assertEqual(self.page.notes, old_notes)
        self.assertEqual(self.record_path.read_bytes(), old_record)
        self.assertTrue(self.page.is_import)

    def test_confirm_update_uses_saved_ppt_and_preserves_old_history(self):
        self.load()
        original = self.record_path.read_bytes()
        self.edit_ppt()
        with patch('ui.dialogs.ppt_update_dialog.PptUpdateDialog') as dialog, \
             patch.object(self.page, 'regenerate') as generate:
            dialog.return_value.exec.return_value = True
            self.page.update_ppt_notes()
            generate.assert_called_once_with()
        self.assertEqual(self.page.notes[2].strip(), '新的第二页备注')
        self.assertIsNone(self.page.session_record_path)
        self.assertEqual(self.record_path.read_bytes(), original)
        self.assertEqual(self.page.presentation_monitor.baseline, self.page.notes)

    def test_actual_update_generation_reuses_unchanged_audio(self):
        self.load()
        engine = self.ctx.tts_engine
        engine.get_generation_profile.return_value = {'voice': 'original'}
        engine.get_output_extension.return_value = 'wav'
        engine.can_parallel_generate.return_value = False
        engine.build_audio_cache_key.side_effect = lambda text, profile: (
            'key1' if text == self.baseline[1].strip() else
            'key2' if text == self.baseline[2].strip() else 'new-key')
        def synthesize(text, path):
            with wave.open(path, 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(bytes(4800))
        engine.save_file.side_effect = synthesize
        task = self.page.save_thread
        task.audio_cache_path = self.page.audio_cache_path
        task.output_path = self.root / 'temp'
        task.output_path.mkdir()
        self.page.wav_temp_path = task.output_path
        old_audio = (self.page.audio_cache_path / 'key1.wav').read_bytes()
        old_record = self.record_path.read_bytes()
        self.edit_ppt()
        with patch('ui.dialogs.ppt_update_dialog.PptUpdateDialog') as dialog, \
             patch.object(task, 'save_countdown_wav'):
            dialog.return_value.exec.return_value = True
            self.page.update_ppt_notes()
            loop = QEventLoop()
            task.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            task.finished.disconnect(loop.quit)
            self.qt.processEvents()
        self.assertFalse(task.isRunning())
        self.assertTrue(self.page.is_import)
        self.assertEqual(self.page.cache_hit_count, 1)
        self.assertEqual(engine.save_file.call_count, 1)
        self.assertEqual(engine.save_file.call_args.args[0], '新的第二页备注')
        self.assertEqual((self.page.audio_cache_path / 'key1.wav').read_bytes(), old_audio)
        self.assertEqual(self.record_path.read_bytes(), old_record)
        self.assertNotEqual(self.page.session_record_path, self.record_path)
        self.assertEqual(self.ctx.app_settings.get('last_session')['record_path'],
                         str(self.page.session_record_path.resolve()))

    def test_save_while_confirmation_is_open_requires_check_again(self):
        self.load()
        original = dict(self.page.notes)
        self.edit_ppt()
        with patch('ui.dialogs.ppt_update_dialog.PptUpdateDialog') as dialog, \
             patch.object(self.page, 'regenerate') as generate, \
             patch.object(self.page, 'create_error_info_bar') as error:
            def confirm():
                self.edit_ppt('确认期间再次编辑')
                return True
            dialog.return_value.exec.side_effect = confirm
            self.page.update_ppt_notes()
            error.assert_called_once()
            generate.assert_not_called()
        self.assertEqual(self.page.notes, original)

    def test_same_notes_do_not_regenerate_and_read_failure_preserves_state(self):
        self.load()
        with patch.object(self.page, 'regenerate') as generate:
            self.page.update_ppt_notes()
            generate.assert_not_called()
        original = dict(self.page.notes)
        self.source.write_bytes(b'partial save')
        with patch.object(self.page, 'create_error_info_bar') as error:
            self.page.update_ppt_notes()
            error.assert_called_once()
        self.assertEqual(self.page.notes, original)
        self.assertTrue(self.page.is_import)

    def test_changed_ppt_since_last_run_prompts_without_confusing_ai_edits(self):
        self.record['notes'] = {1: 'AI 改写', 2: self.baseline[2]}
        self.write_record()
        self.load()
        self.check_monitor()
        self.assertIsNone(self.page._ppt_update_bar)
        self.edit_ppt()
        self.load()
        self.check_monitor()
        self.assertIsNotNone(self.page._ppt_update_bar)

    def test_new_association_sets_new_baseline_and_ignores_stale_result(self):
        self.load()
        monitor = self.page.presentation_monitor
        old_epoch = monitor.epoch
        other = make_ppt(self.root / 'other.pptx')
        self.page.set_presentation_link(presentation_link.describe(other))
        self.check_monitor()
        self.assertIsNone(self.page._ppt_update_bar)
        monitor._checked((old_epoch, (0, 0), NS(notes={1: 'stale'}), {'sha256': 'stale'}))
        self.assertEqual(monitor.baseline, script_io.load_script(other).notes)
        self.assertIsNone(self.page._ppt_update_bar)
        record = json.loads(self.record_path.read_text(encoding='utf-8'))
        self.assertIsNone(record['presentation_notes'])

    def test_reverted_ppt_dismisses_update_and_busy_check_is_deferred(self):
        self.load()
        self.edit_ppt()
        self.check_monitor()
        self.assertIsNotNone(self.page._ppt_update_bar)
        monitor = self.page.presentation_monitor
        previous_stamp = monitor.stamp
        with patch.object(self.page, 'is_busy', return_value=True):
            monitor._checked((monitor.epoch, (1, 1), NS(notes={1: 'later'}), {'sha256': 'later'}))
        self.assertEqual(monitor.stamp, previous_stamp)
        self.edit_ppt(self.baseline[2].strip())
        self.check_monitor()
        self.assertIsNone(self.page._ppt_update_bar)

    def test_background_read_failure_retries_after_file_is_saved(self):
        self.load()
        original = self.source.read_bytes()
        self.source.write_bytes(b'incomplete ppt')
        self.check_monitor()
        self.assertIsNone(self.page._ppt_update_bar)
        self.assertIsNone(self.page.presentation_monitor.stamp)
        self.source.write_bytes(original)
        self.edit_ppt()
        self.check_monitor()
        self.assertIsNotNone(self.page._ppt_update_bar)

    def test_resume_restores_segment_stopped_without_generating_or_show_control(self):
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path), 'index': 1})
        with patch('ui.pages.reviewer_page.MessageBox') as box, \
             patch.object(self.page.save_thread, 'start') as generate, \
             patch('app.slideshow.goto') as go:
            box.return_value.exec.return_value = True
            self.page.prompt_resume_session()
            self.assertIn('第 2 页', box.call_args.args[1])
            generate.assert_not_called()
            go.assert_not_called()
        self.assertEqual(self.page.current_index, 1)
        self.assertEqual(self.page.player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertEqual(self.ctx.app_settings.get('last_session')['index'], 1)

    def window_with_hidden_home(self):
        from qfluentwidgets import FluentWindow, FluentIcon
        root = FluentWindow()
        root.resize(850, 750)
        root.addSubInterface(self.page, FluentIcon.HOME, '主页')
        other = QWidget(root)
        other.setObjectName('settings-test')
        root.addSubInterface(other, FluentIcon.SETTING, '设置')
        self.addCleanup(root.deleteLater)
        root.show()
        root.stackedWidget.setCurrentWidget(other)
        self.wait_until(lambda: not self.page.isVisible())
        return root

    def test_resume_from_settings_uses_visible_main_window_as_parent(self):
        root = self.window_with_hidden_home()
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path)})
        with patch('ui.pages.reviewer_page.MessageBox') as box:
            box.return_value.exec.return_value = False
            self.page.prompt_resume_session()
        self.assertIs(box.call_args.args[2], root)
        self.assertTrue(root.isVisible())

    def test_resume_waits_for_embedded_color_dialog_and_only_prompts_once(self):
        from PySide6.QtGui import QColor
        from app import theme
        root = self.window_with_hidden_home()
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path)})
        color = theme.create_color_dialog(QColor('#B7472A'), root)
        self.addCleanup(color.deleteLater)
        color.setModal(True)
        color.show()
        self.qt.processEvents()
        # Fluent 遮罩弹窗并非独立窗口，Qt 的 activeModalWidget 检测不到它。
        self.assertFalse(color.isWindow())
        self.assertIsNone(self.qt.activeModalWidget())
        with patch('ui.pages.reviewer_page.MessageBox') as box:
            box.return_value.exec.return_value = False
            self.page.prompt_resume_session()
            self.page.prompt_resume_session()
            box.assert_not_called()
            self.assertTrue(color.isVisible())
            color.reject()
            self.wait_until(lambda: box.call_count == 1)
            self.assertIs(box.call_args.args[2], root)
            self.assertFalse(self.page._resume_waiting_for_dialog)

    def test_deferred_resume_preserves_script_imported_while_waiting(self):
        from PySide6.QtGui import QColor
        from app import theme
        root = self.window_with_hidden_home()
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path)})
        color = theme.create_color_dialog(QColor('#B7472A'), root)
        self.addCleanup(color.deleteLater)
        color.setModal(True)
        color.show()
        self.qt.processEvents()
        with patch('ui.pages.reviewer_page.MessageBox') as box:
            self.page.prompt_resume_session()
            self.page.notes = {1: '当前讲稿'}
            color.reject()
            self.wait_until(lambda: not self.page._resume_waiting_for_dialog)
            self.qt.processEvents()
            box.assert_not_called()
        self.assertEqual(self.page.notes, {1: '当前讲稿'})

    def test_disabled_resume_skips_prompt_and_keeps_saved_session(self):
        state = {'record_path': str(self.record_path), 'index': 1}
        self.ctx.app_settings.set('last_session', state)
        self.ctx.app_settings.set('resume_last_session', False)
        with patch('ui.pages.reviewer_page.MessageBox') as box, \
             patch.object(self.page, 'load_session_record') as load, \
             patch.object(Path, 'read_text') as read:
            self.page.prompt_resume_session()
            box.assert_not_called()
            load.assert_not_called()
            read.assert_not_called()
        self.assertEqual(self.ctx.app_settings.get('last_session'), state)
        self.assertFalse(self.page.notes)

    def test_resume_cancel_missing_record_and_existing_work_do_not_load(self):
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path), 'index': 0})
        with patch('ui.pages.reviewer_page.MessageBox') as box, \
             patch.object(self.page, 'load_session_record') as load:
            box.return_value.exec.return_value = False
            self.page.prompt_resume_session()
            load.assert_not_called()
            box.reset_mock()
            self.page.notes = {1: '用户已经导入的讲稿'}
            self.page.prompt_resume_session()
            box.assert_not_called()
            self.page.notes = {}
            self.record_path.unlink()
            self.page.prompt_resume_session()
            box.assert_not_called()

    def test_resume_clamps_index_and_deleted_ppt_loads_restricted_audio(self):
        self.ctx.app_settings.set('last_session', {'record_path': str(self.record_path), 'index': 99})
        payload = self.source.read_bytes()
        self.source.unlink()
        with patch('ui.pages.reviewer_page.MessageBox') as box, \
             patch('app.slideshow.list_open_presentations', return_value=[]):
            box.return_value.exec.return_value = True
            self.page.prompt_resume_session()
        self.assertEqual(self.page.current_index, 1)
        self.assertTrue(self.page.is_restricted())
        self.assertTrue(self.page.playButton.isEnabled())
        self.assertFalse(self.page.scrollEnableSwitch.isEnabled())
        self.source.write_bytes(payload)
        self.wait_until(lambda: not self.page.is_restricted())
        self.assertEqual(self.page.presentation_path, self.source)

    def test_failed_generation_does_not_replace_last_successful_session(self):
        self.load()
        self.page.current_index = 1
        self.page.set_current_label_text()
        saved = dict(self.ctx.app_settings.get('last_session'))
        self.page.clean_and_reset()
        self.page.remember_session()
        self.assertEqual(self.ctx.app_settings.get('last_session'), saved)

    def test_baseline_survives_new_history_and_whitespace_is_not_a_change(self):
        self.load()
        self.ctx.tts_engine.get_generation_profile.return_value = {'voice': 'test'}
        self.page.save_session_record()
        record = json.loads(self.page.session_record_path.read_text(encoding='utf-8'))
        self.assertEqual(changed_pages(record['presentation_notes'], self.baseline), [])
        self.assertEqual(changed_pages({1: '备注\r\n'}, {1: '备注\n'}), [])


if __name__ == '__main__':
    unittest.main()
