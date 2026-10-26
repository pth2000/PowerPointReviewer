"""后台扫描历史摘要及音频缓存占用，分批返回结果。"""

from PySide6.QtCore import QThread, Signal

from app.audio import cache as audio_cache
from app.logging_setup import get_logger
from app.project.history_catalog import read_record_summary


class HistoryScanTask(QThread):
    batch_ready = Signal(object)
    cache_ready = Signal(str)
    failed = Signal(str)

    def __init__(self, directory, parent=None, *, records=True):
        super().__init__(parent)
        self.directory, self.records = directory, records

    def run(self):
        try:
            if self.records:
                files = []
                for path in self.directory.glob('*.json'):
                    if self.isInterruptionRequested():
                        return
                    try:
                        files.append((path.stat(), path))
                    except OSError:
                        get_logger().warning('历史记录文件已不可用：%s', path, exc_info=True)
                files.sort(key=lambda item: item[0].st_mtime_ns, reverse=True)
                batch = []
                for stat, path in files:
                    if self.isInterruptionRequested():
                        return
                    batch.append(read_record_summary(path, stat))
                    if len(batch) == 64:
                        self.batch_ready.emit(batch)
                        batch = []
                if batch:
                    self.batch_ready.emit(batch)
            count, size = 0, 0
            for path in audio_cache.iter_cache_files():
                if self.isInterruptionRequested():
                    return
                try:
                    size += path.stat().st_size
                    count += 1
                except OSError:
                    get_logger().warning('音频缓存统计跳过不可用文件：%s', path, exc_info=True)
            self.cache_ready.emit(f'{count} 条 / {audio_cache.format_size(size)}')
        except Exception as exc:
            get_logger().exception('历史记录扫描失败')
            self.failed.emit(str(exc))
