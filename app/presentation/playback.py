"""普通朗读的放映准备与页内位置对齐，COM 对象只在界面线程使用。"""

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from app.presentation.session import PresentationSession


class SlideshowPlayback(QObject):
    ready = Signal()
    failed = Signal(str)
    restart_required = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.source = None
        self.preparing = False
        self.segment = None
        self.click_base = None
        self._new_session = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._prepare_step)

    @property
    def target_path(self):
        return Path(self.session.presentation.path) if self.session else None

    @property
    def application(self):
        return self.session.presentation.application if self.session else None

    def prepare(self, source, page, offset, *, allow_start=True, align=True, windowed=False,
                preference='auto'):
        self.requested_source = Path(source).resolve()
        self.requested_page, self.requested_offset = int(page), int(offset)
        self.allow_start, self.align = bool(allow_start), bool(align)
        self.windowed = bool(windowed)
        self.preference = preference
        self.attempts = 0
        self.preparing = True
        self.timer.start(0)

    def _prepare_step(self):
        self.timer.stop()
        if not self.preparing:
            return
        try:
            if (not self.session or self.source != self.requested_source
                    or (self.attempts == 0 and not self.session.presentation.is_active())):
                self._release_session()
                self.session = PresentationSession.start(
                    self.requested_source, reuse_open=True, allow_start=self.allow_start,
                    windowed=self.windowed, preference=self.preference)
                self.source = self.requested_source
                self._new_session = True
            presentation = self.session.presentation
            if not presentation.is_active():
                self.attempts += 1
                if self.attempts >= 50:
                    raise RuntimeError('PPT 放映尚未就绪，请稍后重试。')
                self.timer.start(100)
                return
            page, offset = self.requested_page, self.requested_offset
            if not 1 <= page <= presentation.slide_count:
                raise RuntimeError('当前讲稿页码超出了关联 PPT 的页数。')
            presentation.resume_background()
            self.ensure_running()
            view = presentation.window.View
            # 首次连接时也检查实际位置，避免重入同一页而重置正在播放的动画。
            at_position = (self.align and presentation.provider == 'PowerPoint'
                           and int(view.Slide.SlideIndex) == page
                           and self._click_index(view) == offset)
            if self.align and at_position:
                self.click_base = 0
            elif self.align and not self._at_segment(page, offset):
                if offset and presentation.provider == 'WPS 演示':
                    self.restart_required.emit(page)
                    return
                view.GotoSlide(page, True)
                self.click_base = self._click_index(view)
                if offset:
                    if not self._can_seek_click(view, offset):
                        self.restart_required.emit(page)
                        return
                    view.GotoClick(offset)
                    if self._click_index(view) != offset:
                        self.restart_required.emit(page)
                        return
                    self.click_base = 0
            elif not self.align:
                self.click_base = self._click_index(view)
            self.ensure_running()
            self.segment = page, offset
            self.preparing = False
            self._new_session = False
            self.ready.emit()
        except Exception as exc:
            self.cancel_preparation()
            message = (str(exc) if isinstance(exc, RuntimeError) else
                       '无法连接对应的 PPT 放映，请确认 PowerPoint 或 WPS 可以打开该文稿。')
            self.failed.emit(message)

    @staticmethod
    def _click_index(view):
        try:
            return max(0, int(view.GetClickIndex()))
        except Exception:
            return None

    @staticmethod
    def _can_seek_click(view, offset):
        try:
            return offset <= int(view.GetClickCount())
        except Exception:
            return False

    def _at_segment(self, page, offset):
        if self.segment != (page, offset):
            return False
        view = self.session.presentation.window.View
        if int(view.Slide.SlideIndex) != page or self.click_base is None:
            return False
        return self._click_index(view) == self.click_base + offset

    def set_segment(self, page, offset):
        self.segment = int(page), int(offset)

    def ensure_running(self):
        """检查放映本身是否暂停，不以是否拥有前台焦点作为判断条件。"""
        if not self.session or self.session.presentation.provider != 'PowerPoint':
            return
        show = self.session.presentation.window
        try:
            paused = int(show.View.State) == 2
        except Exception:
            return
        if paused:
            raise RuntimeError('PPT 放映已暂停，请恢复放映后再播放。')

    def advance_to(self, page):
        if not self.session or not self.session.presentation.is_active():
            raise RuntimeError('PPT 放映已退出，配音已停止。')
        self.ensure_running()
        view = self.session.presentation.window.View
        if int(view.Slide.SlideIndex) != page:
            view.GotoSlide(page, True)
            self.click_base = self._click_index(view)
        else:
            click = self._click_index(view)
            try:
                count = int(view.GetClickCount())
            except Exception:
                count = None
            if click is not None and count is not None and click >= count:
                raise RuntimeError(f'第 {page} 页的讲稿分段超过了点击动画数量，请检查页内分隔符。')
            view.Next()
        self.ensure_running()

    def goto_page(self, page):
        self.session.presentation.window.View.GotoSlide(page, True)
        self.click_base = self._click_index(self.session.presentation.window.View)
        self.segment = page, 0

    def cancel_preparation(self):
        self.timer.stop()
        self.preparing = False
        if self._new_session:
            self._release_session()
        self._new_session = False

    def _release_session(self):
        if self.session:
            self.session.close(restore_position=False)
        self.session = None
        self.source = None
        self.segment = None
        self.click_base = None

    def release(self):
        self.timer.stop()
        self.preparing = False
        self._release_session()
        self._new_session = False
