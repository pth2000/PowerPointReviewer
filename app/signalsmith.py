"""通过 ctypes 调用随程序提供的 Signalsmith PCM16 变速组件。"""

import ctypes
from functools import lru_cache
import math
from pathlib import Path
import sys


def _library_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'third_party' / 'signalsmith' / 'SignalsmithStretch_x64.dll'


@lru_cache(maxsize=1)
def _library():
    if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError('调整语速目前需要 64 位 Windows。')
    try:
        library = ctypes.CDLL(str(_library_path()))
        version = library.ss_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        if version() != 1:
            raise RuntimeError('语速调整功能不可用，请重新下载最新版软件后再试。')
        process = library.ss_process_pcm16
        pointer = ctypes.POINTER(ctypes.c_int16)
        process.argtypes = [pointer, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.c_double, pointer, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_uint32)]
        process.restype = ctypes.c_int
        return library, process
    except (OSError, AttributeError) as exc:
        raise RuntimeError('语速调整功能不可用，请重新下载最新版软件后再试。') from exc


def change_pcm16(pcm, sample_rate, channels, speed):
    if (channels not in (1, 2) or not 1000 <= sample_rate <= 384000 or
            not math.isfinite(speed) or not 0.5 <= speed <= 2 or
            not pcm or len(pcm) % (channels * 2)):
        raise RuntimeError('无法调整语速，请检查音频是否完整，以及语速是否在 0.5–2.0 倍之间。')
    frames = len(pcm) // (channels * 2)
    output_frames = math.floor(frames / speed + 0.5)
    if frames > 2147483647 or not 1 <= output_frames <= 2147483647:
        raise RuntimeError('这段配音过长，无法调整语速，请缩短单段讲稿后重试。')
    _dll, process = _library()
    source = (ctypes.c_int16 * (frames * channels)).from_buffer_copy(pcm)
    output = (ctypes.c_int16 * (output_frames * channels))()
    written = ctypes.c_uint32()
    status = process(source, frames, sample_rate, channels, speed,
                     output, output_frames, ctypes.byref(written))
    if status == 2:
        raise RuntimeError('语速处理失败：内存不足，请缩短单段讲稿后重试。')
    if status != 0 or written.value != output_frames:
        raise RuntimeError('语速调整失败，请重新生成配音后重试。')
    return ctypes.string_at(output, written.value * channels * 2)
