"""在后台批量生成讲稿音频，并维护缓存与时长元数据。"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import traceback
import uuid
import wave

from mutagen import File as MutagenFile
from mutagen.mp3 import MP3

from PySide6.QtCore import QThread, Signal

from app import paths
from tts_engine import TTSEngine

# 空白讲稿仍需占据一个播放槽位，才能维持“讲稿段—音频—翻页动作”的一一对应；
# 使用短静音还可避免在线引擎对空文本返回无效文件。
SILENCE_SECONDS = 0.4
SILENCE_FRAMERATE = 22050


@dataclass
class GenerationResult:
    """汇总一次批量生成的有序产物和缓存命中信息。"""

    media_paths: list = field(default_factory=list)
    durations: list = field(default_factory=list)
    cache_keys: list = field(default_factory=list)
    cache_exts: list = field(default_factory=list)
    cache_hit_count: int = 0


class AudioGenerationTask(QThread):
    """在后台批量生成讲稿音频，并保持结果与输入索引对齐。"""

    signal_import_index = Signal(int)
    signal_finish = Signal(object)
    signal_error = Signal(str)

    def __init__(self, tts_engine: TTSEngine, parent=None):
        super().__init__(parent)
        self.tts_engine = tts_engine
        self.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.countdown_cache_path = paths.COUNTDOWN_CACHE_DIR
        self.output_path = paths.TEMP_DIR

        # 输入在 start() 前复制进任务，后台线程不读取任何界面控件。
        self.notes_list: list[dict] = []
        self.countdown_max_seconds = 0
        self.force_regenerate = False

    def configure(self, notes_list: list[dict], countdown_max_seconds: int,
                  force_regenerate: bool = False):
        """复制本次任务输入，并设置是否绕过已有缓存。

        ``force_regenerate`` 只跳过命中判断，仍按稳定缓存键覆盖原缓存文件。
        """
        self.notes_list = list(notes_list)
        self.countdown_max_seconds = int(countdown_max_seconds)
        self.force_regenerate = bool(force_regenerate)

    def run(self):
        """预热倒计时后生成正文，并将异常转换为线程信号。"""
        try:
            self.save_countdown_wav()
            result = self.save_wav()
        except Exception as e:
            traceback.print_exc()
            self.signal_error.emit(str(e))
            return

        self.signal_finish.emit(result)

    # 单条音频生成

    @staticmethod
    def _write_silence_wav(path: Path, seconds: float = SILENCE_SECONDS,
                           framerate: int = SILENCE_FRAMERATE):
        """写入单声道 16 位静音 WAV，作为空白讲稿占位。"""
        with wave.open(str(path), 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(framerate)
            wav_file.writeframes(b'\x00\x00' * int(seconds * framerate))

    def _generate_to_cache(self, text: str, cache_path: Path, output_ext: str):
        """先生成临时文件，再原子写入缓存，并在失败后清理半成品。"""
        temp_path = cache_path.with_name(f'{cache_path.stem}.{uuid.uuid4().hex}.tmp.{output_ext}')
        try:
            if text.strip():
                self.tts_engine.save_file(text, str(temp_path))
            else:
                self._write_silence_wav(temp_path)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise RuntimeError('语音引擎返回了空音频文件')

            temp_path.replace(cache_path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError as e:
                    print(f'[生成] 清理临时文件失败：{temp_path.name}, 原因: {e}')

    def _save_one_note_wav(self, index, note_dict, generation_profile):
        """生成或复用单条音频，并返回可按输入索引归位的结果元组。"""
        text = note_dict['text']
        # 静音由 wave 模块生成，扩展名必须保持 WAV，不能伪装成在线引擎的 MP3。
        output_ext = 'wav' if not text.strip() else self.tts_engine.get_output_extension()
        cache_key = self.tts_engine.build_audio_cache_key(text, generation_profile)
        cache_path = self.audio_cache_path / f'{cache_key}.{output_ext}'
        path = self.output_path / f'{note_dict["page"]}_{index + 1}.{output_ext}'

        cache_hit = (not self.force_regenerate
                     and cache_path.exists() and cache_path.stat().st_size > 0)
        if not cache_hit:
            self._generate_to_cache(text, cache_path, output_ext)

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
        except Exception as e:
            print(f'[生成] 无法读取音频时长：{path.name}, 原因: {e}')
            return 0.0

        print(f'[生成] 无法读取音频时长：{path.name}')
        return 0.0

    # 批量生成

    def save_wav(self) -> GenerationResult:
        """按引擎能力串行或并行生成全部讲稿音频。"""
        notes_list = self.notes_list
        total = len(notes_list)
        result = GenerationResult(
            media_paths=[None] * total,
            durations=[0.0] * total,
            cache_keys=[''] * total,
            cache_exts=[''] * total,
        )
        generation_profile = self.tts_engine.get_generation_profile()

        def collect(item):
            index, path, duration, cache_key, cache_ext, cache_hit = item
            result.media_paths[index] = path
            result.durations[index] = duration
            result.cache_keys[index] = cache_key
            result.cache_exts[index] = cache_ext
            if cache_hit:
                result.cache_hit_count += 1

        if self.tts_engine.can_parallel_generate() and total > 1:
            max_workers = max(1, min(self.tts_engine.get_parallel_workers(), total))
            completed = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(self._save_one_note_wav, index, note_dict, generation_profile)
                    for index, note_dict in enumerate(notes_list)
                ]
                for future in as_completed(futures):
                    collect(future.result())
                    completed += 1
                    self.signal_import_index.emit(completed)
        else:
            for index, note_dict in enumerate(notes_list):
                collect(self._save_one_note_wav(index, note_dict, generation_profile))
                self.signal_import_index.emit(index + 1)

        return result

    def save_countdown_wav(self):
        """使用本地引擎补齐指定范围内缺失的倒计时音频。

        倒计时是可选功能，生成失败只记录日志；正文合成继续，播放端负责提示缺失。
        """
        for time_num in range(self.countdown_max_seconds, 0, -1):
            path = self.countdown_cache_path / f'{time_num}.wav'
            if path.exists() and path.stat().st_size > 0:
                continue
            try:
                self.tts_engine.save_file_for_stable_local(f'{time_num}', str(path))
            except Exception as e:
                print(f'[生成] 倒计时音频 {time_num} 生成失败，已跳过：{e}')
                return
        print('倒计时生成完成')
