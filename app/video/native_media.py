"""原生导出使用的视频时间读取和独立音轨封装，共用现有 Qt 媒体库。"""

import ctypes
from dataclasses import dataclass
import logging
from pathlib import Path

from app.video.mux import MuxCancelled, _Progress, _library


class _MediaInfo(ctypes.Structure):
    _fields_ = [('duration_us', ctypes.c_int64), ('video_packets', ctypes.c_int64),
                ('tick_us', ctypes.c_int64), ('width', ctypes.c_int32),
                ('height', ctypes.c_int32), ('audio_streams', ctypes.c_int32)]


@dataclass(frozen=True)
class MediaInfo:
    """视频包的实际时长与画面信息，时长不含其他音轨的容器尾部。"""
    duration_us: int
    video_packets: int
    tick_us: int
    width: int
    height: int
    audio_streams: int


def ensure_native_media_available():
    library = _library()
    try:
        library.mm_probe.argtypes = [ctypes.c_char_p, ctypes.POINTER(_MediaInfo), _Progress,
                                     ctypes.c_char_p, ctypes.c_int]
        library.mm_probe.restype = ctypes.c_int
        library.mm_join.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                    _Progress, ctypes.c_char_p, ctypes.c_int]
        library.mm_join.restype = ctypes.c_int
    except AttributeError as exc:
        raise RuntimeError('视频导出组件需要更新，请重新下载最新版软件。') from exc
    return library


def _cancellation_callback(cancelled):
    errors = []

    @_Progress
    def callback(_position, _duration):
        try:
            if cancelled and cancelled():
                return 1
            return 0
        except Exception as exc:
            errors.append(exc)
            return 1
    return callback, errors


def probe_media(source, *, cancelled=None):
    source = Path(source).resolve()
    if cancelled and cancelled():
        raise MuxCancelled()
    library = ensure_native_media_available()
    data, error = _MediaInfo(), ctypes.create_string_buffer(1024)
    callback, errors = _cancellation_callback(cancelled)
    code = library.mm_probe(str(source).encode('utf-8'), ctypes.byref(data), callback, error, len(error))
    if cancelled and cancelled():
        raise MuxCancelled()
    if code or errors:
        logging.getLogger(__name__).error('读取原生视频失败：%s', error.value.decode('utf-8', errors='replace'))
        raise RuntimeError('无法读取导出视频，请确认文稿可正常导出后重试。')
    return MediaInfo(*(getattr(data, name) for name, _ in data._fields_))


def join_narration(video, audio, target, *, cancelled=None):
    video, audio, target = (Path(path).resolve() for path in (video, audio, target))
    if target in (video, audio) or (target.exists() and target.stat().st_size):
        raise ValueError('目标文件已存在，请更换保存位置或文件名。')
    if not video.is_file() or not audio.is_file():
        raise ValueError('画面或配音文件不存在，请重新导出。')
    if cancelled and cancelled():
        raise MuxCancelled()
    library = ensure_native_media_available()
    error = ctypes.create_string_buffer(1024)
    callback, errors = _cancellation_callback(cancelled)
    code = library.mm_join(str(video).encode('utf-8'), str(audio).encode('utf-8'),
                           str(target).encode('utf-8'), callback, error, len(error))
    if cancelled and cancelled():
        raise MuxCancelled()
    if code or errors or not target.is_file() or not target.stat().st_size:
        logging.getLogger(__name__).error('合并原生视频与配音失败：%s', error.value.decode('utf-8', errors='replace'))
        raise RuntimeError('无法保存视频，请检查保存位置的访问权限和剩余空间后重试。')
