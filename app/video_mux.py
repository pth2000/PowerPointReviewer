"""使用 Qt 随附的 FFmpeg 动态库无损封装视频和可切换的字幕轨道。"""

import ctypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sys
import threading
import time

import PySide6
from PySide6.QtCore import QThread, Signal

from app.video_timeline import SAMPLE_RATE


class MuxCancelled(Exception):
    """封装已取消；调用方须清理其独占的临时输出文件。"""


@dataclass(frozen=True)
class VideoCaption:
    start_ms: int
    end_ms: int
    text: str


def validate_captions(captions):
    previous_end = 0
    for caption in captions:
        if (not isinstance(caption.start_ms, int) or not isinstance(caption.end_ms, int)
                or caption.start_ms < previous_end or caption.end_ms <= caption.start_ms
                or caption.end_ms > (2**63 - 1) // 1000):
            raise ValueError('字幕时间须按顺序排列，且不能重叠。')
        if not caption.text or '\0' in caption.text or len(caption.text.encode('utf-8')) > 65535:
            raise ValueError('字幕内容无效或单段过长，请缩短讲稿分段。')
        previous_end = caption.end_ms


def build_video_captions(notes, timeline):
    """按录制采样时间定位字幕，空白段占用时间但不产生字幕。"""
    if len(notes) != len(timeline.cues):
        raise ValueError('讲稿与录制时间轴的分段数量不一致。')
    captions = []
    for note, cue in zip(notes, timeline.cues):
        if int(note['page']) != cue.page:
            raise ValueError('讲稿与录制时间轴的页码不一致。')
        text = str(note.get('text', '')).replace('\r\n', '\n').replace('\r', '\n').strip()
        start = (cue.audio_frame * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE
        end = (cue.end_frame * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE
        if text and end > start:
            captions.append(VideoCaption(start, end, text))
    validate_captions(captions)
    return tuple(captions)


class _NativeCaption(ctypes.Structure):
    _fields_ = [('start_ms', ctypes.c_int64), ('end_ms', ctypes.c_int64), ('text', ctypes.c_char_p)]


_Progress = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int64, ctypes.c_int64)


@lru_cache(maxsize=1)
def _library():
    if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError('视频封装组件需要 64 位 Windows。')
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    try:
        library = ctypes.CDLL(str(root / 'native/media_mux/MediaMux_x64.dll'))
        library.mm_abi_version.argtypes = []
        library.mm_abi_version.restype = ctypes.c_uint
        if library.mm_abi_version() != 1:
            raise RuntimeError('视频封装组件版本不兼容，请使用完整发行包。')
        library.mm_initialize.argtypes = [ctypes.c_wchar_p, ctypes.c_char_p, ctypes.c_int]
        library.mm_initialize.restype = ctypes.c_int
        library.mm_mux.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.POINTER(_NativeCaption), ctypes.c_int, _Progress,
                                   ctypes.c_char_p, ctypes.c_int]
        library.mm_mux.restype = ctypes.c_int
        error = ctypes.create_string_buffer(1024)
        if library.mm_initialize(str(Path(PySide6.__file__).resolve().parent), error, len(error)) != 0:
            raise RuntimeError('无法加载 Qt 视频封装库，请使用匹配的完整发行包：'
                               + error.value.decode('utf-8', errors='replace'))
        return library
    except (OSError, AttributeError) as exc:
        raise RuntimeError('无法加载视频封装组件，请使用完整发行包。') from exc


def ensure_mux_available():
    """在耗时录制开始前检查组件和 Qt 库的 ABI。"""
    _library()


def mux_video(source, target, container='mp4', captions=(), cancelled=None, progress=None):
    """封装到调用方独占的空文件；不覆盖非空文件、不调用 ffmpeg.exe。"""
    if container not in ('mp4', 'mkv'):
        raise ValueError('视频输出格式须为 MP4 或 MKV。')
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError('封装输出不能覆盖输入视频。')
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError('录制视频不存在或为空。')
    if target.exists() and target.stat().st_size:
        raise RuntimeError('封装临时文件不是空文件，已停止以避免覆盖。')
    captions = tuple(captions)
    validate_captions(captions)
    if cancelled and cancelled():
        raise MuxCancelled()
    library = _library()
    encoded = [caption.text.encode('utf-8') for caption in captions]
    native_captions = (_NativeCaption * len(captions))(*(
        _NativeCaption(caption.start_ms, caption.end_ms, text) for caption, text in zip(captions, encoded)))
    callback_errors = []

    @_Progress
    def callback(position, duration):
        # ctypes callbacks must never allow Python exceptions to escape into C++.
        try:
            if cancelled and cancelled():
                return 1
            if progress:
                progress(max(0, position), max(0, duration))
            return 0
        except Exception as exc:
            callback_errors.append(exc)
            return 1

    error = ctypes.create_string_buffer(1024)
    result = library.mm_mux(str(source).encode('utf-8'), str(target).encode('utf-8'),
                            b'matroska' if container == 'mkv' else b'mp4', native_captions,
                            len(captions), callback, error, len(error))
    if callback_errors:
        raise RuntimeError('视频封装进度处理失败。') from callback_errors[0]
    if cancelled and cancelled():
        raise MuxCancelled()
    if result != 0:
        raise RuntimeError('视频封装失败：' + error.value.decode('utf-8', errors='replace'))
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError('视频封装未产生有效文件。')


class VideoMuxTask(QThread):
    """封装和 IO 在工作线程完成；finished 发出后才能删除临时文件。"""

    progress = Signal(int)

    def __init__(self, source, target, container, captions=(), parent=None):
        super().__init__(parent)
        self.source, self.target = Path(source), Path(target)
        self.container, self.captions = container, tuple(captions)
        self.error = ''
        self.was_cancelled = False
        self._cancel = threading.Event()
        self._last_progress = 0.0

    def cancel(self):
        self._cancel.set()

    def _progress(self, position, duration):
        now = time.monotonic()
        if duration > 0 and now - self._last_progress >= 0.1:
            self._last_progress = now
            self.progress.emit(min(99, round(position * 100 / duration)))

    def run(self):
        try:
            mux_video(self.source, self.target, self.container, self.captions,
                      self._cancel.is_set, self._progress)
        except MuxCancelled:
            self.was_cancelled = True
        except Exception as exc:
            self.error = str(exc)
