"""管理可丢弃的临时目录，用进程锁区分活动工作区与异常退出残留。"""

import json
import re
import shutil
import tempfile
import threading
import uuid
import weakref
from pathlib import Path

from PySide6.QtCore import QLockFile

from app import paths
from app.logging_setup import get_logger

_MARKER = '.pptreviewer-workspace.json'
_LOCK = '.pptreviewer-in-use.lock'
_PREFIXES = ('slide-', 'audio-review-', 'voice-preview-', 'video-audio-', 'sentence-',
             'pptreviewer-update-', 'pptreviewer-export-', 'pptreviewer-show-', 'pptreviewer-link-')


def _is_link(path):
    return path.is_symlink() or path.is_junction()


def _metadata(directory):
    if _is_link(directory) or _is_link(directory / _MARKER) or _is_link(directory / _LOCK):
        return None
    try:
        marker = directory / _MARKER
        if marker.stat().st_size > 1024:
            return None
        data = json.loads(marker.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('version') != 1
                or data.get('prefix') not in _PREFIXES
                or not re.fullmatch(r'[a-f0-9]{16}', str(data.get('token', '')))
                or not directory.name.startswith(data['prefix'])
                or not directory.name.endswith('-' + data['token'])):
            return None
        return data
    except (OSError, ValueError):
        return None


def _remove(directory, lock, data):
    """保留归属标记至最后，文件占用导致失败时仍可在下次启动重试。"""
    try:
        if not directory.exists():
            return
        if _is_link(directory) or _metadata(directory) != data:
            raise OSError('临时目录归属已改变，跳过清理。')
        if not lock.isLocked() and not lock.tryLock(0):
            raise OSError('临时目录正在清理，请稍后重试。')
        if _is_link(directory) or _metadata(directory) != data:
            raise OSError('临时目录归属已改变，跳过清理。')
        for child in directory.iterdir():
            if child.name in (_MARKER, _LOCK):
                continue
            if child.is_junction():
                child.rmdir()
            elif child.is_dir() and not _is_link(child):
                if child.resolve().parent != directory.resolve():
                    raise OSError('临时子目录超出了工作区，跳过清理。')
                shutil.rmtree(child)
            else:
                child.unlink()
        lock.unlock()
        (directory / _MARKER).unlink(missing_ok=True)
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            if directory.is_dir() and not _is_link(directory):
                (directory / _MARKER).write_text(json.dumps(data), encoding='utf-8')
            raise
    finally:
        lock.unlock()


def _finalize(directory, lock, data):
    try:
        _remove(directory, lock, data)
    except OSError:
        get_logger().warning('临时目录暂未清理，将在下次启动重试：%s', directory, exc_info=True)


class TemporaryWorkspace:
    """与 TemporaryDirectory 相同的主要接口，目录只存放可重新生成的文件。"""

    def __init__(self, *, prefix, dir=None):
        if prefix not in _PREFIXES:
            raise ValueError('未知的临时目录类型。')
        parent = Path(dir) if dir is not None else paths.TEMP_DIR
        parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:16]
        directory = Path(tempfile.mkdtemp(prefix=prefix, suffix='-' + token, dir=parent)).resolve()
        self.name = str(directory)
        self._data = {'version': 1, 'prefix': prefix, 'token': token}
        self._lock = QLockFile(str(directory / _LOCK))
        self._lock.setStaleLockTime(0)
        if not self._lock.tryLock(0):
            directory.rmdir()
            raise OSError('无法保护临时目录。')
        try:
            (directory / _MARKER).write_text(json.dumps(self._data), encoding='utf-8')
        except Exception:
            self._lock.unlock()
            try:
                (directory / _MARKER).unlink(missing_ok=True)
                directory.rmdir()
            except OSError:
                get_logger().warning('无法移除未初始化的临时目录：%s', directory, exc_info=True)
            raise
        self._finalizer = weakref.finalize(self, _finalize, directory, self._lock, self._data)

    def cleanup(self):
        directory = Path(self.name)
        if not directory.exists():
            self._lock.unlock()
            self._finalizer.detach()
            return
        _remove(directory, self._lock, self._data)
        self._finalizer.detach()

    def __enter__(self):
        return self.name

    def __exit__(self, *_):
        try:
            self.cleanup()
        except OSError:
            get_logger().warning('临时目录暂未清理，将在下次启动重试：%s', self.name, exc_info=True)


def cleanup_stale_workspaces(roots=None):
    """仅扫描应用自己的临时区和分句暂存区，跳过未知目录和活动进程。"""
    roots = roots if roots is not None else (paths.PREVIEW_DIR, paths.TEMP_DIR, paths.AUDIO_CACHE_DIR)
    removed = 0
    for root in dict.fromkeys(Path(root).resolve() for root in roots):
        try:
            directories = tuple(root.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            get_logger().warning('无法检查临时目录：%s', root, exc_info=True)
            continue
        for directory in directories:
            if not directory.name.startswith(_PREFIXES) or not directory.is_dir() or _is_link(directory):
                continue
            if directory.resolve().parent != root:
                continue
            data = _metadata(directory)
            if data is None:
                continue
            lock = QLockFile(str(directory / _LOCK))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                continue
            try:
                _remove(directory, lock, data)
                removed += 1
            except OSError:
                get_logger().warning('残留临时目录仍被占用，将稍后重试：%s', directory, exc_info=True)
    if removed:
        get_logger().info('已清理 %s 个残留临时目录', removed)
    return removed


def start_cleanup():
    """启动一次后台清理，避免历史残留较多时延迟主窗口显示。"""
    thread = threading.Thread(target=cleanup_stale_workspaces, name='temporary-cleanup', daemon=True)
    thread.start()
    return thread
