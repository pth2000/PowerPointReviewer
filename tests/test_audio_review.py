"""片段配音试听、候选采用、恢复与持久化的完整流程。"""

from array import array
import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import wave

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QItemSelectionModel, QTimer, Qt
from qfluentwidgets import TransparentToolButton

from app.audio import cache as audio_cache, segments as segment_audio
from app.documents import export as exporters, package as project_package
from app import paths
from app.audio.playback import PlaybackBus
from app.settings_store import AppSettings
from tasks.audio_generation_task import AudioGenerationTask
from tasks.audio_merge_task import AudioMergeTask
from tasks.audio_review_task import AudioReviewTask
from tts_engine import TTSEngine
from ui.dialogs.audio_review_dialog import AudioReviewDialog
from ui.dialogs.audio_review_widgets import SEGMENT_ROLE
from ui.pages.reviewer_page import PPTReviewer

from qt_test_case import QtTestCase


def make_wav(path, seconds=1.0, amplitude=4000):
    samples = array('h', (round(amplitude * math.sin(2 * math.pi * 220 * index / 24000))
                          for index in range(round(seconds * 24000))))
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(samples.tobytes())
    return path


class SegmentAudioTests(unittest.TestCase):
    def test_effects_change_duration_and_volume_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source = make_wav(Path(folder) / 'voice.wav')
            original = source.read_bytes()
            result = segment_audio.process_wav(original, {'speed': 2, 'volume': 50,
                                                          'leading_ms': 100, 'trailing_ms': 200})
            from app.audio.processing import _read_wav
            params, pcm = _read_wav(result)
            self.assertAlmostEqual(params.nframes / params.framerate, 0.8, delta=0.05)
            full = segment_audio.process_wav(original, {'speed': 2, 'volume': 100,
                                                       'leading_ms': 100, 'trailing_ms': 200})
            _params, full_pcm = _read_wav(full)
            self.assertAlmostEqual(max(abs(value) for value in array('h', pcm)),
                                   max(abs(value) for value in array('h', full_pcm)) / 2, delta=1)
            self.assertEqual(source.read_bytes(), original)

    def test_repeated_text_matches_occurrences_and_drops_changed_text(self):
        before = [{'page': 1, 'text': '相同'}, {'page': 1, 'text': '相同'}, {'page': 2, 'text': '原文'}]
        edits = {1: {'text': '相同', 'settings': {'speed': 0.9}}, 2: {'text': '原文'}}
        after = [{'page': 1, 'text': '新增'}, *before[:2], {'page': 2, 'text': '新文'}]
        result = segment_audio.match_edits(before, edits, after)
        self.assertEqual(set(result), {2})
        result[2]['settings']['speed'] = 1.5
        self.assertEqual(edits[1]['settings']['speed'], 0.9)

    def test_cache_references_include_originals_and_reject_paths(self):
        item = {'cache_key': 'final', 'audio_edit': {'base': {'key': 'base'}, 'original': {'key': 'original'}}}
        self.assertEqual({ref['key'] for ref in segment_audio.references(item)}, {'final', 'base', 'original'})
        with self.assertRaises(ValueError):
            segment_audio.cache_path(Path('.'), {'key': '../secret', 'ext': 'wav'})


