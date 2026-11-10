"""读取已保存的幻灯片画面，复用干净文稿或独立加载保存内容。"""

import ctypes
import logging
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtGui import QImageReader

from app.presentation.com import (
    application_candidates,
    application_pid,
    ordered_prog_ids,
    running_applications,
)


class SlideRenderCancelled(Exception):
    pass


class SlideSourceChanged(RuntimeError):
    """复用的文稿已关闭、改名或产生未保存修改，需要重新选择读取来源。"""


@contextmanager
def _com_apartment():
    """平衡本次 COM 初始化，不撤销调用线程已有的初始化或连接。"""
    ole32 = ctypes.WinDLL('ole32')
    initialize = ole32.CoInitializeEx
    initialize.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    initialize.restype = ctypes.c_long
    uninitialize = ole32.CoUninitialize
    uninitialize.argtypes = []
    uninitialize.restype = None
    result = initialize(None, 2)  # COINIT_APARTMENTTHREADED
    if result < 0 and result != -2147417850:  # RPC_E_CHANGED_MODE：沿用调用线程已有的模式。
        raise OSError(f'无法初始化演示软件连接：0x{result & 0xffffffff:08X}')
    try:
        yield
    finally:
        if result >= 0:
            uninitialize()


def _is_saved_source(presentation, source):
    try:
        return (Path(str(presentation.FullName)).resolve() == source
                and bool(int(presentation.Saved)))
    except Exception:
        return False


def _find_saved_source(application, source):
    try:
        presentations = application.Presentations
        for index in range(1, int(presentations.Count) + 1):
            try:
                presentation = presentations.Item(index)
                if _is_saved_source(presentation, source):
                    return presentation
            except Exception:
                continue
    except Exception:
        pass
    return None


class SlideImageSource:
    """在所属 COM 线程内复用文稿，已导出的页面保存在调用方临时目录。"""

    def __init__(self, presentation, directory, width, height, cancelled, *, borrowed_source=None):
        self.presentation = presentation
        self.borrowed_source = borrowed_source
        self.source_stamp = self._stamp() if borrowed_source else None
        self.directory = directory
        self.page_count = int(presentation.Slides.Count)
        self.cancelled = cancelled
        slide_width = float(presentation.PageSetup.SlideWidth)
        slide_height = float(presentation.PageSetup.SlideHeight)
        if slide_width <= 0 or slide_height <= 0:
            raise RuntimeError('无法读取 PPT 的页面尺寸。')
        scale = min(width / slide_width, height / slide_height)
        self.width = max(1, round(slide_width * scale))
        self.height = max(1, round(slide_height * scale))

    def _stamp(self):
        stat = self.borrowed_source.stat()
        return stat.st_mtime_ns, stat.st_size

    def _check_source(self):
        if self.borrowed_source is None:
            return
        try:
            valid = (_is_saved_source(self.presentation, self.borrowed_source)
                     and self._stamp() == self.source_stamp)
        except OSError:
            valid = False
        if not valid:
            raise SlideSourceChanged()

    def export(self, page):
        if self.cancelled and self.cancelled():
            raise SlideRenderCancelled()
        self._check_source()
        page = int(page)
        if page < 1:
            raise ValueError('讲稿页码须从第 1 页开始。')
        if page > self.page_count:
            raise ValueError(f'讲稿包含第 {page} 页，但所选 PPT 只有 {self.page_count} 页。')
        path = self.directory / f'{page}.png'
        if not path.is_file():
            try:
                self.presentation.Slides.Item(page).Export(
                    str(path.resolve()), 'PNG', self.width, self.height)
            except Exception:
                self._check_source()
                raise
        self._check_source()
        if self.cancelled and self.cancelled():
            raise SlideRenderCancelled()
        reader = QImageReader(str(path))
        valid = reader.canRead() and reader.size().isValid()
        reader.setFileName('')
        if not valid:
            path.unlink(missing_ok=True)
            raise RuntimeError(f'第 {page} 页的 PPT 画面未能正确导出。')
        return path


