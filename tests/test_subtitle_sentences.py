"""短句配音的分段、时间保存、效果处理和字幕导出。"""

import json
import shutil
import struct
import tempfile
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app.audio.captions import (
    CONTINUOUS_MODE,
    SENTENCE_MODE,
    read_captions,
    split_sentences,
    transfer_captions,
    write_captions,
)
from app.audio.processing import process_wav
from app.audio.segments import process_wav as process_segment
from app.documents.export import build_srt
from app.documents.package import export_package, import_package
from app.video.mux import VideoCaption, build_video_captions
from app.video.timeline import VideoTimeline
from tts_engine import TTSEngine


def wav_content(frames=48000, rate=48000):
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b'\x00\x10' * frames)
    return output.getvalue()


class SentenceCaptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cues = [
            {'start': 0, 'end': 12000, 'text': '第一句。'},
            {'start': 12000, 'end': 48000, 'text': '第二句。'},
        ]
        self.content = write_captions(wav_content(), self.cues)

    def test_split_preserves_text_words_units_and_avoids_stranded_punctuation(self):
        text = (
            '本系统采用IEEE 802.11协议进行无线通信，频率为3.5 GHz。'
            '然后分析复杂环境下的传输质量及影响因素。'
        )
        parts = split_sentences(text)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual(''.join(''.join(parts).split()), ''.join(text.split()))
        self.assertTrue(any('3.5 GHz' in part for part in parts))
        self.assertTrue(any('IEEE' in part for part in parts))
        self.assertFalse(any(part[0] in '，。！？；：、' for part in parts))
        self.assertEqual(split_sentences('第一句。第二句！'), ['第一句。', '第二句！'])
        self.assertEqual(split_sentences('  \n '), [])

    def test_long_text_and_long_identifiers_are_never_lost(self):
        for text in ('测试文字' * 500, 'SuperLongTechnicalIdentifier' * 8, '测试，' * 150):
            parts = split_sentences(text)
            self.assertEqual(''.join(parts), text)
            self.assertTrue(all(parts))

    def test_metadata_round_trip_preserves_pcm_and_replaces_previous_chunk(self):
        for source in (self.content, write_captions(self.content, self.cues)):
            self.assertEqual(read_captions(source)['cues'], self.cues)
            with wave.open(BytesIO(source), 'rb') as reader:
                self.assertEqual(reader.getnframes(), 48000)
                self.assertEqual(reader.readframes(48000), b'\x00\x10' * 48000)
            self.assertEqual(source.count(b'pprs'), 1)
        self.assertIsNone(read_captions(b'not an audio file'))
        self.assertIsNone(read_captions(self.root / 'missing.wav'))
        self.assertIsNone(read_captions(wav_content()))

    def test_invalid_metadata_cannot_overrun_audio_or_overlap(self):
        for cues in (
            [{'start': 0, 'end': 48001, 'text': '超出范围'}],
            [self.cues[0], {'start': 100, 'end': 24000, 'text': '重叠'}],
            [{'start': False, 'end': 20, 'text': '错误类型'}],
        ):
            with self.subTest(cues=cues), self.assertRaises(ValueError):
                write_captions(wav_content(), cues)
        payload = json.dumps({'version': 1, 'rate': 48000, 'frames': 1, 'cues': self.cues}).encode()
        audio = wav_content()
        block = b'pprs' + struct.pack('<I', len(payload)) + payload
        corrupted = audio[:4] + struct.pack('<I', len(audio) + len(block) - 8) + audio[8:] + block
        self.assertIsNone(read_captions(corrupted))

    def test_speed_padding_gain_and_resampling_keep_measured_boundaries(self):
        fast = read_captions(process_wav(self.content, speed=2))
        self.assertEqual(
            [(cue['start'], cue['end']) for cue in fast['cues']], [(0, 6000), (6000, 24000)]
        )
        padded = read_captions(
            process_segment(
                self.content, {'speed': 1, 'volume': 50, 'leading_ms': 200, 'trailing_ms': 300}
            )
        )
        self.assertEqual(
            [(cue['start'], cue['end']) for cue in padded['cues']], [(9600, 21600), (21600, 57600)]
        )
        converted = read_captions(transfer_captions(self.content, wav_content(24000, 24000)))
        self.assertEqual(converted['rate'], 24000)
        self.assertEqual(converted['cues'][-1]['end'], 24000)

    def test_video_and_srt_use_sentence_durations_and_original_click_count(self):
        source = self.root / '讲稿.wav'
        source.write_bytes(self.content)
        notes = [{'page': 1, 'text': '第一句。第二句。'}]
        timeline = VideoTimeline([1], [source], wait_ms=700)
        captions = build_video_captions(notes, timeline)
        self.assertEqual(len(timeline.cues), 1)
        self.assertEqual(
            captions, (VideoCaption(700, 950, '第一句。'), VideoCaption(950, 1700, '第二句。'))
        )
        srt = build_srt(notes, [1], [source])
        self.assertIn('00:00:00,000 --> 00:00:00,250\n第一句。', srt)
        self.assertIn('00:00:00,250 --> 00:00:01,000\n第二句。', srt)
        self.assertEqual(len(build_video_captions([{'page': 1, 'text': '修改后'}], timeline)), 1)

    def test_project_package_keeps_timing_inside_audio_without_sidecar_files(self):
        from app import paths

        cache, sessions = self.root / 'cache', self.root / 'sessions'
        cache.mkdir()
        sessions.mkdir()
        source = cache / 'sentence.wav'
        source.write_bytes(self.content)
        package = self.root / '讲稿.pprj'
        item = {
            'page': 1,
            'index': 0,
            'text': '第一句。第二句。',
            'duration': 1,
            'cache_key': 'sentence',
            'cache_ext': 'wav',
            'media': str(source),
        }
        with (
            patch.object(paths, 'AUDIO_CACHE_DIR', cache),
            patch.object(paths, 'SESSION_DIR', sessions),
            patch.object(paths, 'ensure_runtime_dirs'),
        ):
            export_package(
                package,
                items=[item],
                notes={1: item['text']},
                mark='●',
                source_name='讲稿',
                generation_profile={},
                speaker='测试音色',
            )
            source.unlink()
            restored = import_package(package)
        self.assertEqual(restored['restored'], 1)
        self.assertEqual(read_captions(source)['cues'], self.cues)
        self.assertEqual(list(cache.iterdir()), [source])


class SentenceGenerationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.engine = TTSEngine()
        self.engine.set_mode('edge')
        cache = patch('app.paths.AUDIO_CACHE_DIR', self.root / 'cache')
        cache.start()
        self.addCleanup(cache.stop)

    def test_default_preserves_existing_cache_and_output_format(self):
        before = self.engine.get_generation_profile()
        self.assertNotIn('sentence_mode', before['options'])
        self.assertEqual(self.engine.get_output_extension(), 'mp3')
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        self.assertEqual(self.engine.get_output_extension(), 'wav')
        self.assertNotEqual(self.engine.get_generation_profile(), before)
        self.engine.set_current_option('sentence_mode', CONTINUOUS_MODE)
        self.assertEqual(self.engine.get_generation_profile(), before)

    def test_generation_uses_actual_sentence_lengths_and_persists_choice(self):
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        calls = []

        def synthesize(_mode, text, path, *_args, **kwargs):
            calls.append(text)
            self.assertEqual(kwargs['leading_silence_ms'], 0)
            Path(path).write_bytes(wav_content(12000 if len(calls) == 1 else 36000))

        def decode(source, target, *_args, **_kwargs):
            shutil.copyfile(source, target)

        target = self.root / '配音.wav'
        with (
            patch.object(self.engine, '_synthesize_file_by_mode', side_effect=synthesize),
            patch('app.audio.decode.decode_wav', side_effect=decode),
        ):
            self.engine.save_file('第一句。第二句。', str(target))
        self.assertEqual(calls, ['第一句。', '第二句。'])
        timing = read_captions(target)
        self.assertEqual(
            [(cue['start'], cue['end']) for cue in timing['cues']], [(0, 12000), (12000, 48000)]
        )
        restored = TTSEngine()
        restored.import_persistent_state(self.engine.export_persistent_state())
        self.assertEqual(restored.get_current_option_values()['sentence_mode'], SENTENCE_MODE)

    def test_cancellation_and_failure_do_not_replace_audio(self):
        self.engine.set_current_option('sentence_mode', SENTENCE_MODE)
        target = self.root / '已有.wav'
        target.write_bytes(b'keep existing audio')
        with self.assertRaises(InterruptedError):
            self.engine.save_file('第一句。', str(target), cancelled=lambda: True)
        with patch.object(
            self.engine, '_synthesize_file_by_mode', side_effect=RuntimeError('failed')
        ):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                self.engine.save_file('第一句。', str(target))
        self.assertEqual(target.read_bytes(), b'keep existing audio')
