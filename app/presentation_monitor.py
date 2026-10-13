"""后台检查已保存的 PPT 备注，不更改讲稿或调用语音引擎。"""

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QThread, QTimer, Signal

from app import presentation_link, script_io


def normalized_notes(notes):
    result = {}
    for page, text in notes.items():
        try:
            result[int(page)] = str(text).replace('\r\n', '\n').strip()
        except (TypeError, ValueError):
            continue
    return result


def changed_pages(before, after):
    before, after = normalized_notes(before), normalized_notes(after)
    return sorted(page for page in before.keys() | after.keys()
                  if before.get(page) != after.get(page))


def file_stamp(path):
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_size


class PresentationCheckTask(QThread):
    checked = Signal(object)

    def run(self):
        try:
            stamp = file_stamp(self.path)
            data = script_io.load_script(self.path)
            link = presentation_link.describe(self.path)
            if stamp != file_stamp(self.path):
                return  # 保存尚未完成，下次轮询再读。
            self.checked.emit((self.epoch, stamp, data, link))
        except Exception:
            pass  # 文件可能正在替换或被占用，不打断播放；下次继续检查。


class PresentationMonitor(QObject):
    change_detected = Signal(object)
    unchanged = Signal()
    availability_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.baseline = None
        self.fallback = {}
        self.expected_sha = ''
        self.stamp = None
        self.epoch = 0
        self.can_check = lambda: True
        self._available = None
        self._pending = False
        self._received = False
        self._retries = 0
        self._closing = False
        self.task = PresentationCheckTask(self)
        self.task.checked.connect(self._checked)
        self.task.finished.connect(self._after_check)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._filesystem_changed)
        self.watcher.directoryChanged.connect(self._filesystem_changed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.poll)

    def bind(self, path, *, baseline=None, expected_sha='', fallback=None):
        self.epoch += 1
        self.path = (Path(path).resolve() if path and Path(path).suffix.lower()
                     in presentation_link.PPT_SUFFIXES else None)
        self.baseline = dict(baseline) if isinstance(baseline, dict) else None
        self.fallback = dict(fallback or {})
        self.expected_sha = expected_sha
        self.stamp = None
        self._pending = False
        self._retries = 0
        self.timer.stop()
        self._sync_watches()
        if self.path:
            self.timer.start(0)

    def _sync_watches(self):
        """同时监视文件和所在目录，兼容 Office 替换文件式保存。"""
        desired_files, desired_dirs = set(), set()
        available = False
        if self.path:
            available = self.path.is_file()
            if available:
                desired_files.add(str(self.path))
            directory = self.path.parent
            # 目录整体被移走时，监视其父目录以等待恢复。
            for _ in range(3):
                if directory.is_dir():
                    desired_dirs.add(str(directory))
                    break
                directory = directory.parent
        existing = set(self.watcher.files()) | set(self.watcher.directories())
        desired = desired_files | desired_dirs
        if existing - desired:
            self.watcher.removePaths(list(existing - desired))
        if desired - existing:
            self.watcher.addPaths(list(desired - existing))
        if not available:
            self.stamp = None
        if self._available != available:
            self._available = available
            self.availability_changed.emit(available)

    def _filesystem_changed(self, _path):
        if self._closing:
            return
        self._sync_watches()
        try:
            stamp = file_stamp(self.path) if self.path else None
        except OSError:
            stamp = None
        if stamp != self.stamp:
            self._retries = 0
            self.timer.start(300)

    def poll(self):
        self.timer.stop()
        if self._closing or not self.path:
            return
        self._sync_watches()
        if self.path.suffix.lower() != '.pptx':
            return
        if self.task.isRunning() or not self.can_check():
            self._pending = True
            return
        self._pending = False
        try:
            if file_stamp(self.path) == self.stamp:
                return
        except OSError:
            return
        self.task.path, self.task.epoch = self.path, self.epoch
        self._received = False
        self.task.start()

    def _checked(self, result):
        epoch, stamp, data, link = result
        if epoch != self.epoch or not self.can_check():
            self._pending = True
            return  # 已切换讲稿或开始生成，稍后按当前状态重查。
        try:
            if file_stamp(self.path) != stamp:
                self._pending = True
                return
        except OSError:
            self._sync_watches()
            return
        self._received = True
        self.stamp = stamp
        if self.baseline is None:
            if self.expected_sha and link['sha256'] != self.expected_sha:
                self.baseline = self.fallback
            else:
                self.baseline = dict(data.notes)
        if changed_pages(self.baseline, data.notes):
            self.change_detected.emit((data, link))
        else:
            self.unchanged.emit()

    def _after_check(self):
        if self._closing or not self.path or not self._available or not self.can_check():
            return
        if self._pending or self.task.epoch != self.epoch:
            self.timer.start(0)
        elif not self._received and self._retries < 3:
            # 保存时文件短暂不可读，有限重试；空闲时不轮询。
            self._retries += 1
            self.timer.start(500)

    def shutdown(self):
        self._closing = True
        self.timer.stop()
        if not self.task.wait(5000):
            self._closing = False
            return False
        return True