class AudioReviewTests(QtTestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, folder in (('AUDIO_CACHE_DIR', 'audio'), ('SESSION_DIR', 'history'), ('PREVIEW_DIR', 'preview')):
            path = self.root / folder
            path.mkdir()
            patcher = patch.object(paths, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = TTSEngine()
        self.engine.set_mode('edge')
        self.engine.get_selected_voice_name = Mock(return_value='测试音色')
        self.ctx = NS(version='1.7.0', app_settings=AppSettings(), config=Mock(), tts_engine=self.engine,
                      playback_bus=PlaybackBus(), hotkeys=Mock(), slideshow_watcher=Mock())
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(self.ctx)
        self.page.notes = {1: '第一段●第二段', 2: '第三段'}
        self.page.mark_split()
        self.page.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.page.session_root_path = paths.SESSION_DIR
        self.page.media_list = [make_wav(paths.AUDIO_CACHE_DIR / f'voice{index}.wav') for index in range(3)]
        self.page.note_cache_keys = [f'voice{index}' for index in range(3)]
        self.page.note_cache_exts = ['wav'] * 3
        self.page.notes_duration_list = [1] * 3
        self.page.generation_profile = self.engine.get_generation_profile()
        self.page.generation_speaker = '测试音色'
        self.page.is_import = True
        self.page.save_session_record()
        self.record = self.page.session_record_path
        self.dialog = AudioReviewDialog(self.page)
        self.page.audio_review_dialog = self.dialog
        self.dialog.output.setMuted(True)
        self.dialog.show()
        self.qt.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.dialog.task is not None:
            self.dialog.task.requestInterruption()
            self.wait_task()
        self.dialog.candidates.clear()
        self.dialog.drafts.clear()
        self.dialog.close()
        self.dialog.deleteLater()
        self.page.presentation_monitor.shutdown()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def wait_task(self):
        task = self.dialog.task
        if task is None:
            return
        loop = QEventLoop()
        task.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        if task.isRunning():
            loop.exec()
        self.qt.processEvents()
        self.assertIsNone(self.dialog.task, self.dialog.message.text())

    def render(self, speed=1.5, volume=80):
        self.dialog.speed.setValue(speed)
        self.dialog.volume.setValue(volume)
        self.dialog.prepare(False)
        self.wait_task()
        self.assertIn(0, self.dialog.candidates, self.dialog.message.text())
        self.dialog.stop()

    def test_render_compare_adopt_restore_and_undo_update_one_record(self):
        original = self.page.media_list[0].read_bytes()
        before = self.record.read_bytes()
        self.render()
        self.assertEqual(self.record.read_bytes(), before)
        self.assertEqual(self.page.media_list[0].read_bytes(), original)
        self.dialog.adopt()
        self.assertEqual(self.page.session_record_path, self.record)
        self.assertEqual(len(list(paths.SESSION_DIR.glob('*.json'))), 1)
        self.assertAlmostEqual(self.page.notes_duration_list[0], 2 / 3, delta=0.05)
        self.assertEqual(self.page.media_list[1].name, 'voice1.wav')
        saved = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertEqual(saved['items'][0]['audio_edit']['settings']['speed'], 1.5)
        self.assertEqual(saved['items'][0]['cache_key'], self.page.note_cache_keys[0])
        self.assertEqual(paths.AUDIO_CACHE_DIR.joinpath('voice0.wav').read_bytes(), original)
        tuned = self.page.note_cache_keys[0]
        original_key = self.page.audio_edits[0]['original']['key']
        self.assertNotEqual(original_key, 'voice0')
        self.dialog.restore_original()
        self.assertEqual(self.page.note_cache_keys[0], original_key)
        self.assertNotIn(0, self.page.audio_edits)
        self.dialog.undo()
        self.assertEqual(self.page.note_cache_keys[0], tuned)
        self.assertIn(0, self.page.audio_edits)

    def test_adjusting_again_uses_unprocessed_base(self):
        self.render(speed=2, volume=50)
        self.dialog.adopt()
        # A later forced generation can replace a shared TTS cache entry.
        # Adopted adjustments must still use their own preserved original.
        make_wav(paths.AUDIO_CACHE_DIR / 'voice0.wav', seconds=2, amplitude=500)
        self.render(speed=1, volume=100)
        candidate = self.dialog.candidates[0]
        self.assertAlmostEqual(candidate['duration'], 1, delta=0.01)
        from app.audio.processing import _read_wav
        _params, pcm = _read_wav(Path(candidate['path']).read_bytes())
        self.assertGreater(max(abs(value) for value in array('h', pcm)), 3900)

    def test_batch_effects_and_page_search_do_not_control_ppt(self):
        self.dialog._select_index(1)
        self.dialog.volume.setValue(50)
        self.dialog._select_index(0)
        self.dialog.table.selectionModel().select(
            self.dialog.table.model().index(1, 1),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.assertEqual(self.dialog.selected(), [0, 1])
        self.dialog.speed_slider.setValue(80)
        self.assertEqual(self.dialog.speed.value(), 0.8)
        self.dialog.prepare(False)
        self.wait_task()
        self.dialog.stop()
        self.assertEqual(set(self.dialog.candidates), {0, 1})
        self.assertEqual(self.dialog.candidates[0]['settings']['volume'], 100)
        self.assertEqual(self.dialog.candidates[1]['settings']['volume'], 50)
        self.dialog.adopt()
        self.assertEqual(set(self.page.audio_edits), {0, 1})
        self.assertEqual(self.dialog.selected(), [0, 1])
        self.assertAlmostEqual(self.page.notes_duration_list[0], 1.25, delta=0.05)
        self.dialog.pages.setCurrentIndex(2)
        self.assertEqual(self.dialog.table.rowCount(), 1)
        self.assertEqual(self.dialog.current(), 2)
        self.dialog.search.setText('不存在')
        self.assertEqual(self.dialog.table.rowCount(), 0)
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.ctx.slideshow_watcher.start.assert_not_called()

    def test_failed_save_rolls_back_adoption_and_candidate_can_retry(self):
        self.render()
        old = list(self.page.media_list)
        original = self.record.read_bytes()
        with patch.object(self.page, 'save_session_record', side_effect=OSError('disk full')):
            self.dialog.adopt()
        self.assertEqual(self.page.media_list, old)
        self.assertEqual(self.page.audio_edits, {})
        self.assertEqual(self.record.read_bytes(), original)
        self.assertIn(0, self.dialog.candidates)
        self.dialog.adopt()
        self.assertIn(0, self.page.audio_edits)

    def test_stale_candidate_cannot_apply_to_a_different_script(self):
        self.render()
        self.page.notes_list[0] = {'page': 1, 'text': '新讲稿'}
        self.dialog.adopt()
        self.assertEqual(self.page.audio_edits, {})
        self.assertFalse(self.dialog.candidates)

    def test_generation_reuses_unchanged_tuned_audio_and_package_keeps_original(self):
        self.render()
        self.dialog.adopt()
        key = self.page.note_cache_keys[0]
        original_key = self.page.audio_edits[0]['original']['key']
        task = self.page.save_thread
        task.audio_cache_path = paths.AUDIO_CACHE_DIR
        task.output_path = self.root / 'generated'
        task.output_path.mkdir()
        with patch.object(self.engine, 'validate_generation_settings'), \
             patch.object(self.engine, 'save_file') as synthesize:
            task.configure(self.page.notes_list[:1], 0, audio_edits=self.page.audio_edits)
            result = task.save_wav()
        synthesize.assert_not_called()
        self.assertEqual(result.cache_keys, [key])
        self.assertIn(0, result.audio_edits)
        items = project_package.build_items(self.page.notes_list, self.page.notes_duration_list,
                                            self.page.note_cache_keys, self.page.note_cache_exts,
                                            self.page.media_list, audio_edits=self.page.audio_edits)
        package = self.root / 'lesson.pprpkg'
        project_package.export_package(package, items=items, notes=self.page.notes, mark='●',
                                       source_name='lesson', generation_profile=self.page.generation_profile,
                                       speaker='test', script_comments_enabled=False)
        import zipfile
        with zipfile.ZipFile(package) as zipped:
            self.assertIn(f'audio/{original_key}.wav', zipped.namelist())
            self.assertIn(f'audio/{key}.wav', zipped.namelist())
        self.assertIn(original_key, audio_cache.keys_of_record(self.record))
        audio_cache.clear_orphans(self.page.protected_audio_keys())
        self.assertTrue((paths.AUDIO_CACHE_DIR / f'{original_key}.wav').is_file())
        with patch('app.paths.ensure_runtime_dirs'):
            imported = project_package.import_package(package)
        self.page.load_session_record(imported['record_path'])
        self.assertEqual(self.page.note_cache_keys[0], key)
        self.assertEqual(self.page.audio_edits[0]['original']['key'], original_key)
        srt = exporters.build_srt(self.page.notes_list, self.page.notes_duration_list)
        self.assertIn('00:00:00,667', srt)

    def test_regenerate_candidate_preserves_source_and_spoken_text_is_not_subtitle(self):
        # Existing records may contain a spoken-text override from an earlier version.
        self.page.audio_edits[0] = {'spoken_text': '第一段的读音修正'}
        self.dialog._populate_segments()
        self.assertEqual(self.dialog.script_text.text(), '第一段的读音修正')
        def synthesize(text, path):
            make_wav(Path(path), seconds=1)
        self.dialog.speed.setValue(2)
        with patch.object(self.engine, 'save_file', side_effect=synthesize), \
             patch.object(self.engine, 'validate_generation_settings'):
            self.dialog.prepare(True)
            self.wait_task()
        self.dialog.stop()
        candidate = self.dialog.candidates[0]
        self.assertNotEqual(candidate['path'], candidate['base_path'])
        self.assertAlmostEqual(candidate['base_duration'], 1, delta=0.01)
        self.assertAlmostEqual(candidate['duration'], 0.5, delta=0.05)
        self.dialog.adopt()
        self.assertEqual(self.page.notes_list[0]['text'], '第一段')
        self.assertEqual(self.page.audio_edits[0]['spoken_text'], '第一段的读音修正')
        self.assertEqual(self.page.notes[1], '第一段●第二段')
        self.assertEqual(self.dialog.table.item(0, 2).data(SEGMENT_ROLE)['text'], '第一段的读音修正')
        self.dialog.search.setText('读音修正')
        self.assertEqual(self.dialog.table.rowCount(), 1)

    def test_undo_redo_restores_audio_settings_and_original_restore(self):
        self.render()
        self.dialog.adopt()
        first = self.page.note_cache_keys[0]
        self.render(speed=2)
        self.dialog.adopt()
        second = self.page.note_cache_keys[0]
        self.assertNotEqual(first, second)
        self.dialog.undo()
        self.assertEqual(self.page.note_cache_keys[0], first)
        self.assertEqual(self.page.audio_edits[0]['settings']['speed'], 1.5)
        with patch.object(self.engine, 'save_file') as generate:
            self.dialog.redo()
        generate.assert_not_called()
        self.assertEqual(self.page.note_cache_keys[0], second)
        self.assertEqual(self.page.audio_edits[0]['settings']['speed'], 2)
        self.dialog.restore_original()
        original = self.page.note_cache_keys[0]
        self.assertNotIn(0, self.page.audio_edits)
        self.dialog.undo()
        self.assertEqual(self.page.note_cache_keys[0], second)
        self.dialog.redo()
        self.assertEqual(self.page.note_cache_keys[0], original)
        saved = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertEqual(saved['items'][0]['cache_key'], original)
        self.assertNotIn('audio_edit', saved['items'][0])

    def test_redo_retains_audio_after_cache_cleanup_and_failed_write_can_retry(self):
        self.render()
        self.dialog.adopt()
        adopted = self.page.note_cache_keys[0]
        self.dialog.undo()
        original = self.page.note_cache_keys[0]
        audio_cache.clear_orphans(self.page.protected_audio_keys())
        self.assertTrue((paths.AUDIO_CACHE_DIR / f'{adopted}.wav').is_file())
        before = self.record.read_bytes()
        with patch.object(self.page, 'save_session_record', side_effect=OSError('disk full')):
            self.dialog.redo()
        self.assertEqual(self.page.note_cache_keys[0], original)
        self.assertEqual(self.record.read_bytes(), before)
        self.assertEqual(len(self.dialog.redo_stack), 1)
        self.dialog.redo()
        self.assertEqual(self.page.note_cache_keys[0], adopted)
        self.assertFalse(self.dialog.redo_stack)

    def test_new_adoption_clears_redo_but_preview_and_draft_keep_it(self):
        self.render()
        self.dialog.adopt()
        self.dialog.undo()
        self.assertTrue(self.dialog.redo_button.isEnabled())
        self.render(speed=0.8)
        self.assertEqual(len(self.dialog.redo_stack), 1)
        self.dialog.adopt()
        self.assertFalse(self.dialog.redo_stack)
        self.assertFalse(self.dialog.redo_button.isEnabled())

    def test_clip_badges_follow_draft_candidate_adoption_restore_and_reopen(self):
        def status():
            return self.dialog.table.item(0, 2).data(SEGMENT_ROLE)['status']
        self.assertEqual(status(), '')
        self.dialog.speed.setValue(1.5)
        self.assertEqual(status(), '待试听')
        self.render()
        self.assertEqual(status(), '待采用')
        self.dialog.adopt()
        self.assertEqual(status(), '已调整')
        self.dialog.close()
        self.dialog.show()
        self.qt.processEvents()
        self.assertEqual(status(), '已调整')
        self.dialog.restore_original()
        self.assertEqual(status(), '')
        self.dialog.undo()
        self.assertEqual(status(), '已调整')
        self.dialog.redo()
        self.assertEqual(status(), '')

    def test_cancel_undo_keeps_pending_work_and_other_segments_drafts_are_preserved(self):
        self.render()
        self.dialog.adopt()
        key = self.page.note_cache_keys[0]
        self.dialog.volume.setValue(30)
        with patch('ui.dialogs.audio_review_dialog.MessageBox') as box:
            box.return_value.exec.return_value = False
            self.dialog.undo()
        self.assertEqual(self.page.note_cache_keys[0], key)
        self.assertEqual(self.dialog.drafts[0]['settings']['volume'], 30)
        self.assertEqual(len(self.dialog.undo_stack), 1)
        self.dialog.discard()
        self.dialog._select_index(2)
        self.dialog.volume.setValue(55)
        self.dialog.undo()
        self.assertIn(2, self.dialog.drafts)
        self.assertEqual(self.dialog.current(), 0)
        self.assertTrue(self.dialog.redo_button.isEnabled())

    def test_no_change_previews_current_audio_without_creating_candidate(self):
        self.assertFalse(self.dialog.render_button.isEnabled())
        with patch.object(self.dialog, 'play') as play, \
             patch('ui.dialogs.audio_review_dialog.AudioReviewTask') as task:
            self.dialog.prepare(False)
        task.assert_not_called()
        play.assert_called_once_with(indices=[0])
        self.assertFalse(self.dialog.candidates)

    def test_batch_effects_reuse_current_audio_and_display_selected_scripts(self):
        self.dialog.table.selectionModel().select(
            self.dialog.table.model().index(1, 1),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.dialog.speed.setValue(1.2)
        self.assertIn('第一段', self.dialog.script_text.text())
        self.assertIn('第二段', self.dialog.script_text.text())
        with patch.object(self.engine, 'save_file', side_effect=lambda text, path: make_wav(Path(path))) as generate, \
             patch.object(self.engine, 'validate_generation_settings'):
            self.dialog.prepare(False)
            self.wait_task()
        self.dialog.stop()
        generate.assert_not_called()
        self.assertFalse(self.dialog.candidates[0]['regenerated'])
        self.assertFalse(self.dialog.candidates[1]['regenerated'])
        self.dialog.adopt()
        self.assertEqual(self.dialog.selected(), [0, 1])
        self.assertEqual(self.page.notes_list[1]['text'], '第二段')

    def test_switching_lecture_clears_undo_and_redo_together(self):
        self.render()
        self.dialog.adopt()
        self.dialog.undo()
        self.assertTrue(self.dialog.redo_stack)
        self.page.is_import = False
        self.page.audio_changed.emit()
        self.assertFalse(self.dialog.redo_stack)
        self.assertFalse(self.dialog.undo_stack)
        self.assertFalse(self.dialog.redo_button.isEnabled())
        self.assertFalse(self.dialog.undo_button.isEnabled())

    def test_qt_decodes_mp3_for_effects(self):
        source = self.root / 'voice.mp3'
        AudioGenerationTask._write_silence(source, 'mp3')
        job = {'index': 0, 'base_path': source, 'settings': segment_audio.DEFAULT_SETTINGS,
               'spoken_text': '测试'}
        task = AudioReviewTask([job], self.dialog._directory.name)
        ready, errors = [], []
        task.ready.connect(ready.append)
        task.failed.connect(errors.append)
        loop = QEventLoop()
        task.finished.connect(loop.quit)
        task.start()
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        task.wait(1000)
        self.qt.processEvents()
        self.assertFalse(errors)
        self.assertTrue(ready)
        self.assertGreater(ready[0][0]['duration'], 0)

    def test_batch_regeneration_uses_current_settings_without_changing_global_settings(self):
        self.engine.set_voice(0, '全局音色')
        for index, name in enumerate(('音色甲', '音色乙')):
            profile = copy.deepcopy(self.page.generation_profile)
            profile.update(voice_name=name, voice_index=index + 1)
            self.page.audio_edits[index] = {'voice_profile': profile}
        self.dialog.table.selectionModel().select(
            self.dialog.table.model().index(1, 1),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.dialog.speed.setValue(1.25)
        profile_before = copy.deepcopy(self.engine.get_generation_profile())
        record_before = self.record.read_bytes()
        audio_before = list(self.page.note_cache_keys)
        voices = []

        def synthesize(engine, text, path):
            voices.append(engine.get_generation_profile()['voice_name'])
            make_wav(Path(path))

        with patch.object(TTSEngine, 'save_file', autospec=True, side_effect=synthesize), \
             patch.object(TTSEngine, 'validate_generation_settings'):
            self.dialog.generate_button.trigger()
            self.wait_task()
        self.dialog.stop()
        self.assertEqual(voices, ['全局音色', '全局音色'])
        self.assertEqual(set(self.dialog.candidates), {0, 1})
        self.assertEqual(self.engine.get_generation_profile(), profile_before)
        self.assertEqual(self.record.read_bytes(), record_before)
        self.assertEqual(self.page.note_cache_keys, audio_before)
        for candidate in self.dialog.candidates.values():
            self.assertEqual(candidate['voice_profile'], segment_audio.public_profile(profile_before))
            self.assertEqual(candidate['settings']['speed'], 1.25)
        self.dialog.adopt()
        for index in (0, 1):
            self.assertEqual(self.page.audio_edits[index]['voice_profile'], segment_audio.public_profile(profile_before))

    def test_mixed_audio_merges_and_failure_preserves_existing_output(self):
        source = self.root / 'voice.mp3'
        AudioGenerationTask._write_silence(source, 'mp3')
        output = self.root / 'combined.wav'
        sources = [source, self.page.media_list[0]]
        self.assertFalse(exporters.can_merge_audio_directly(sources))

        def run(sources):
            task = AudioMergeTask(sources, output)
            ready, errors = [], []
            task.ready.connect(lambda: ready.append(True))
            task.failed.connect(errors.append)
            loop = QEventLoop()
            task.finished.connect(loop.quit)
            task.start()
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            self.assertTrue(task.wait(1000))
            self.qt.processEvents()
            return ready, errors

        ready, errors = run(sources)
        self.assertTrue(ready)
        self.assertFalse(errors)
        with wave.open(str(output), 'rb') as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels()), (48000, 1))
            self.assertGreater(reader.getnframes() / reader.getframerate(), 1)
        before = output.read_bytes()
        ready, errors = run([self.root / 'missing.mp3'])
        self.assertFalse(ready)
        self.assertTrue(errors)
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.audio-merge-*')))

    def test_playback_ranges_finish_and_stop_ignore_stale_end_signal(self):
        self.dialog.play_range.setCurrentIndex(1)
        with patch.object(self.dialog, '_play_queued') as queued:
            self.dialog.play()
            self.assertEqual(self.dialog._queue, [(0, 'current'), (1, 'current')])
            serial = self.dialog._play_serial
            self.dialog._advance(serial)
            self.assertEqual(self.dialog._queue_offset, 1)
            count = queued.call_count
            self.dialog._advance(serial)
            self.assertEqual(self.dialog._queue_offset, 1)
            self.assertFalse(self.dialog._playing)
            self.assertEqual(queued.call_count, count)
            self.dialog._advance(serial)
            self.assertEqual(queued.call_count, count)
            self.dialog.play()
            serial = self.dialog._play_serial
            count = queued.call_count
            self.dialog.stop()
            self.dialog._advance(serial)
            self.assertEqual(queued.call_count, count)
        self.dialog.play_range.setCurrentIndex(3)
        self.dialog._select_index(1)
        with patch.object(self.dialog, '_play_queued'):
            self.dialog.play()
            self.assertEqual(self.dialog._queue, [(1, 'current'), (2, 'current')])

    def test_close_reopen_and_discard_clean_draft(self):
        self.dialog.resize(960, 620)
        self.qt.processEvents()
        self.assertLessEqual(self.dialog.height(), 640)
        self.assertFalse(self.dialog.compare_card.isVisible())
        self.render()
        self.qt.processEvents()
        self.assertTrue(self.dialog.compare_card.isVisible())
        self.assertEqual(self.dialog.script_text.text(), '第一段')
        self.assertTrue(self.dialog.script_text.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        viewport = self.dialog
        position = self.dialog.apply_button.mapTo(viewport, self.dialog.apply_button.rect().center())
        self.assertTrue(viewport.rect().contains(position))
        self.dialog.discard()
        self.dialog.speed.setValue(1.5)
        self.assertTrue(self.dialog.drafts)
        self.dialog.discard()
        self.assertFalse(self.dialog.drafts)
        directory = Path(self.dialog._directory.name)
        self.assertTrue(self.dialog.close())
        self.assertFalse(directory.exists())
        self.dialog.show()
        self.qt.processEvents()
        self.assertTrue(Path(self.dialog._directory.name).is_dir())
        self.render()

    def test_editor_expands_complete_selected_text_and_follows_window_width(self):
        self.page.notes_list[0]['text'] = '这里是一段需要逐句核对读音和节奏的较长讲稿。' * 30
        self.page.audio_changed.emit()
        self.dialog.resize(960, 620)
        self.qt.processEvents()
        narrow = self.dialog.table.rowHeight(0)
        self.assertTrue(self.dialog.table.item(0, 2).data(SEGMENT_ROLE)['expanded'])
        self.dialog.resize(1180, 760)
        self.qt.processEvents()
        self.assertLess(self.dialog.table.rowHeight(0), narrow)
        expanded = self.dialog.table.rowHeight(0)
        self.dialog._select_index(1)
        self.assertLess(self.dialog.table.rowHeight(0), expanded)
        self.assertFalse(self.dialog.table.item(0, 2).data(SEGMENT_ROLE)['expanded'])

    def test_play_buttons_stay_centered_in_rows_after_search_and_filter_changes(self):
        self.page.notes_list[0]['text'] = '搜索目标第一段，这是一段需要核对的讲稿。' * 8
        self.page.notes_list[1]['text'] = '搜索目标第二段，这是另一段更长的讲稿内容。' * 13
        self.page.audio_changed.emit()
        self.dialog.resize(960, 620)

        def assert_buttons_centered():
            self.qt.processEvents()
            for row in range(self.dialog.table.rowCount()):
                cell = self.dialog.table.cellWidget(row, 0)
                button = cell.findChild(TransparentToolButton)
                rect = self.dialog.table.visualRect(self.dialog.table.model().index(row, 0))
                center = button.mapTo(self.dialog.table.viewport(), button.rect().center())
                self.assertLessEqual(abs(center.y() - rect.center().y()), 1)
                self.assertTrue(rect.contains(cell.geometry()))
                self.assertTrue(cell.rect().contains(button.geometry()))

        assert_buttons_centered()
        for query in ('搜', '搜索', '第二段', '没有匹配的内容', '目标', ''):
            self.dialog.search.setText(query)
            assert_buttons_centered()
        self.dialog._select_index(1)
        assert_buttons_centered()
        self.dialog.resize(1180, 760)
        assert_buttons_centered()
        self.dialog.pages.setCurrentIndex(2)
        assert_buttons_centered()
        self.dialog.pages.setCurrentIndex(0)
        assert_buttons_centered()

    def test_new_and_current_audio_share_player_without_changing_saved_audio(self):
        before = self.record.read_bytes()
        self.assertTrue(self.dialog.render_button.isVisible())
        self.assertFalse(self.dialog.apply_button.isVisible())
        self.render()
        self.assertFalse(self.dialog.render_button.isVisible())
        self.assertTrue(self.dialog.apply_button.isVisible())
        self.dialog._compare_source('current')
        self.assertEqual(Path(self.dialog.player.source().toLocalFile()), self.page.media_list[0])
        self.dialog._compare_source('candidate')
        self.assertEqual(Path(self.dialog.player.source().toLocalFile()), self.dialog.candidates[0]['path'])
        self.dialog.stop()
        self.assertIn('0.67', self.dialog.time_label.text())
        self.assertEqual(self.record.read_bytes(), before)

    def test_changing_script_while_candidate_is_prepared_invalidates_result(self):
        self.dialog.speed.setValue(1.25)
        self.dialog.prepare(False)
        self.page.is_import = False
        self.page.audio_changed.emit()
        self.wait_task()
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertFalse(self.dialog.play_button.isEnabled())
        self.page.is_import = True
        self.page.notes_list[0] = {'page': 1, 'text': '另一份讲稿'}
        self.page.audio_changed.emit()
        self.assertFalse(self.dialog.candidates)
        self.assertFalse(self.dialog.drafts)


if __name__ == '__main__':
    unittest.main()
