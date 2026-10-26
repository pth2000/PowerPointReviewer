"""不创建窗口，验证工程状态、历史兼容和更新重试的业务边界。"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from app.project import history
from app.project.generation import GenerationWorkflow
from app.project.model import ProjectSession, UnavailableReason


class ProjectSessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.audio = self.root / 'first.wav'
        self.audio.write_bytes(b'existing audio')
        self.project = ProjectSession(
            notes={1: '讲稿'}, notes_list=[{'page': 1, 'text': '讲稿'}],
            media_list=[self.audio], notes_duration_list=[1.0],
            note_cache_keys=['first'], note_cache_exts=['wav'], audio_loaded=True)

    def test_unlinked_project_keeps_document_and_audio_actions_available(self):
        state = self.project.availability()
        self.assertIsNone(state.unavailable_for())
        self.assertIsNone(state.unavailable_for(audio=True, durations=True))
        self.assertEqual(state.unavailable_for(presentation=True), UnavailableReason.PRESENTATION)
        self.assertEqual(state.sync_unavailable_reason(), UnavailableReason.PRESENTATION)

    def test_missing_audio_and_missing_durations_affect_only_dependent_operations(self):
        self.project.notes_duration_list = []
        state = self.project.availability()
        self.assertIsNone(state.unavailable_for(audio=True))
        self.assertEqual(state.unavailable_for(audio=True, durations=True), UnavailableReason.AUDIO)
        self.audio.unlink()
        state = self.project.availability()
        self.assertIsNone(state.unavailable_for())
        self.assertEqual(state.unavailable_for(audio=True), UnavailableReason.AUDIO)

    def test_task_and_preparation_states_take_precedence_over_missing_presentation(self):
        state = self.project.availability(busy=True)
        self.assertEqual(state.unavailable_for(audio=True, presentation=True), UnavailableReason.BUSY)
        self.assertEqual(state.sync_unavailable_reason(), UnavailableReason.BUSY)
        state = self.project.availability(busy=True, preparing=True)
        self.assertEqual(state.sync_unavailable_reason(), UnavailableReason.PREPARING)

    def test_comments_sparse_pages_and_audio_reset_preserve_source_and_edits(self):
        self.project.notes = {3: '// 提醒\n 第一段 <分段> 第二段 <分段>', 1: ''}
        self.project.mark = '<分段>'
        self.project.audio_edits = {0: {'custom': 'keep'}}
        self.project.split_notes(comments_enabled=True)
        self.assertEqual(self.project.notes_list, [
            {'page': 1, 'text': ''}, {'page': 3, 'text': '第一段'}, {'page': 3, 'text': '第二段'}])
        self.project.clear_audio()
        self.assertIn('// 提醒', self.project.notes[3])
        self.assertEqual(self.project.audio_edits, {0: {'custom': 'keep'}})
        self.assertFalse(self.project.audio_loaded)

    def test_legacy_history_recovers_extension_and_rebuilds_pages_without_original_ppt(self):
        record = self.root / 'old.json'
        record.write_text(json.dumps({'items': [
            {'page': 3, 'text': '第一句', 'duration': 1, 'cache_key': 'first'},
            {'page': 3, 'text': '第二句', 'duration': 2, 'cache_key': 'first'},
        ]}), encoding='utf-8')
        loaded = history.load_session(record, self.root, Mock(), fallback_mark='<分段>')
        self.assertEqual(loaded.project.notes, {3: '第一句<分段>第二句'})
        self.assertEqual(loaded.project.note_cache_exts, ['wav', 'wav'])
        self.assertTrue(loaded.project.audio_loaded)
        self.assertFalse(loaded.project.has_presentation())

    def test_saving_preserves_record_identity_and_failed_replace_leaves_no_temporary_file(self):
        record = self.root / 'history.json'
        original = {'session_id': 'stable', 'created_at': 'original time', 'custom': 'keep'}
        record.write_text(json.dumps(original), encoding='utf-8')
        self.project.session_record_path = record
        engine = Mock()
        engine.get_generation_profile.return_value = {'mode': 'edge'}
        engine.get_selected_voice_name.return_value = '原音色'
        history.save_session(self.project, self.root, engine)
        saved = json.loads(record.read_text(encoding='utf-8'))
        for key, value in original.items():
            self.assertEqual(saved[key], value)
        previous = record.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                history.save_session(self.project, self.root, engine)
        self.assertEqual(record.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_retry_after_generation_failure_retains_original_page_and_generation_profile(self):
        self.project.notes_list = [
            {'page': 1, 'text': '前页'}, {'page': 2, 'text': '第一段'}, {'page': 2, 'text': '第二段'}]
        self.project.current_index = 2
        self.project.session_record_path = self.root / 'history.json'
        self.project.notes = {1: '新增第一段●新增第二段●新增第三段', 2: '修改后的第一段●第二段'}
        workflow = GenerationWorkflow(self.project)
        workflow.prepare_notes(comments_enabled=False)
        self.project.clear_audio()
        workflow.prepare_notes(comments_enabled=False)
        count = len(self.project.notes_list)
        profile = {'mode': 'original', 'voice': 'original'}
        result = NS(media_paths=[self.audio] * count, durations=[1] * count,
                    cache_keys=['first'] * count, cache_exts=['wav'] * count,
                    generation_profile=profile, speaker='原音色', cache_hit_count=count)
        engine = Mock()
        workflow.apply_result(result, engine)
        self.assertEqual(self.project.current_index, 3)
        self.assertEqual(self.project.generation_profile, profile)
        self.assertEqual(self.project.generation_speaker, '原音色')
        engine.get_generation_profile.assert_not_called()
        engine.get_selected_voice_name.assert_not_called()


if __name__ == '__main__':
    unittest.main()
