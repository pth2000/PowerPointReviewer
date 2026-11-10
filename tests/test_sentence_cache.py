"""分句缓存的局部更新、失败恢复、并发与历史引用保护。"""

import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from test_subtitle_sentences import wav_content

from app import paths
from app.audio import cache as audio_cache
from app.audio.captions import SENTENCE_MODE, read_captions, split_sentences, write_captions
from app.audio.sentence_cache import cache_key, get_or_generate
from tasks.audio_generation_task import AudioGenerationTask
from tts_engine import TTSEngine


class SentenceCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cache = self.root / 'cache'
        self.sessions = self.root / 'sessions'
        self.cache.mkdir()
        self.sessions.mkdir()
        for name, value in (('AUDIO_CACHE_DIR', self.cache), ('SESSION_DIR', self.sessions)):
            patcher = patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = TTSEngine()
        self.engine.set_mode('edge')
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        self.calls = []
        self.fail_text = ''

        def synthesize(_mode, text, target, *_args, **_kwargs):
            self.calls.append(text)
            if text == self.fail_text:
                raise RuntimeError('test service failure')
            Path(target).write_bytes(wav_content(24000))

        def decode(source, target, *_args, **_kwargs):
            shutil.copyfile(source, target)

        for patcher in (
            patch.object(self.engine, '_synthesize_file_by_mode', side_effect=synthesize),
            patch('app.audio.decode.decode_wav', side_effect=decode),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def generate(self, text='第一句。第二句。第三句。', **kwargs):
        target = self.root / 'merged.wav'
        self.engine.save_file(text, str(target), **kwargs)
        return target

    def test_changing_one_sentence_only_synthesizes_that_sentence(self):
        self.generate()
        self.calls.clear()
        target = self.generate('第一句。更新第二句。第三句。')
        self.assertEqual(self.calls, ['更新第二句。'])
        self.assertEqual(len(read_captions(target)['cues']), 3)
        self.calls.clear()
        self.generate('第三句。第一句。更新第二句。')
        self.assertEqual(self.calls, [])

    def test_retry_keeps_finished_sentences_and_does_not_replace_previous_output(self):
        target = self.root / 'merged.wav'
        target.write_bytes(b'previous complete audio')
        self.fail_text = '第二句。'
        with self.assertRaisesRegex(RuntimeError, 'test service failure'):
            self.generate()
        self.assertEqual(target.read_bytes(), b'previous complete audio')
        self.assertEqual(self.calls, ['第一句。', '第二句。'])
        self.calls.clear()
        self.fail_text = ''
        self.generate()
        self.assertEqual(self.calls, ['第二句。', '第三句。'])
        self.assertFalse(list(self.cache.glob('sentence-*')))

    def test_padding_reuses_sources_but_voice_parameters_invalidate_them(self):
        self.generate()
        self.calls.clear()
        self.engine.set_current_option('leading_silence_ms', 200)
        timing = read_captions(self.generate())
        self.assertEqual(self.calls, [])
        self.assertEqual(timing['cues'][0]['start'], 9600)
        self.engine.set_current_option('volume', 0.5)
        self.generate()
        self.assertEqual(self.calls, ['第一句。', '第二句。', '第三句。'])

    def test_explicit_rate_override_uses_a_different_cache(self):
        self.generate(rate=180)
        self.calls.clear()
        self.generate(rate=220)
        self.assertEqual(len(self.calls), 3)
        self.calls.clear()
        self.generate(rate=180)
        self.assertEqual(self.calls, [])

    def test_paragraph_boundaries_are_part_of_the_merged_cache_identity(self):
        key = self.engine.build_audio_cache_key('第一段\n第二段')
        self.assertEqual(key, self.engine.build_audio_cache_key(' 第一段\r\n\n 第二段 '))
        self.assertNotEqual(key, self.engine.build_audio_cache_key('第一段 第二段'))

    def test_batch_generation_reuses_sentences_and_respects_force_regeneration(self):
        task = AudioGenerationTask(self.engine)
        task.output_path = self.root / 'output'
        task.output_path.mkdir()
        profile = self.engine.get_generation_profile()
        original = {'page': 1, 'text': '第一句。第二句。第三句。'}
        task._save_one_note_wav(0, original, profile)
        self.calls.clear()
        modified = {**original, 'text': '第一句。修改第二句。第三句。'}
        task._save_one_note_wav(0, modified, profile)
        self.assertEqual(self.calls, ['修改第二句。'])
        self.calls.clear()
        self.assertTrue(task._save_one_note_wav(0, modified, profile)[-1])
        self.assertEqual(self.calls, [])
        task.force_regenerate = True
        task._save_one_note_wav(0, modified, profile)
        self.assertEqual(self.calls, ['第一句。', '修改第二句。', '第三句。'])

    def test_cancelled_generation_keeps_previous_source_and_removes_temporary_files(self):
        key = cache_key('正文。', {})
        before = get_or_generate(self.cache, key, lambda target: target.write_bytes(wav_content()))
        cancelled = threading.Event()

        def generate(target):
            target.write_bytes(wav_content(24000))
            cancelled.set()

        with self.assertRaises(InterruptedError):
            get_or_generate(self.cache, key, generate, force=True, cancelled=cancelled.is_set)
        self.assertEqual((self.cache / f'{key}.wav').read_bytes(), before)
        self.assertFalse(list(self.cache.glob('sentence-*')))

    def test_forced_generation_refreshes_sources_and_failure_keeps_previous_cache(self):
        self.generate()
        originals = {path: path.read_bytes() for path in self.cache.glob('*.wav')}
        self.calls.clear()
        self.fail_text = '第一句。'
        with self.assertRaises(RuntimeError):
            self.generate(force_sentence_regeneration=True)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        self.fail_text = ''
        self.calls.clear()
        self.generate(force_sentence_regeneration=True)
        self.assertEqual(len(self.calls), 3)

    def test_corrupted_source_cache_is_rebuilt(self):
        source_key = read_captions(self.generate())['cues'][1]['source_key']
        (self.cache / f'{source_key}.wav').write_bytes(b'incomplete audio')
        self.calls.clear()
        self.generate()
        self.assertEqual(self.calls, ['第二句。'])

    def test_parallel_requests_for_the_same_sentence_share_one_synthesis(self):
        key = cache_key('相同句子。', {'voice': 'test'})
        calls = []
        started = threading.Event()

        def generate(target):
            calls.append(True)
            started.set()
            target.write_bytes(wav_content())

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(get_or_generate, self.cache, key, generate) for _ in range(6)
            ]
            self.assertTrue(started.wait(5))
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_orphan_cleanup_and_record_deletion_preserve_shared_sentences(self):
        self.generate('共享句子。第一段。')
        first = self.cache / 'parent1.wav'
        shutil.copyfile(self.root / 'merged.wav', first)
        self.generate('共享句子。第二段。')
        second = self.cache / 'parent2.wav'
        shutil.copyfile(self.root / 'merged.wav', second)
        records = []
        for key in ('parent1', 'parent2'):
            record = self.sessions / f'{key}.json'
            record.write_text(json.dumps({'items': [{'cache_key': key, 'cache_ext': 'wav'}]}))
            records.append(record)
        first_keys = audio_cache.keys_of_record(records[0])
        second_keys = audio_cache.keys_of_record(records[1])
        shared = (first_keys & second_keys).pop()
        orphan = self.cache / 'unused.wav'
        orphan.write_bytes(wav_content())
        audio_cache.clear_orphans()
        self.assertFalse(orphan.exists())
        self.assertTrue(
            all((self.cache / f'{key}.wav').is_file() for key in first_keys | second_keys)
        )
        audio_cache.remove_keys(audio_cache.exclusive_keys([records[0]]))
        self.assertFalse(first.exists())
        self.assertTrue((self.cache / f'{shared}.wav').is_file())
        self.assertTrue(second.exists())

    def test_untrusted_sentence_reference_cannot_escape_cache_directory(self):
        source = self.cache / 'parent.wav'
        source.write_bytes(
            write_captions(
                wav_content(),
                [{'start': 0, 'end': 48000, 'text': '正文', 'source_key': '../outside'}],
            )
        )
        self.assertEqual(audio_cache.expand_sentence_keys(['parent']), {'parent'})

    def test_parallel_generation_reports_monotonic_sentence_progress_and_cache_reuse(self):
        self.engine.get_selected_voice_name = lambda: 'test voice'
        task = AudioGenerationTask(self.engine)
        task.output_path = self.root / 'output'
        task.output_path.mkdir()
        notes = [{'page': 1, 'text': '共同句。甲句。'}, {'page': 1, 'text': '共同句。乙句。'}]
        task.configure(notes)
        events = []
        task.signal_progress.connect(events.append, Qt.ConnectionType.DirectConnection)
        result = task.save_wav()
        self.assertEqual(result.sentence_count, 4)
        self.assertEqual(result.sentence_cache_hit_count, 1)
        self.assertEqual(self.calls.count('共同句。'), 1)
        self.assertEqual(
            [event['completed'] for event in events], sorted(event['completed'] for event in events)
        )
        self.assertEqual(events[-1]['completed'], 4)
        self.calls.clear()
        task.configure(notes)
        result = task.save_wav()
        self.assertEqual(result.sentence_cache_hit_count, 4)
        self.assertEqual(self.calls, [])

    def test_generation_error_identifies_page_segment_and_sentence(self):
        task = AudioGenerationTask(self.engine)
        task.output_path = self.root
        notes = [{'page': 7, 'text': '前一段。'}, {'page': 7, 'text': '第一句。第二句。'}]
        task.configure(notes)
        self.fail_text = '第二句。'
        with self.assertRaisesRegex(RuntimeError, '第 7 页 · 第 2 段 · 第 2 句'):
            task._save_one_note_wav(1, notes[1], task.tts_engine.get_generation_profile())


class SpeechSegmentationTests(unittest.TestCase):
    def test_unpunctuated_technical_clause_is_kept_whole(self):
        text = '通过优化物理层处理流程显著提升复杂环境下无线通信系统的传输可靠性和运行效率'
        self.assertEqual(split_sentences(text), [text])

    def test_english_sentences_decimal_numbers_and_abbreviations(self):
        text = 'Dr. Smith uses 3.5 GHz. This is the second sentence.'
        self.assertEqual(
            split_sentences(text), ['Dr. Smith uses 3.5 GHz.', 'This is the second sentence.']
        )
        self.assertEqual(
            split_sentences('We use IEEE 802.11 and e.g. U.S.A. standards.'),
            ['We use IEEE 802.11 and e.g. U.S.A. standards.'],
        )

    def test_quote_closings_and_paragraph_boundaries(self):
        self.assertEqual(
            split_sentences('他说：“第一句！”然后继续讲解。'),
            ['他说：“第一句！”', '然后继续讲解。'],
        )
        self.assertEqual(
            split_sentences('第一段没有句号\n第二段另起一行'), ['第一段没有句号', '第二段另起一行']
        )
