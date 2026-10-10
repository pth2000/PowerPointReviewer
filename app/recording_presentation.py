"""连接 PowerPoint / WPS 放映，绑定采集窗口并按页码、点击索引控制。"""

import ctypes
from pathlib import Path
import sys

from PySide6.QtCore import QSizeF
from PySide6.QtGui import QWindow
from PySide6.QtMultimedia import QCapturableWindow

from app.com_window import slideshow_hwnd
from app.slideshow import PROG_IDS, _ensure_com
from app import window_target


_PROCESSES = {'PowerPoint': {'powerpnt.exe'}, 'WPS 演示': {'wps.exe', 'wpp.exe'}}


def list_presentations():
    if sys.platform != 'win32' or not _ensure_com():
        return []
    import win32com.client
    result = []
    for prog_id, provider in PROG_IDS:
        try:
            application = win32com.client.GetActiveObject(prog_id)
            count = int(application.SlideShowWindows.Count)
        except Exception:
            continue
        for index in range(1, count + 1):
            try:
                window = application.SlideShowWindows(index)
                if bool(window.IsFullScreen):
                    result.append(RecordingPresentation(window, provider, prog_id, index))
            except Exception:
                continue
    return result


class RecordingPresentation:
    """COM 在界面线程使用；窗口选择仅影响采集，不改变翻页方式。"""

    def __init__(self, window, provider='PowerPoint', prog_id='PowerPoint.Application', index=1):
        self.window = window
        self.provider = provider
        self.presentation = window.Presentation
        self.name = str(self.presentation.Name)
        self.path = str(self.presentation.FullName)
        self.slide_count = int(self.presentation.Slides.Count)
        self.slide_size = QSizeF(float(self.presentation.PageSetup.SlideWidth),
                                 float(self.presentation.PageSetup.SlideHeight))
        if self.slide_size.isEmpty():
            raise RuntimeError('无法读取 PPT 的页面尺寸。')
        self.hwnd = 0
        self.target = {}
        self.auto_target = {}
        self.capture_mode = 'auto'
        self.prog_id, self.show_index = prog_id, index
        self.original_advance_mode = None
        self.foreign_window = None
        self.click_index = None
        self.indexed_clicks = False
        hwnd = slideshow_hwnd(prog_id, index)
        if hwnd:
            try:
                self.bind_capture_window(window_target.window_info(hwnd), automatic=True)
            except RuntimeError:
                pass  # Retain the COM show so the user can explicitly select a window.

    def matches_source(self, source):
        return source and Path(source).suffix.lower() == '.pptx' and Path(source).resolve() == Path(self.path).resolve()

    @property
    def capture_processes(self):
        return _PROCESSES.get(self.provider, set())

    def validate_capture_window(self, target, automatic=False):
        """检查采集窗口，保持当前绑定和 COM 控制目标不变。"""
        hwnd = int(target.get('hwnd') or 0)
        actual = window_target.window_info(hwnd)
        if not actual or actual['process'].casefold() not in self.capture_processes:
            raise RuntimeError(f'请选择 {self.provider} 的放映窗口。')
        if any(target.get(key) and target[key] != actual[key] for key in ('pid', 'class_name')):
            raise RuntimeError('所选窗口已关闭或改变，请刷新窗口列表。')
        user32 = ctypes.windll.user32
        if user32.GetAncestor(hwnd, 2) != hwnd:
            raise RuntimeError('请选择全屏放映窗口，当前窗口无法单独采集。')
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            raise RuntimeError('请保持放映窗口正常显示。')
        known_show = automatic or all(actual.get(key) == self.auto_target.get(key)
                                      for key in ('hwnd', 'pid', 'class_name'))
        name = actual['class_name'].casefold()
        if not known_show:
            supported_class = (name == 'screenclass' if self.provider == 'PowerPoint'
                               else name.startswith('qt') and name.endswith('qwindowicon'))
            if not supported_class:
                raise RuntimeError('请选择幻灯片放映窗口，演讲者控制台、阅读和编辑窗口暂不支持。')
        if not window_target.is_fullscreen_window(hwnd):
            raise RuntimeError('当前仅支持全屏放映，阅读或窗口放映暂不支持。')
        return actual

    def bind_capture_window(self, target, automatic=False):
        """绑定本次录制画面，允许覆盖自动窗口；COM 控制仍指向当前文稿。"""
        actual = self.validate_capture_window(target, automatic)
        self.hwnd, self.target = actual['hwnd'], actual
        self.foreign_window = None
        self.capture_mode = 'auto' if automatic else 'manual'
        if automatic:
            self.auto_target = dict(actual)

    def restore_auto_window(self):
        target = self.auto_target
        if not target:
            hwnd = slideshow_hwnd(self.prog_id, self.show_index)
            target = window_target.window_info(hwnd) if hwnd else {}
        if not target:
            raise RuntimeError('无法自动识别放映窗口，请刷新放映或继续手动选择。')
        self.bind_capture_window(target, automatic=True)

    def same_show(self, other):
        """刷新列表时只向同一个 COM 放映继承手动选择。"""
        try:
            return (self.provider == other.provider
                    and self.window._oleobj_ == other.window._oleobj_)
        except AttributeError:
            return self is other

    def capturable_window(self):
        self._verify_window()
        # Qt 的 Python 接口不暴露原生窗口 ID，使用其公开的外部窗口包装接口。
        self.foreign_window = QWindow.fromWinId(self.hwnd)
        if self.foreign_window is None:
            raise RuntimeError('无法识别放映窗口，请重新选择窗口或启动放映。')
        captured = QCapturableWindow(self.foreign_window)
        if not captured.isValid():
            raise RuntimeError('当前放映窗口无法录制，请重新启动放映。')
        return captured

    def _verify_window(self):
        if not self.hwnd:
            raise RuntimeError('无法自动识别放映窗口，请点击“选择窗口”指定本次录制的窗口。')
        user32 = ctypes.windll.user32
        actual = window_target.window_info(self.hwnd)
        if (not actual or actual['pid'] != self.target['pid']
                or actual['class_name'] != self.target['class_name']):
            raise RuntimeError('放映窗口已关闭或改变，录制已停止。')
        if not user32.IsWindowVisible(self.hwnd):
            raise RuntimeError('放映窗口已隐藏，录制已停止。')
        if user32.IsIconic(self.hwnd):
            raise RuntimeError('放映窗口已最小化，录制已停止。')
        if not window_target.is_fullscreen_window(self.hwnd):
            raise RuntimeError('采集窗口已退出全屏，录制已停止。')

    def _reset_clicks(self):
        self.click_index = None
        try:
            self.window.View.GetClickCount()
            self.click_index = max(0, int(self.window.View.GetClickIndex()))
            self.indexed_clicks = True
        except Exception:
            self.indexed_clicks = False
        if self.indexed_clicks and self.click_index:
            # WPS can retain the old click state even after GotoSlide(..., True).
            self.window.View.GotoClick(0)
            self.click_index = max(0, int(self.window.View.GetClickIndex()))
            if self.click_index:
                raise RuntimeError('无法复位本页动画，请重新启动放映后再录制。')

    def begin(self, page):
        self._verify_window()
        if not 1 <= page <= self.slide_count:
            raise RuntimeError('讲稿页码超出了所选 PPT 的页数。')
        settings = self.presentation.SlideShowSettings
        mode = int(settings.AdvanceMode)
        if mode != 1 and self._needs_manual_advance(mode):
            if self.original_advance_mode is None:
                self.original_advance_mode = mode
            settings.AdvanceMode = 1  # ppSlideShowManualAdvance，录制时间轴负责换页。
        self.window.View.GotoSlide(page, True)
        self._reset_clicks()
        self.verify(page)

    def _needs_manual_advance(self, mode):
        if mode != 2:  # ppSlideShowUseSlideTimings; rehearsal also needs disabling.
            return True
        try:
            slides = self.presentation.Slides
            return any(bool(slides.Item(index).SlideShowTransition.AdvanceOnTime)
                       for index in range(1, self.slide_count + 1))
        except Exception:
            # An older provider may not expose transition timing information.
            return True

    def verify(self, page):
        self._verify_window()
        try:
            actual = int(self.window.View.Slide.SlideIndex)
        except Exception as exc:
            raise RuntimeError(f'无法连接 {self.provider} 放映，录制已停止。') from exc
        if actual != page:
            raise RuntimeError(f'放映已切到第 {actual} 页，讲稿仍在第 {page} 页。'
                               '请检查分隔符与点击动画的对应关系，并关闭 PPT 的自动换页。')

    def advance(self, page, previous_page):
        self.verify(previous_page)
        view = self.window.View
        if page != previous_page:
            # A slide change must not consume another animation on the old slide.
            view.GotoSlide(page, True)
            self._reset_clicks()
        elif self.indexed_clicks:
            if max(0, int(view.GetClickIndex())) != self.click_index:
                raise RuntimeError('放映动画的点击位置已改变，请勿在录制期间手动点击。')
            target = self.click_index + 1
            count = int(view.GetClickCount())
            if target > count:
                raise RuntimeError(f'第 {page} 页讲稿要求第 {target} 次点击，但放映只有 {count} 次点击，'
                                   '请检查页内分隔符与动画是否对应。')
            view.GotoClick(target)
            if int(view.GetClickIndex()) != target:
                raise RuntimeError(f'第 {page} 页的第 {target} 次动画点击未能执行。')
            self.click_index = target
        else:
            view.Next()
        self.verify(page)

    def restore(self):
        if self.original_advance_mode is not None:
            try:
                settings = self.presentation.SlideShowSettings
                if int(settings.AdvanceMode) != self.original_advance_mode:
                    settings.AdvanceMode = self.original_advance_mode
            except Exception:
                pass
            self.original_advance_mode = None
