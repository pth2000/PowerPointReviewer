"""独立讲稿处理、跨文稿替换和配音历史隔离。"""

import hashlib
import json
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docx import Document
from pptx import Presentation
from PySide6.QtCore import QCoreApplication, QEvent
from qfluentwidgets import PushButton
from qt_test_case import QtTestCase
from test_presentation_link import make_ppt

from app import paths
from app.audio.playback import PlaybackBus
from app.documents import io as script_io
from app.documents import package as project_package
from app.settings_store import AppSettings
from tasks.audio_generation_task import GenerationResult
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.tools_page import ToolsInterface


class ScriptOnlyTests(QtTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        settings = AppSettings()
        settings.set('auto_generate_audio', False)
        self.engine = Mock()
        self.engine.get_selected_voice_name.return_value = '测试声音'
        self.engine.get_generation_profile.return_value = {'mode': 'edge', 'options': {}}
        self.engine.snapshot.return_value = self.engine
        context = SimpleNamespace(
            app_settings=settings,
            config=Mock(),
            playback_bus=PlaybackBus(),
            slideshow_watcher=Mock(),
            hotkeys=Mock(),
            tts_engine=self.engine,
        )
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(context)
        for name, subfolder in (
            ('audio_cache_path', 'audio'),
            ('session_root_path', 'sessions'),
            ('wav_temp_path', 'temp'),
        ):
            path = self.root / subfolder
            path.mkdir()
            setattr(self.page, name, path)
        self.page.presentation_monitor.timer.stop()
        self.tools = ToolsInterface(context, self.page)
        self.addCleanup(self.dispose)
        self.target = make_ppt(self.root / '中文.pptx')
        self.source = make_ppt(self.root / 'English.pptx')
        ppt = Presentation(self.source)
        for number, slide in enumerate(ppt.slides, 1):
            slide.notes_slide.notes_text_frame.text = f'English notes {number}.'
        ppt.save(self.source)
        for method in (
            'create_warning_info_bar',
            'create_error_info_bar',
            'create_success_info_bar',
        ):
            patcher = patch.object(self.page, method)
            patcher.start()
            self.addCleanup(patcher.stop)

    def dispose(self):
        self.page.stop_audio()
        self.page.presentation_monitor.shutdown()
        self.tools.deleteLater()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def load(self):
        self.assertTrue(self.page.import_file(self.target))
        self.page.presentation_monitor.timer.stop()

    def finish_audio(self):
        paths, keys = [], []
        for note in self.page.project.notes_list:
            key = hashlib.sha256(note['text'].encode()).hexdigest()
            path = self.page.audio_cache_path / f'{key}.wav'
            with wave.open(str(path), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(24000)
                writer.writeframes(bytes(4800))
            paths.append(path)
            keys.append(key)
        self.page.thread_save_finish(
            GenerationResult(
                media_paths=paths,
                durations=[0.1] * len(paths),
                cache_keys=keys,
                cache_exts=['wav'] * len(paths),
                generation_profile={'mode': 'edge', 'options': {}},
                speaker='测试声音',
            )
        )
        return self.page.project.session_record_path

    def test_import_only_does_not_touch_tts_or_animation_validation(self):
        with (
            patch.object(self.page.save_thread, 'start') as start,
            patch.object(self.page, '_confirm_animation_alignment') as check,
        ):
            self.load()
        start.assert_not_called()
        check.assert_not_called()
        self.engine.assert_not_called()
        self.assertEqual(self.engine.mock_calls, [])
        self.assertFalse(self.page.project.audio_loaded)
        self.assertFalse(self.page.project.audio_outdated)
        self.assertEqual(self.page.playButton.accessibleName(), '播放')
        self.assertFalse(self.page.playButton.isEnabled())
        self.assertEqual(self.page.generate_audio_link.text(), '生成配音')
        self.assertTrue(self.page.generate_audio_link.isEnabled())
        self.assertFalse(self.page.generate_audio_link.isHidden())
        self.assertEqual(list(self.page.session_root_path.iterdir()), [])

    def test_linking_ppt_to_an_unvoiced_script_keeps_script_and_uses_matching_prompt(self):
        source = self.root / 'script.json'
        source.write_text(
            json.dumps({'pages': [{'page': 1, 'text': '独立讲稿'}]}), encoding='utf-8'
        )
        self.assertTrue(self.page.import_file(source))
        with patch('ui.pages.reviewer_page.PptImportDialog') as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.selected_action.return_value = 'link'
            self.assertTrue(self.page.import_file(self.target))
        self.assertFalse(dialog.call_args.kwargs['has_audio'])
        self.assertFalse(dialog.call_args.kwargs['generate_audio'])
        self.assertEqual(self.page.project.notes, {1: '独立讲稿'})
        self.assertEqual(self.page.project.presentation_path, self.target)
        self.assertEqual(self.page.notes_path_text.text, str(self.target))
        self.assertFalse(self.page.project.audio_loaded)

    def test_document_exports_have_no_fabricated_audio_metadata_and_can_be_reimported(self):
        self.load()
        output = self.root / 'exports'
        output.mkdir()
        with patch.object(self.tools, 'choose_export_dir', return_value=output):
            self.tools.write_to_word()
            self.tools.write_to_json()
            self.tools.write_to_markdown()
        word = next(output.glob('*.docx'))
        self.assertEqual(
            [cell.text for cell in Document(word).tables[0].rows[0].cells], ['页码', '讲稿']
        )
        data = next(output.glob('*.json'))
        payload = json.loads(data.read_text(encoding='utf-8'))
        self.assertNotIn('engine', payload)
        self.assertNotIn('speaker', payload)
        self.assertTrue(all('duration' not in page for page in payload['pages']))
        markdown = next(output.glob('*.md')).read_text(encoding='utf-8')
        self.assertNotIn('00:00', markdown)
        self.assertNotIn('时长', markdown)
        expected = {page: text.strip() for page, text in self.page.project.notes.items()}
        self.assertEqual(script_io.load_script(word).notes, expected)
        self.assertEqual(script_io.load_script(data).notes, expected)
        self.assertEqual(self.engine.mock_calls, [])

    def test_cross_ppt_update_preserves_target_and_both_original_files(self):
        self.load()
        originals = {path: path.read_bytes() for path in (self.source, self.target)}
        baseline = dict(self.page.presentation_monitor.baseline)
        self.assertTrue(self.page.update_notes_from_file(self.source))
        self.assertEqual(self.page.project.presentation_path, self.target)
        self.assertEqual(self.page.project.note_file_path, self.target)
        self.assertEqual(self.page.project.note_file_name, self.target.stem)
        self.assertEqual(self.page.project.notes, script_io.load_script(self.source).notes)
        self.assertEqual(self.page.presentation_monitor.baseline, baseline)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.assertFalse(self.page.project.audio_loaded)
        self.assertEqual(list(self.page.session_root_path.iterdir()), [])

    def test_larger_source_is_rejected_without_changing_the_current_script(self):
        self.load()
        before = dict(self.page.project.notes)
        larger = make_ppt(self.root / 'larger.pptx', count=3)
        self.assertFalse(self.page.update_notes_from_file(larger))
        self.assertEqual(self.page.project.notes, before)
        self.assertEqual(self.page.project.presentation_path, self.target)

    def test_missing_target_disables_ppt_actions_even_before_audio_generation(self):
        self.load()
        self.assertTrue(self.page.update_ppt_action.isEnabled())
        self.target.unlink()
        self.page._poll_presentation()
        self.assertFalse(self.page.update_ppt_action.isEnabled())
        buttons = [
            button
            for button in self.tools.script_export_card.findChildren(PushButton)
            if button.property('requires_ppt')
        ]
        self.assertTrue(buttons)
        self.assertTrue(all(not button.isEnabled() for button in buttons))

    def test_ppt_note_export_uses_updated_script_and_preserves_original_target(self):
        self.load()
        self.assertTrue(self.page.update_notes_from_file(self.source))
        original = self.target.read_bytes()
        output = self.root / 'exports'
        output.mkdir()
        with patch.object(self.tools, 'choose_export_dir', return_value=output):
            self.tools.write_to_ppt()
        saved = next(output.glob('*.pptx'))
        self.assertEqual(
            {page: text.strip() for page, text in script_io.load_script(saved).notes.items()},
            {page: text.strip() for page, text in script_io.load_script(self.source).notes.items()},
        )
        self.assertEqual(self.target.read_bytes(), original)

    def test_partial_ppt_update_requires_confirmation_and_keeps_omitted_pages(self):
        self.load()
        smaller = make_ppt(self.root / 'smaller.pptx', count=1)
        before = dict(self.page.project.notes)
        with patch('ui.pages.reviewer_page.MessageBox') as box:
            box.return_value.exec.return_value = False
            self.assertFalse(self.page.update_notes_from_file(smaller))
            self.assertEqual(self.page.project.notes, before)
            box.return_value.exec.return_value = True
            self.assertTrue(self.page.update_notes_from_file(smaller))
        self.assertEqual(self.page.project.notes[2], before[2])

    def test_pending_script_does_not_overwrite_history_until_audio_is_ready(self):
        self.load()
        record = self.finish_audio()
        before = record.read_bytes()
        self.assertTrue(self.page.update_notes_from_file(self.source))
        self.assertTrue(self.page.project.audio_outdated)
        self.assertFalse(self.page.project.audio_loaded)
        self.assertEqual(self.page.playButton.accessibleName(), '播放')
        self.assertEqual(self.page.generate_audio_link.text(), '更新配音')
        self.assertEqual(self.page.project.media_list, [])
        self.page.save_session_record()
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(self.finish_audio(), record)
        saved = json.loads(record.read_text(encoding='utf-8'))
        self.assertIn('English notes', saved['notes']['1'])
        self.assertFalse(self.page.project.audio_outdated)
        self.assertEqual(self.page.playButton.accessibleName(), '播放')
        self.assertTrue(self.page.generate_audio_link.isHidden())
        self.assertEqual(list(self.page.session_root_path.glob('*.json')), [record])

    def test_manual_generation_runs_validation_and_does_not_start_playback(self):
        self.load()
        with (
            patch.object(self.page, '_confirm_animation_alignment', return_value=True) as check,
            patch.object(self.page.save_thread, 'configure'),
            patch.object(self.page.save_thread, 'start') as start,
            patch.object(self.page.player, 'play') as play,
        ):
            self.page.init_play()
            start.assert_not_called()
            self.page.generate_audio_link.click()
        check.assert_called_once()
        start.assert_called_once()
        play.assert_not_called()

    def test_ai_adoption_only_updates_the_script_when_automatic_audio_is_disabled(self):
        self.load()
        with patch.object(self.page.save_thread, 'start') as start:
            self.assertEqual(self.page.apply_rewritten_notes({1: 'Rewritten notes.'}), 1)
        self.assertEqual(self.page.project.notes[1], 'Rewritten notes.')
        start.assert_not_called()
        self.assertEqual(list(self.page.session_root_path.iterdir()), [])

    def test_ppt_update_does_not_request_a_voice_or_generate_audio_when_disabled(self):
        self.load()
        ppt = Presentation(self.target)
        ppt.slides[0].notes_slide.notes_text_frame.text = '更新后的讲稿'
        ppt.save(self.target)
        with (
            patch('ui.pages.reviewer_page.PptUpdateDialog') as dialog,
            patch.object(self.page.save_thread, 'start') as start,
        ):
            dialog.return_value.exec.return_value = True
            self.page.update_ppt_notes()
        self.assertFalse(dialog.call_args.kwargs['generate_audio'])
        self.assertEqual(self.page.project.notes[1].strip(), '更新后的讲稿')
        self.engine.get_selected_voice_name.assert_not_called()
        start.assert_not_called()

    def test_package_without_audio_loads_script_without_leaving_a_broken_history_record(self):
        self.load()
        package = self.root / 'notes.pprpkg'
        items = [
            {
                'page': note['page'],
                'index': index,
                'text': note['text'],
                'duration': 1,
                'cache_key': f'missing{index}',
                'cache_ext': 'wav',
            }
            for index, note in enumerate(self.page.project.notes_list)
        ]
        project_package.export_package(
            package,
            items=items,
            notes=self.page.project.notes,
            mark='●',
            source_name='讲稿',
            generation_profile={},
            speaker='',
            include_audio=False,
        )
        with (
            patch.object(paths, 'AUDIO_CACHE_DIR', self.page.audio_cache_path),
            patch.object(paths, 'SESSION_DIR', self.page.session_root_path),
            patch.object(paths, 'ensure_runtime_dirs'),
        ):
            self.assertTrue(self.page.import_file(package))
        self.assertTrue(self.page.project.notes_list)
        self.assertFalse(self.page.project.audio_loaded)
        self.assertEqual(list(self.page.session_root_path.glob('*.json')), [])
