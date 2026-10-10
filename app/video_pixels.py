"""为静态页面准备 NV12 像素，避免编码器逐帧重复转换 RGB。"""

import ctypes
from functools import lru_cache
from pathlib import Path
import sys

import PySide6
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat


_BytePointer = ctypes.POINTER(ctypes.c_uint8)
_Planes = _BytePointer * 4
_Strides = ctypes.c_int * 4


@lru_cache(maxsize=1)
def _scaler():
    if sys.platform != 'win32':
        raise OSError('NV12 preparation requires the Windows Qt libraries')
    library = ctypes.CDLL(str(Path(PySide6.__file__).resolve().parent / 'swscale-8.dll'))
    library.sws_getContext.argtypes = [ctypes.c_int] * 7 + [ctypes.c_void_p] * 3
    library.sws_getContext.restype = ctypes.c_void_p
    library.sws_scale.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BytePointer),
                                 ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int,
                                 ctypes.POINTER(_BytePointer), ctypes.POINTER(ctypes.c_int)]
    library.sws_scale.restype = ctypes.c_int
    library.sws_freeContext.argtypes = [ctypes.c_void_p]
    library.sws_freeContext.restype = None
    library.sws_getCoefficients.argtypes = [ctypes.c_int]
    library.sws_getCoefficients.restype = ctypes.POINTER(ctypes.c_int)
    library.sws_setColorspaceDetails.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                                ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    library.sws_setColorspaceDetails.restype = ctypes.c_int
    return library


class PreparedVideoFrame:
    """只缓存当前页的像素；每帧使用独立缓冲区和时间戳。"""

    def __init__(self, image, fps):
        image = image.convertToFormat(QImage.Format.Format_RGB32)
        width, height = image.width(), image.height()
        if image.isNull() or width % 2 or height % 2:
            raise ValueError('NV12 页面尺寸须为正偶数。')
        self.format = QVideoFrameFormat(QSize(width, height), QVideoFrameFormat.PixelFormat.Format_NV12)
        self.format.setStreamFrameRate(fps)
        self.format.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT709)
        self.format.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709)
        self.format.setColorRange(QVideoFrameFormat.ColorRange.ColorRange_Video)
        library = _scaler()
        # FFmpeg 7 的公开像素格式：BGRA=28，NV12=23；这里只调用稳定的 swscale C 接口。
        context = library.sws_getContext(width, height, 28, width, height, 23, 2, None, None, None)
        if not context:
            raise RuntimeError('无法准备视频像素。')
        frame = QVideoFrame(self.format)
        mapped = False
        try:
            coefficients = library.sws_getCoefficients(1)  # SWS_CS_ITU709
            if library.sws_setColorspaceDetails(context, coefficients, 1, coefficients, 0,
                                                0, 1 << 16, 1 << 16) < 0:
                raise RuntimeError('无法准备视频颜色。')
            mapped = frame.map(QVideoFrame.MapMode.WriteOnly)
            if not mapped or frame.planeCount() != 2:
                raise RuntimeError('无法准备视频缓冲区。')
            # 保持这些视图存活，直到 sws_scale 返回；所有指针仅用于本次同步调用。
            buffers = [(ctypes.c_uint8 * image.sizeInBytes()).from_buffer(image.bits())]
            buffers.extend((ctypes.c_uint8 * frame.mappedBytes(i)).from_buffer(frame.bits(i))
                           for i in range(2))
            source = _Planes(ctypes.cast(buffers[0], _BytePointer))
            target = _Planes(*(ctypes.cast(buffer, _BytePointer) for buffer in buffers[1:]))
            strides = _Strides(frame.bytesPerLine(0), frame.bytesPerLine(1))
            if library.sws_scale(context, source, _Strides(image.bytesPerLine()),
                                 0, height, target, strides) != height:
                raise RuntimeError('无法转换视频像素。')
            self.planes = tuple(bytes(frame.bits(i)[:frame.mappedBytes(i)]) for i in range(2))
            self.strides = tuple(strides[:2])
        finally:
            if mapped:
                frame.unmap()
            library.sws_freeContext(context)

    def frame(self):
        # QVideoFrame 的复制构造共享时间戳；不可直接复制并修改一个缓存帧。
        frame = QVideoFrame(self.format)
        if not frame.map(QVideoFrame.MapMode.WriteOnly):
            raise RuntimeError('无法生成视频画面，请重新导出。')
        try:
            for index, plane in enumerate(self.planes):
                if (frame.bytesPerLine(index) != self.strides[index]
                        or frame.mappedBytes(index) != len(plane)):
                    raise RuntimeError('无法生成视频画面，请重新导出。')
                frame.bits(index)[:len(plane)] = plane
        finally:
            frame.unmap()
        return frame
