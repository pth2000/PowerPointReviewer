"""验证任务有界派发、缓存互斥、请求节流与会话回收。"""

import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from test_subtitle_sentences import wav_content

from app import paths
from app.audio.captions import SENTENCE_MODE, read_captions
from app.audio.concurrency import (
    RequestGate,
    SynthesisHttpError,
    SynthesisSession,
    completed_map,
    interruptible_wait,
    retry_after,
)
from tasks.audio_generation_task import AudioGenerationTask
from tts_engine import TTSEngine


class DispatchTests(unittest.TestCase):
    def test_failure_stops_dispatch_before_the_remaining_document(self):
        barrier = threading.Barrier(4, timeout=3)
        stopped = threading.Event()
        started = []

        def generate(index):
            started.append(index)
            barrier.wait()
            if index == 0:
                raise RuntimeError('service unavailable')
            self.assertTrue(stopped.wait(3))
            return index

        with self.assertRaisesRegex(RuntimeError, 'service unavailable'):
            list(completed_map(generate, range(16), 4, stopped.is_set, stopped.set))
        self.assertCountEqual(started, range(4))

    def test_cancellation_does_not_submit_more_work(self):
        cancelled = threading.Event()
        started = []

        def generate(index):
            started.append(index)
            cancelled.set()
            return index

        with self.assertRaises(InterruptedError):
            list(completed_map(generate, range(10), 1, cancelled.is_set, cancelled.set))
        self.assertEqual(started, [0])

    def test_request_gate_limits_simultaneous_requests_and_start_rate(self):
        gate = RequestGate(2, 0.02)
        starts = []
        state = {'active': 0, 'peak': 0}
        lock = threading.Lock()

        def request(_index):
            with gate.slot(lambda: False):
                with lock:
                    starts.append(time.monotonic())
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                time.sleep(0.07)
                with lock:
                    state['active'] -= 1

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(request, range(6)))
        self.assertEqual(state['peak'], 2)
        self.assertTrue(all(b - a >= 0.018 for a, b in zip(starts, starts[1:])))

    def test_cancel_interrupts_backoff_and_throttled_gate(self):
        for operation in (lambda cancel: interruptible_wait(60, cancel),
                          lambda cancel: self._enter_throttled_gate(cancel)):
            with self.subTest(operation=operation):
                cancelled = threading.Event()
                timer = threading.Timer(0.03, cancelled.set)
                timer.start()
                try:
                    start = time.monotonic()
                    with self.assertRaises(InterruptedError):
                        operation(cancelled.is_set)
                    self.assertLess(time.monotonic() - start, 1)
                finally:
                    timer.join()

    @staticmethod
    def _enter_throttled_gate(cancelled):
        gate = RequestGate(2, 0)
        gate.throttle(60)
        with gate.slot(cancelled):
            raise AssertionError('request entered a blocked gate')

    def test_http_sessions_are_reused_per_worker_and_closed(self):
        session = SynthesisSession(RequestGate(2, 0), lambda: False)
        barrier = threading.Barrier(2, timeout=3)

        def request(_index):
            client = session.http()
            barrier.wait()
            self.assertIs(session.http(), client)
            return client

        with patch('requests.Session', side_effect=lambda: Mock()) as factory:
            with ThreadPoolExecutor(max_workers=2) as executor:
                clients = list(executor.map(request, range(2)))
        self.assertEqual(factory.call_count, 2)
        self.assertIsNot(clients[0], clients[1])
        session.close()
        for client in clients:
            client.close.assert_called_once()


class RetryTests(unittest.TestCase):
    def test_http_errors_retry_only_when_recoverable(self):
        for status, attempts in ((400, 1), (401, 1), (403, 1), (429, 3), (503, 3)):
            with self.subTest(status=status):
                engine = TTSEngine()
                response = Mock(status_code=status, headers={'Retry-After': '2'})
                gate = Mock()
                gate.slot.side_effect = lambda _cancel: nullcontext()
                with patch.object(engine, '_request_gate', return_value=gate), \
                     patch.object(engine, '_synthesize_once', side_effect=SynthesisHttpError('test', response)) as send, \
                     patch('tts_engine.interruptible_wait') as pause:
                    with self.assertRaises(RuntimeError):
                        engine._synthesize_file_by_mode('edge', '测试', 'unused.mp3', retry_times=2)
                self.assertEqual(send.call_count, attempts)
                self.assertEqual(pause.call_count, attempts - 1)
                self.assertEqual(gate.throttle.call_count, 2 if status == 429 else 0)
                if attempts > 1:
                    self.assertTrue(all(call.args[0] >= 2 for call in pause.call_args_list))

    def test_retry_after_supports_fractional_seconds_and_sdk_responses(self):
        error = SynthesisHttpError('test', Mock(status_code=429, headers={'Retry-After': '0.25'}))
        self.assertEqual(retry_after(error), 0.25)
        error.response = {'status_code': 429}
        self.assertEqual(retry_after(error), 0)


class BatchConcurrencyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.output = self.root / 'output'
        self.output.mkdir()
        for name, value in (('AUDIO_CACHE_DIR', self.cache), ('SESSION_DIR', self.root / 'sessions')):
            patcher = patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = TTSEngine()
        self.engine.set_mode('edge')
        self.calls = []
        self.active = self.peak = 0
        self.lock = threading.Lock()

        def synthesize(_mode, text, target, *_args, **_kwargs):
            with self.lock:
                self.calls.append(text)
                self.active += 1
                self.peak = max(self.active, self.peak)
            # 不同长度让后派发的请求先完成，验证成品仍按输入顺序组装。
            time.sleep(0.04 if text.startswith('第一') else 0.015)
            if self.engine.get_current_option_values().get('sentence_mode') != SENTENCE_MODE:
                AudioGenerationTask._write_silence(Path(target), 'mp3')
            else:
                Path(target).write_bytes(wav_content(24000 if text.startswith('第一') else 12000))
            with self.lock:
                self.active -= 1

        for patcher in (
            patch.object(self.engine, '_synthesize_file_by_mode', side_effect=synthesize),
            patch('app.audio.decode.decode_wav', side_effect=lambda source, target, *_a, **_k: shutil.copyfile(source, target)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def generate(self, notes, force=False):
        task = AudioGenerationTask(self.engine)
        task.output_path = self.output
        task.notes_list = notes
        task.force_regenerate = force
        return task.save_wav()

    def test_identical_paragraphs_generate_once_including_forced_batches(self):
        notes = [{'page': i + 1, 'text': '相同讲稿'} for i in range(10)]
        for force in (False, True):
            with self.subTest(force=force):
                self.calls.clear()
                result = self.generate(notes, force)
                self.assertEqual(self.calls, ['相同讲稿'])
                self.assertEqual(result.cache_hit_count, 9)
                self.assertEqual(len(set(result.media_paths)), 10)
                self.assertTrue(all(path.read_bytes() == result.media_paths[0].read_bytes()
                                    for path in result.media_paths))

    def test_single_paragraph_sentences_run_concurrently_and_merge_in_order(self):
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        result = self.generate([{'page': 1, 'text': '第一句。第二句。第三句。第四句。'}])
        self.assertGreater(self.peak, 1)
        self.assertLessEqual(self.peak, 4)
        cues = read_captions(result.media_paths[0])['cues']
        self.assertEqual([cue['text'] for cue in cues], ['第一句。', '第二句。', '第三句。', '第四句。'])
        self.assertEqual([cue['end'] - cue['start'] for cue in cues], [24000, 12000, 12000, 12000])
        self.assertEqual(result.sentence_count, 4)
        self.assertEqual(result.sentence_cache_hit_count, 0)

    def test_repeated_sentences_share_one_request_even_when_force_regenerating(self):
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        notes = [{'page': 1, 'text': '第一句。第二句。'}, {'page': 2, 'text': '第二句。第一句。'}]
        for force in (False, True):
            with self.subTest(force=force):
                self.calls.clear()
                result = self.generate(notes, force)
                self.assertCountEqual(self.calls, ['第一句。', '第二句。'])
                self.assertEqual(result.sentence_cache_hit_count, 2)
                for index, expected in enumerate((['第一句。', '第二句。'], ['第二句。', '第一句。'])):
                    self.assertEqual([cue['text'] for cue in read_captions(result.media_paths[index])['cues']], expected)

    def test_failure_releases_sessions(self):
        with patch.object(self.engine, '_synthesize_file_by_mode', side_effect=RuntimeError('test failure')):
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                self.generate([{'page': 1, 'text': '正文'}])
        self.assertIsNone(self.engine._synthesis_session)
        self.assertFalse(list(self.cache.glob('*.tmp.*')))
