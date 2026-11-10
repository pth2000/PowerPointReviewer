"""在独立任务中准备片段候选配音，支持 Qt 解码与本地效果处理。"""

import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app import paths
from app.audio import segments as segment_audio
from app.audio.captions import SENTENCE_MODE
from app.audio.decode import decode_wav
from app.logging_setup import get_logger
from tasks.audio_generation_task import AudioGenerationTask


class AudioReviewTask(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, jobs, directory, *, engine=None, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.directory = Path(directory)
        self.engine = engine.snapshot() if engine is not None else None

    def run(self):
        try:
            results = {}
            for count, job in enumerate(self.jobs, 1):
                if self.isInterruptionRequested():
                    return
                prefix = self.directory / uuid.uuid4().hex
                base = Path(job['base_path'])
                engine = job.get('engine') or self.engine
                if engine is not None:
                    engine.validate_generation_settings()
                    base = prefix.with_suffix('.source.' + engine.get_output_extension())
                    text = str(job['spoken_text'])
                    if text.strip():
                        options = engine.get_current_option_values()
                        cancellation = ({'cancelled': self.isInterruptionRequested,
                                         'sentence_cache_dir': paths.AUDIO_CACHE_DIR,
                                         'force_sentence_regeneration': True}
                                        if options.get('sentence_mode') == SENTENCE_MODE else {})
                        engine.save_file(text, str(base), **cancellation)
                    else:
                        AudioGenerationTask._write_silence(base, base.suffix.lstrip('.'))
                decoded = prefix.with_suffix('.decoded.wav')
                decode_wav(base, decoded, self.isInterruptionRequested)
                if self.isInterruptionRequested():
                    return
                target = prefix.with_suffix('.processed.wav')
                target.write_bytes(segment_audio.process_wav(decoded.read_bytes(), job['settings']))
                duration = AudioGenerationTask.get_audio_duration(target)
                if duration <= 0:
                    raise RuntimeError('这段配音为空，请重试。')
                results[job['index']] = {**{key: value for key, value in job.items() if key != 'engine'},
                                         'path': target, 'base_path': base,
                                         'duration': duration,
                                         'base_duration': AudioGenerationTask.get_audio_duration(decoded)}
                self.progress.emit(count, len(self.jobs))
            if not self.isInterruptionRequested():
                self.ready.emit(results)
        except InterruptedError:
            pass
        except Exception as exc:
            get_logger().exception('片段配音处理失败')
            self.failed.emit(str(exc))
