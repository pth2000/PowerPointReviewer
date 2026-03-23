"""音频生成线程模块"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
import wave
from mutagen import File as MutagenFile
from mutagen.mp3 import MP3

from PySide6.QtCore import QThread, Signal

from tts_engine import TTSEngine


class AudioGenerationTask(QThread):
    """生成语音线程"""

    signal_import_index = Signal(int)
    signal_finish = Signal()
    signal_cache_hit_count = Signal(int)  # 新增：传递缓存命中数

    def __init__(self, reviewer_page, tts_engine: TTSEngine, parent=None):
        super().__init__(parent)
        self.reviewer_page = reviewer_page
        self.tts_engine = tts_engine
        self.audio_cache_path = Path('./data/cache/audio_chunks').resolve()
        self.audio_cache_path.mkdir(parents=True, exist_ok=True)

    def run(self):
        """先生成倒计时，再生成正文音频"""
        self.save_countdown_wav()
        self.save_wav()
        self.signal_finish.emit()

    @staticmethod
    def _safe_copy(src: Path, dst: Path):
        """复制缓存文件到会话目录"""
        shutil.copy2(src, dst)

    def _save_one_note_wav(self, index, note_dict, generation_profile):
        """保存单条讲稿并返回索引、音频时长和是否命中缓存的标志"""
        output_ext = self.tts_engine.get_output_extension()
        path = self.reviewer_page.wav_temp_path / f'{note_dict["page"]}_{index + 1}.{output_ext}'
        cache_key = self.tts_engine.build_audio_cache_key(note_dict['text'], generation_profile)
        cache_path = self.audio_cache_path / f'{cache_key}.{output_ext}'

        cache_hit = False
        if cache_path.exists() and cache_path.stat().st_size > 0:
            self._safe_copy(cache_path, path)
            cache_hit = True
        else:
            temp_path = self.audio_cache_path / f'{cache_key}.{index}.tmp.{output_ext}'
            self.tts_engine.save_file(note_dict['text'], str(temp_path))
            temp_path.replace(cache_path)
            self._safe_copy(cache_path, path)

        duration = self.get_audio_duration(path)
        return index, duration, cache_key, output_ext, cache_hit

    @staticmethod
    def get_audio_duration(path: Path) -> float:
        """读取音频时长（支持 wav/mp3）"""
        suffix = path.suffix.lower()
        if suffix == '.wav':
            try:
                with wave.open(str(path), 'rb') as wav_file:
                    return wav_file.getnframes() / float(wav_file.getframerate())
            except Exception:
                pass

        if suffix == '.mp3':
            audio = MP3(str(path))
            if getattr(audio, 'info', None):
                length = float(getattr(audio.info, 'length', 0.0))
                if length > 0:
                    return length

        # 对其他格式尝试 mutagen
        audio = MutagenFile(str(path))
        if getattr(audio, 'info', None):
            length = float(getattr(audio.info, 'length', 0.0))
            if length > 0:
                return length

        raise RuntimeError(f'无法读取音频时长：{path.name}')

    def save_wav(self):
        """调用 TTS 保存文字为 wav"""
        notes_list = self.reviewer_page.notes_list
        total = len(notes_list)
        info_list = [0.0] * total
        cache_key_list = [''] * total
        cache_ext_list = [''] * total
        cache_hit_count = 0
        generation_profile = self.tts_engine.get_generation_profile()

        if self.tts_engine.can_parallel_generate() and total > 1:
            max_workers = max(1, min(self.tts_engine.get_parallel_workers(), total))
            completed = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(self._save_one_note_wav, index, note_dict, generation_profile): index
                    for index, note_dict in enumerate(notes_list)
                }

                for future in as_completed(future_map):
                    index = future_map[future]
                    result_index, duration, cache_key, cache_ext, cache_hit = future.result()
                    info_list[result_index] = duration
                    cache_key_list[result_index] = cache_key
                    cache_ext_list[result_index] = cache_ext
                    if cache_hit:
                        cache_hit_count += 1

                    completed += 1
                    self.signal_import_index.emit(completed)
        else:
            for index, note_dict in enumerate(notes_list):
                result_index, duration, cache_key, cache_ext, cache_hit = self._save_one_note_wav(index, note_dict, generation_profile)
                info_list[result_index] = duration
                cache_key_list[result_index] = cache_key
                cache_ext_list[result_index] = cache_ext
                if cache_hit:
                    cache_hit_count += 1
                self.signal_import_index.emit(index + 1)

        self.reviewer_page.notes_duration_list = info_list
        self.reviewer_page.note_cache_keys = cache_key_list
        self.reviewer_page.note_cache_exts = cache_ext_list
        self.signal_cache_hit_count.emit(cache_hit_count)

    def save_countdown_wav(self):
        """首次预热倒计时缓存（1~最大秒数），后续直接复用"""
        max_seconds = self.reviewer_page.currentSpinBox.maximum()
        for time_num in range(max_seconds, 0, -1):
            path = self.reviewer_page.countdown_wav_temp_path / f'{time_num}.wav'
            if path.exists() and path.stat().st_size > 0:
                continue
            self.tts_engine.save_file_for_stable_local(f'{time_num}', str(path))
        print('倒计时生成完成')
