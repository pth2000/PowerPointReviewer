"""可选的 PowerPoint 画面进度监听，失败时不影响导出。"""

import ctypes
from dataclasses import dataclass
import logging

from app.presentation.com import application_pid
from app.video.mux import _library


logger = logging.getLogger('pptreviewer.video.powerpoint')


def powerpoint_process_id(application):
    """缺少 HWND 的隐藏实例只在进程唯一时关联，避免猜测多个实例的归属。"""
    try:
        import win32process
        from app.presentation.window_target import _process_name
        pid = application_pid(application)
        if pid and _process_name(pid).lower() == 'powerpnt.exe':
            return pid
        candidates = [pid for pid in win32process.EnumProcesses()
                      if pid and _process_name(pid).lower() == 'powerpnt.exe']
        return candidates[0] if len(candidates) == 1 else None
    except Exception:
        logger.debug('无法确认 PowerPoint 进度监听的进程', exc_info=True)
        return None


class _Snapshot(ctypes.Structure):
    _fields_ = [('position_us', ctypes.c_int64), ('phase', ctypes.c_int32),
                ('idle_ms', ctypes.c_int32), ('error_code', ctypes.c_uint32),
                ('video_jobs', ctypes.c_uint32)]


@dataclass(frozen=True)
class ExportProgress:
    fraction: float | None = None
    saving: bool = False


class ExportProgressMonitor:
    """启动、读取和关闭均在导出线程执行；本机监听线程不回调 Python。"""

    def __init__(self, pid, duration_us):
        self.pid, self.duration_us = pid, duration_us
        self._library = None
        self._handle = ctypes.c_void_p()

    def start(self):
        if self._handle or not self.pid or self.duration_us <= 0:
            return
        try:
            library = _library()
            library.mm_export_progress_start.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
            library.mm_export_progress_start.restype = ctypes.c_uint32
            library.mm_export_progress_poll.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Snapshot)]
            library.mm_export_progress_poll.restype = ctypes.c_uint32
            library.mm_export_progress_stop.argtypes = [ctypes.c_void_p]
            library.mm_export_progress_stop.restype = ctypes.c_uint32
            self._library = library
            code = library.mm_export_progress_start(self.pid, ctypes.byref(self._handle))
            if code:
                logger.debug('画面进度监听不可用：Windows=%d', code)
                self.close()
        except Exception:
            logger.debug('画面进度监听不可用，继续等待 PowerPoint 导出', exc_info=True)
            self.close()

    def read(self):
        if not self._handle:
            return ExportProgress()
        try:
            data = _Snapshot()
            code = self._library.mm_export_progress_poll(self._handle, ctypes.byref(data))
            if code or data.phase == 3 or data.video_jobs > 1:
                logger.debug('画面进度监听已停止：Windows=%d', code or data.error_code)
                self.close()
                return ExportProgress()
            if data.phase == 2:
                return ExportProgress(saving=True)
            if data.phase == 0 and data.idle_ms > 10000:
                self.close()
            if (data.phase != 1 or data.idle_ms > 5000 or data.video_jobs != 1
                    or data.position_us <= 0):
                return ExportProgress()
            if data.position_us > self.duration_us + 200000:
                self.close()
                return ExportProgress()
            return ExportProgress(min(1., data.position_us / self.duration_us))
        except Exception:
            logger.debug('读取画面进度失败，继续等待 PowerPoint 导出', exc_info=True)
            self.close()
            return ExportProgress()

    def close(self):
        handle, self._handle = self._handle, ctypes.c_void_p()
        if handle and self._library is not None:
            try:
                code = self._library.mm_export_progress_stop(handle)
                if code:
                    logger.warning('画面进度监听清理未完成：Windows=%d', code)
            except Exception:
                logger.warning('无法关闭画面进度监听', exc_info=True)
