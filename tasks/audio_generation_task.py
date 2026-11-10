"""在后台批量生成讲稿音频，并维护缓存与时长元数据。"""

import shutil
import threading
import uuid
import wave
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from PySide6.QtCore import QThread, Signal

from app import paths
from app.audio import processing as audio_processing
from app.audio import segments as segment_audio
from app.audio.captions import SENTENCE_MODE, split_sentences
from app.audio.concurrency import cache_lock, check_cancelled, completed_map
from app.audio.sentence_cache import cache_key as sentence_key
from app.audio.sentences import SentenceGenerationError, load_sentences
from app.logging_setup import get_logger
from tts_engine import TTSEngine

# 空白讲稿仍需占据一个播放槽位，才能维持“讲稿段—音频—翻页动作”的一一对应；
# 使用短静音还可避免在线引擎对空文本返回无效文件。
SILENCE_SECONDS = 0.4
SILENCE_FRAMERATE = 22050

# MPEG-1 Layer III 单声道 32 kbps / 44.1 kHz 的帧头，其余字节置零即为一帧静音。
# 项目没有音频编码依赖，靠拼接这种帧得到与在线引擎同格式的静音占位。
MP3_SILENCE_HEADER = bytes((0xFF, 0xFB, 0x10, 0xC4))
MP3_FRAME_BYTES = 144 * 32000 // 44100
MP3_FRAME_SECONDS = 1152 / 44100


@dataclass
class GenerationResult:
    """汇总一次批量生成的有序产物和缓存命中信息。"""

    media_paths: list = field(default_factory=list)
    durations: list = field(default_factory=list)
    cache_keys: list = field(default_factory=list)
    cache_exts: list = field(default_factory=list)
    cache_hit_count: int = 0
    generation_profile: dict = field(default_factory=dict)
    speaker: str = ''
    audio_edits: dict = field(default_factory=dict)
    sentence_count: int = 0
    sentence_cache_hit_count: int = 0


