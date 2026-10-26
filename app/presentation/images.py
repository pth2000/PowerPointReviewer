"""由 PowerPoint / WPS 将已保存文稿的副本导出为静态页面。"""

import logging
from pathlib import Path
import shutil
import sys

from PySide6.QtGui import QImageReader

from app.presentation.com import PROG_IDS, application_candidates, application_pid


class SlideRenderCancelled(Exception):
    pass


def export_slide_images(source, pages, directory, width, height, *, progress=None, cancelled=None,
                        preference='auto'):
    """只关闭本次打开的副本；不会退出已有 Office 进程或保存用户文稿。"""
    if sys.platform != 'win32':
        raise RuntimeError('PPT 静态画面导出目前需要 Windows 与 PowerPoint / WPS 演示。')
    source, directory = Path(source), Path(directory)
    pages = sorted(set(int(page) for page in pages))
    if not source.is_file() or source.suffix.lower() not in ('.ppt', '.pptx'):
        raise ValueError('请选择已保存的 PPT 或 PPTX 文件。')
    if not pages or pages[0] < 1:
        raise ValueError('讲稿页码须从第 1 页开始。')
    if width <= 0 or height <= 0:
        raise ValueError('PPT 画面的尺寸无效。')

    import pythoncom
    import win32com.client
    import win32com.client.dynamic
    import win32process

    def check_cancelled():
        if cancelled and cancelled():
            raise SlideRenderCancelled()

    directory.mkdir(parents=True, exist_ok=True)
    check_cancelled()
    copy_path = directory / ('source' + source.suffix.lower())
    if source.resolve() == copy_path.resolve():
        raise ValueError('无法准备 PPT 画面，请重新导出视频。')
    shutil.copyfile(source, copy_path)
    pythoncom.CoInitialize()
    try:
        original_pids = set(win32process.EnumProcesses())
        errors = []
        opened_application = False
        running_applications = {}
        for prog_id, _provider in PROG_IDS:
            try:
                running_applications[prog_id] = win32com.client.GetActiveObject(prog_id)
            except Exception:
                pass
        # 自动模式优先复用运行实例，显式偏好则由统一的软件选择规则排序。
        providers = application_candidates(
            [(prog, name, running_applications[prog]) for prog, name in PROG_IDS
             if prog in running_applications], preference)
        for prog_id, provider, running_application in providers:
            application = presentation = None
            owned_pid = None
            security = None
            try:
                check_cancelled()
                started_application = running_application is None
                application = win32com.client.DispatchEx(prog_id) if started_application else running_application
                application = win32com.client.dynamic.Dispatch(application)
                opened_application = True
                pid = application_pid(application)
                owned_pid = pid if started_application and pid and pid not in original_pids else None
                try:
                    security = application.AutomationSecurity
                    application.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
                except Exception:
                    pass
                presentation = application.Presentations.Open(str(copy_path.resolve()), -1, 0, 0)
                count = int(presentation.Slides.Count)
                if pages[-1] > count:
                    raise ValueError(f'讲稿包含第 {pages[-1]} 页，但所选 PPT 只有 {count} 页。')
                slide_width = float(presentation.PageSetup.SlideWidth)
                slide_height = float(presentation.PageSetup.SlideHeight)
                if slide_width <= 0 or slide_height <= 0:
                    raise RuntimeError('无法读取 PPT 的页面尺寸。')
                scale = min(width / slide_width, height / slide_height)
                image_width = max(1, round(slide_width * scale))
                image_height = max(1, round(slide_height * scale))
                output = directory / prog_id
                output.mkdir(exist_ok=True)
                images = {}
                for index, page in enumerate(pages):
                    check_cancelled()
                    path = output / f'{page}.png'
                    presentation.Slides.Item(page).Export(str(path.resolve()), 'PNG', image_width, image_height)
                    check_cancelled()
                    reader = QImageReader(str(path))
                    valid = reader.canRead() and reader.size().isValid()
                    reader.setFileName('')  # 立即释放 PNG 文件，便于取消时清理临时目录。
                    if not valid:
                        raise RuntimeError(f'第 {page} 页的 PPT 画面未能正确导出。')
                    images[page] = path
                    if progress:
                        progress(index + 1, len(pages))
                return images
            except (SlideRenderCancelled, ValueError):
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning('%s 静态页面导出失败：%s', provider, exc)
                errors.append(provider)
            finally:
                if presentation is not None:
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
    finally:
        pythoncom.CoUninitialize()