@contextmanager
def open_slide_images(source, directory, width, height, *, cancelled=None, preference='auto'):
    """同一线程内读取和释放资源，用户文稿只借用，始终由用户管理。"""
    if sys.platform != 'win32':
        raise RuntimeError('PPT 静态画面导出目前需要 Windows 与 PowerPoint / WPS 演示。')
    source, directory = Path(source), Path(directory)
    if not source.is_file() or source.suffix.lower() not in ('.ppt', '.pptx'):
        raise ValueError('请选择已保存的 PPT 或 PPTX 文件。')
    if width <= 0 or height <= 0:
        raise ValueError('PPT 画面的尺寸无效。')

    # COM 的初始化与文稿操作必须发生在同一线程。
    import win32com.client
    import win32com.client.dynamic
    import win32process

    def check_cancelled():
        if cancelled and cancelled():
            raise SlideRenderCancelled()

    directory.mkdir(parents=True, exist_ok=True)
    check_cancelled()
    with _com_apartment():
        original_pids = set(win32process.EnumProcesses())
        errors = []
        opened_application = False
        providers = application_candidates(running_applications(), preference)
        preferred = ordered_prog_ids(preference)[0][0] if preference in ('powerpoint', 'wps') else None
        providers.sort(key=lambda item: (
            bool(preferred and item[0] != preferred),
            item[2] is None or _find_saved_source(item[2], source.resolve()) is None,
        ))
        for prog_id, provider, running_application in providers:
            application = presentation = images = None
            owned_pid = None
            security = None
            yielded = False
            borrowed = False
            try:
                check_cancelled()
                started_application = running_application is None
                application = win32com.client.DispatchEx(prog_id) if started_application else running_application
                application = win32com.client.dynamic.Dispatch(application)
                opened_application = True
                presentation = _find_saved_source(application, source.resolve())
                borrowed = presentation is not None
                if borrowed:
                    output = directory / prog_id
                    output.mkdir(exist_ok=True)
                    try:
                        images = SlideImageSource(presentation, output, width, height, cancelled,
                                                  borrowed_source=source.resolve())
                        images._check_source()
                    except Exception:
                        # 文稿可能在枚举后被关闭或编辑，继续独立读取保存内容。
                        if images is not None:
                            images.presentation = None
                        presentation = images = None
                        borrowed = False
                    else:
                        yielded = True
                        yield images
                        return
                pid = application_pid(application)
                owned_pid = pid if started_application and pid and pid not in original_pids else None
                input_path, untitled = source.resolve(), -1
                if prog_id != 'PowerPoint.Application':
                    # WPS 可能忽略 Untitled，独立路径可避免复用用户正在编辑的文稿。
                    input_path = (directory / ('source' + source.suffix.lower())).resolve()
                    if input_path == source.resolve():
                        raise ValueError('无法准备 PPT 画面，请重新导出视频。')
                    shutil.copyfile(source, input_path)
                    untitled = 0
                check_cancelled()
                try:
                    security = application.AutomationSecurity
                    application.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
                except Exception:
                    pass
                try:
                    presentation = application.Presentations.Open(str(input_path), -1, untitled, 0)
                finally:
                    if security is not None:
                        application.AutomationSecurity = security
                        security = None
                check_cancelled()
                output = directory / prog_id
                output.mkdir(exist_ok=True)
                images = SlideImageSource(presentation, output, width, height, cancelled)
                yielded = True
                yield images
                return
            except (SlideRenderCancelled, ValueError):
                raise
            except Exception as exc:
                if yielded:
                    raise
                logging.getLogger(__name__).warning('%s 静态页面导出失败：%s', provider, exc)
                errors.append(provider)
            finally:
                if images is not None:
                    images.presentation = None
                if presentation is not None and not borrowed:
                    try:
                        presentation.Close()
                    except Exception:
                        logging.getLogger(__name__).warning('无法关闭静态导出使用的文稿副本', exc_info=True)
                if application is not None:
                    if security is not None:
                        try:
                            application.AutomationSecurity = security
                        except Exception:
                            pass
                    if owned_pid:
                        try:
                            if int(application.Presentations.Count) == 0:
                                application.Quit()
                        except Exception:
                            pass
                presentation = application = None
        if not opened_application:
            raise RuntimeError('无法打开演示软件，请确认已安装 PowerPoint 或 WPS 演示，并能正常打开所选文稿。')
        raise RuntimeError(f'{" / ".join(errors)} 无法导出 PPT 画面，请确认文稿可正常打开并重试。')


def export_slide_images(source, pages, directory, width, height, *, progress=None, cancelled=None,
                        preference='auto'):
    """一次导出若干页面，返回的图片由调用方负责清理。"""
    pages = sorted({int(page) for page in pages})
    if not pages or pages[0] < 1:
        raise ValueError('讲稿页码须从第 1 页开始。')
    with open_slide_images(source, directory, width, height,
                           cancelled=cancelled, preference=preference) as reader:
        if pages[-1] > reader.page_count:
            raise ValueError(f'讲稿包含第 {pages[-1]} 页，但所选 PPT 只有 {reader.page_count} 页。')
        images = {}
        for index, page in enumerate(pages):
            images[page] = reader.export(page)
            if progress:
                progress(index + 1, len(pages))
        return images