class AudioGenerationTask(QThread):
    """在后台批量生成讲稿音频，并保持结果与输入索引对齐。"""

    signal_import_index = Signal(int)
    signal_finish = Signal(object)
    signal_error = Signal(str)
    signal_progress = Signal(object)

    def __init__(self, tts_engine: TTSEngine, parent=None):
        super().__init__(parent)
        self.tts_engine = tts_engine
        self.source_engine = tts_engine
        self.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.output_path = paths.TEMP_DIR

        # 输入在 start() 前复制进任务，后台线程不读取任何界面控件。
        self.notes_list: list[dict] = []
        self.force_regenerate = False
        self.audio_edits = {}
        self._progress_lock = threading.Lock()
        self._progress = None
        self._stopped = threading.Event()
        self._generated = set()
        self._sentences_prepared = False
        self._in_batch = False

    def configure(self, notes_list: list[dict], *, force_regenerate: bool = False, audio_edits=None):
        """复制本次任务输入，并设置是否绕过已有缓存。

        ``force_regenerate`` 只跳过命中判断，仍按稳定缓存键覆盖原缓存文件。
        """
        self.notes_list = list(notes_list)
        self.tts_engine = self.source_engine.snapshot()
        self.force_regenerate = bool(force_regenerate)
        self.audio_edits = dict(audio_edits or {})
        self._progress = None
        self._stopped.clear()
        self._generated.clear()
        self._sentences_prepared = False

    def _cancelled(self):
        return self._stopped.is_set() or self.isInterruptionRequested()

    def run(self):
        """生成正文，并将异常转换为线程信号。"""
        try:
            result = self.save_wav()
            if self.isInterruptionRequested():
                return
        except InterruptedError:
            return
        except Exception as e:
            get_logger().exception('讲稿配音生成失败')
            self.signal_error.emit(str(e))
            return

        self.signal_finish.emit(result)

    # 单条音频生成

    @staticmethod
    def _write_silence(path: Path, output_ext: str, seconds: float = SILENCE_SECONDS,
                       framerate: int = SILENCE_FRAMERATE):
        """按当前引擎的输出格式写入静音占位。

        占位与正文音频同格式，合并导出时不会出现 wav 与 mp3 混用。
        """
        if output_ext == 'mp3':
            frame = MP3_SILENCE_HEADER + bytes(MP3_FRAME_BYTES - len(MP3_SILENCE_HEADER))
            path.write_bytes(frame * max(1, round(seconds / MP3_FRAME_SECONDS)))
            return

        with wave.open(str(path), 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(framerate)
            wav_file.writeframes(b'\x00\x00' * int(seconds * framerate))

    def _generate_to_cache(self, text: str, cache_path: Path, output_ext: str, sentence_progress=None):
        """先生成临时文件，再原子写入缓存，并在失败后清理半成品。"""
        temp_path = cache_path.with_name(f'{cache_path.stem}.{uuid.uuid4().hex}.tmp.{output_ext}')
        try:
            if text.strip():
                options = self.tts_engine.get_current_option_values()
                cancellation = ({'cancelled': self._cancelled,
                                 'sentence_cache_dir': self.audio_cache_path,
                                 'force_sentence_regeneration': self.force_regenerate and not self._sentences_prepared,
                                 'sentence_progress': None if self._sentences_prepared else sentence_progress}
                                if options.get('sentence_mode') == SENTENCE_MODE else {'cancelled': self._cancelled})
                self.tts_engine.save_file(text, str(temp_path), **cancellation)
            else:
                # Edge 启用留白后为 24 kHz WAV，空讲稿占位也必须匹配。
                framerate = 24000 if self.tts_engine.get_mode() == 'edge' and output_ext == 'wav' else SILENCE_FRAMERATE
                self._write_silence(temp_path, output_ext, framerate=framerate)
                options = self.tts_engine.get_current_option_values()
                leading = options.get('leading_silence_ms', 0)
                trailing = options.get('trailing_silence_ms', 0)
                if leading or trailing:
                    content = audio_processing.process_wav(temp_path.read_bytes(),
                                                           leading_silence_ms=leading,
                                                           trailing_silence_ms=trailing)
                    temp_path.write_bytes(content)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise RuntimeError('未生成可播放的配音，请重试或更换音色。')
            check_cancelled(self._cancelled)
            temp_path.replace(cache_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    get_logger().warning('生成后清理临时文件失败：%s', temp_path.name, exc_info=True)

    def _cache_reference(self, index, note_dict, generation_profile):
        text = note_dict['text']
        output_ext = self.tts_engine.get_output_extension()
        cache_key = self.tts_engine.build_audio_cache_key(text, generation_profile)
        cache_path = self.audio_cache_path / f'{cache_key}.{output_ext}'
        edit = self.audio_edits.get(index, {})
        if (not self.force_regenerate and edit.get('text') == text
                and edit.get('profile_id') == segment_audio.profile_id(generation_profile)):
            try:
                tuned = segment_audio.cache_path(self.audio_cache_path,
                                                 {'key': edit['audio_key'], 'ext': edit['audio_ext']})
                if tuned.is_file() and tuned.stat().st_size:
                    cache_path, cache_key, output_ext = tuned, edit['audio_key'], edit['audio_ext']
            except (KeyError, ValueError, OSError):
                get_logger().warning('第 %s 页第 %s 段已调整配音不可用，将重新生成',
                                     note_dict['page'], index + 1, exc_info=True)
        return cache_path, cache_key, output_ext

    def _save_one_note_wav(self, index, note_dict, generation_profile):
        """缓存检查、生成及复制共用一把锁，避免替换正在读取的 Windows 文件。"""
        check_cancelled(self._cancelled)
        cache_path, cache_key, output_ext = self._cache_reference(index, note_dict, generation_profile)
        with cache_lock(cache_path, self._cancelled):
            return self._save_locked_note(index, note_dict, cache_path, cache_key, output_ext)

    def _save_locked_note(self, index, note_dict, cache_path, cache_key, output_ext):
        text = note_dict['text']
        path = self.output_path / f'{note_dict["page"]}_{index + 1}.{output_ext}'
        with self._progress_lock:
            already_generated = cache_path in self._generated
        cache_hit = ((not self.force_regenerate or self._in_batch and already_generated)
                     and cache_path.exists() and cache_path.stat().st_size > 0)
        if not cache_hit:
            try:
                self._generate_to_cache(
                    text, cache_path, output_ext,
                    lambda number, total, reused: self._report_sentence(index, number, reused, total))
                with self._progress_lock:
                    self._generated.add(cache_path)
            except InterruptedError:
                raise
            except Exception as exc:
                number = sum(item['page'] == note_dict['page'] for item in self.notes_list[:index]) + 1
                location = f'第 {note_dict["page"]} 页 · 第 {number} 段'
                if isinstance(exc, SentenceGenerationError):
                    location += f' · 第 {exc.number} 句'
                    detail = exc.detail
                else:
                    detail = str(exc)
                raise RuntimeError(f'{location}：{detail}') from exc

        sentence_count = None
        if cache_hit and self._progress is not None and self._progress['short']:
            timeline = load_sentences(cache_path)
            sentence_count = len(timeline) if timeline else len(split_sentences(text))
        self._report_sentence(index, None, cache_hit, sentence_count)

        shutil.copy2(cache_path, path)
        duration = self.get_audio_duration(path)
        return index, path, duration, cache_key, output_ext, cache_hit

    @staticmethod
    def get_audio_duration(path: Path) -> float:
        """读取 WAV 或 MP3 时长；解析失败时记录诊断并返回 0。"""
        suffix = path.suffix.lower()
        try:
            if suffix == '.wav':
                with wave.open(str(path), 'rb') as wav_file:
                    return wav_file.getnframes() / float(wav_file.getframerate())

            audio = MP3(str(path)) if suffix == '.mp3' else MutagenFile(str(path))
            length = float(getattr(getattr(audio, 'info', None), 'length', 0.0))
            if length > 0:
                return length
        except Exception:
            get_logger().warning('无法读取音频时长：%s', path.name, exc_info=True)
            return 0.0

        get_logger().warning('无法读取音频时长：%s', path.name)
        return 0.0

    # 批量生成

    def _report_sentence(self, index, number, reused, total=None):
        if self._progress is None:
            return
        with self._progress_lock:
            state = self._progress
            if total is not None:
                state['totals'][index] = max(1, total)
                state['sentence_counts'][index] = total
            target = state['totals'][index] if number is None else number
            delta = max(0, target - state['done'][index])
            state['done'][index] += delta
            if reused and state['sentence_counts'][index]:
                state['reused'] += delta
            note = self.notes_list[index]
            self.signal_progress.emit({
                'page': note['page'], 'segment': state['numbers'][index],
                'sentence': target, 'sentences': state['totals'][index],
                'completed': sum(state['done']), 'total': sum(state['totals']),
                'short': state['short'],
                'blank': not bool(note['text'].strip()),
            })

    def save_wav(self) -> GenerationResult:
        self._generated.clear()
        self._stopped.clear()
        self._in_batch = True
        session = (self.tts_engine.synthesis_session(self._cancelled)
                   if isinstance(self.tts_engine, TTSEngine) else nullcontext())
        try:
            with session:
                return self._save_batch()
        finally:
            self._in_batch = self._sentences_prepared = False

    def _save_batch(self):
        """按引擎能力串行或并行生成全部讲稿音频。"""
        notes_list = self.notes_list
        total = len(notes_list)
        result = GenerationResult(
            media_paths=[None] * total,
            durations=[0.0] * total,
            cache_keys=[''] * total,
            cache_exts=[''] * total,
        )
        self.tts_engine.validate_generation_settings()
        generation_profile = self.tts_engine.get_generation_profile()
        short = generation_profile.get('options', {}).get('sentence_mode') == SENTENCE_MODE
        numbers, counts = [], {}
        for note in notes_list:
            counts[note['page']] = counts.get(note['page'], 0) + 1
            numbers.append(counts[note['page']])
        sentence_counts = [len(split_sentences(note['text'])) if short else 0 for note in notes_list]
        self._progress = {
            'short': short, 'numbers': numbers, 'done': [0] * total, 'reused': 0,
            'totals': [max(1, count) for count in sentence_counts],
            'sentence_counts': sentence_counts,
        }
        result.generation_profile = generation_profile
        result.speaker = self.tts_engine.get_selected_voice_name()

        def collect(item):
            index, path, duration, cache_key, cache_ext, cache_hit = item
            result.media_paths[index] = path
            result.durations[index] = duration
            result.cache_keys[index] = cache_key
            result.cache_exts[index] = cache_ext
            if cache_hit:
                result.cache_hit_count += 1
            edit = self.audio_edits.get(index, {})
            if edit.get('audio_key') == cache_key and edit.get('audio_ext') == cache_ext:
                result.audio_edits[index] = edit

        workers = self.tts_engine.get_parallel_workers() if self.tts_engine.can_parallel_generate() else 1
        workers = max(1, workers)
        if short and isinstance(self.tts_engine, TTSEngine):
            self._prepare_sentences(workers, generation_profile)
        items = enumerate(notes_list)
        for completed, item in enumerate(completed_map(
                lambda pair: self._save_one_note_wav(*pair, generation_profile), items,
                1 if self._sentences_prepared else min(workers, total or 1),
                self._cancelled, self._stopped.set), 1):
            collect(item)
            self.signal_import_index.emit(completed)

        if short:
            result.sentence_count = sum(self._progress['sentence_counts'])
            result.sentence_cache_hit_count = self._progress['reused']
        return result

    def _prepare_sentences(self, workers, generation_profile):
        profile = self.tts_engine._sentence_profile(self.tts_engine.get_mode(), None, None, None, {})
        pending = {}
        for index, note in enumerate(self.notes_list):
            path, _, _ = self._cache_reference(index, note, generation_profile)
            if not self.force_regenerate and path.is_file() and path.stat().st_size:
                continue
            for number, text in enumerate(split_sentences(note['text']), 1):
                key = sentence_key(text, profile)
                pending.setdefault(key, (text, []))[1].append((index, number))

        def prepare(item):
            text, targets = item
            try:
                _, _, reused = self.tts_engine.prepare_sentence(
                    text, sentence_cache_dir=self.audio_cache_path, cancelled=self._cancelled,
                    force_sentence_regeneration=self.force_regenerate)
            except InterruptedError:
                raise
            except Exception as exc:
                index, number = targets[0]
                raise RuntimeError(f'第 {self.notes_list[index]["page"]} 页 · '
                                   f'第 {self._progress["numbers"][index]} 段 · 第 {number} 句：{exc}') from exc
            return targets, reused

        completed = [0] * len(self.notes_list)
        for targets, reused in completed_map(prepare, pending.values(), min(workers, len(pending) or 1),
                                             self._cancelled, self._stopped.set):
            for position, (index, _) in enumerate(targets):
                completed[index] += 1
                self._report_sentence(index, completed[index], reused or position > 0)
        self._sentences_prepared = True
