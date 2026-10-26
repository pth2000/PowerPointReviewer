"""为已导入的 PPT 连接或启动放映，管理本次打开的临时文稿。"""

import logging
from pathlib import Path
import shutil
import tempfile

from app.presentation.control import PresentationControl, list_presentations
from app.presentation.com import application_candidates, application_pid, ensure_com, running_applications


logger = logging.getLogger(__name__)


class PresentationSession:
    def __init__(self):
        self.presentation = None
        self.application = None
        self.document = None
        self.directory = None
        self.window = None
        self.owned_pid = None
        self.original_position = None

    @classmethod
    def start(cls, source, *, reuse_open=False, allow_start=True, windowed=False, preference='auto'):
        """复用对应放映；普通播放可从已打开文稿启动，否则使用只读副本。"""
        import win32com.client
        import win32process

        source = Path(source).resolve()
        if not source.is_file() or source.suffix.lower() not in ('.ppt', '.pptx'):
            raise RuntimeError('找不到关联的 PPT，请重新关联文稿。')
        if not ensure_com():
            raise RuntimeError('无法连接演示软件，请检查 PowerPoint 或 WPS 演示。')
        shows = list_presentations(fullscreen_only=False, capture_window=not reuse_open)
        if preference in ('powerpoint', 'wps'):
            label = 'WPS 演示' if preference == 'wps' else 'PowerPoint'
            shows.sort(key=lambda show: show.provider != label)
        for presentation in shows:
            if presentation.matches_source(source):
                session = cls()
                session.presentation = presentation
                session.remember_position()
                return session

        if not allow_start:
            raise RuntimeError('请先在 PowerPoint 或 WPS 中开始放映对应 PPT，再点击播放。')

        original_pids = set(win32process.EnumProcesses())
        running = list(running_applications())
        opened = []
        preferred = set()
        for prog_id, provider, application in running:
            try:
                for index in range(1, int(application.Presentations.Count) + 1):
                    try:
                        document = application.Presentations.Item(index)
                        if Path(str(document.FullName)).resolve() == source:
                            preferred.add(id(application))
                            opened.append((prog_id, provider, application, document))
                    except Exception:
                        continue
            except Exception:
                continue
        providers = application_candidates(sorted(running, key=lambda item: id(item[2]) not in preferred),
                                          preference)
        if reuse_open and opened:
            if preference in ('powerpoint', 'wps'):
                opened.sort(key=lambda item: item[1] != ('WPS 演示' if preference == 'wps' else 'PowerPoint'))
            for prog_id, provider, application, document in opened:
                session = cls()
                try:
                    session.application = application
                    # 使用内存中的文稿；不改放映设置、保存状态或编辑窗口。
                    # 只拥有本次启动的放映，不拥有用户的文稿或演示软件。
                    session.window = cls._run_open_document(document,
                                                            windowed and provider == 'PowerPoint')
                    session.presentation = PresentationControl(
                        session.window, provider, prog_id, session._show_index(), session.application,
                        capture_window=False)
                    session.presentation.require_fullscreen = False
                    return session
                except Exception:
                    logger.warning('%s 从已打开文稿启动放映失败', provider, exc_info=True)
                    session.close(restore_position=False)
            raise RuntimeError('无法从已打开的 PPT 启动放映，请在演示软件中手动开始放映后重试。')
        for prog_id, provider, running_application in providers:
            session = cls()
            security = None
            application = None
            try:
                session.directory = tempfile.TemporaryDirectory(prefix='pptreviewer-show-')
                copy = Path(session.directory.name) / (source.stem + '-slideshow' + source.suffix)
                shutil.copyfile(source, copy)
                started = running_application is None
                application = running_application if not started else win32com.client.DispatchEx(prog_id)
                session.application = win32com.client.dynamic.Dispatch(application)
                pid = application_pid(session.application)
                session.owned_pid = pid if started and pid and pid not in original_pids else None
                try:
                    security = session.application.AutomationSecurity
                    session.application.AutomationSecurity = 3
                except Exception:
                    pass
                session.document = session.application.Presentations.Open(str(copy), -1, 0, 0)
                settings = session.document.SlideShowSettings
                settings.ShowType = 2 if windowed and provider == 'PowerPoint' else 1
                if windowed and provider == 'PowerPoint':
                    settings.ShowScrollbar = 0
                settings.RangeType = 1  # ppShowAll：配音时间轴可以访问所有页面。
                settings.AdvanceMode = 1
                settings.ShowWithAnimation = -1
                try:
                    settings.ShowPresenterView = 0
                except Exception:
                    pass  # 部分 WPS 版本没有此属性。
                session.window = settings.Run()
                index = session._show_index()
                session.presentation = PresentationControl(session.window, provider, prog_id, index,
                                                             session.application,
                                                             capture_window=not reuse_open)
                session.presentation.source_path = str(source)
                session.presentation.name = source.name
                return session
            except Exception:
                logger.warning('%s 自动启动放映失败', provider, exc_info=True)
                if session.application is not None and security is not None:
                    try:
                        session.application.AutomationSecurity = security
                    except Exception:
                        logger.warning('无法恢复演示软件安全设置', exc_info=True)
                    security = None
                session.close()
            finally:
                if session.application is not None and security is not None:
                    try:
                        session.application.AutomationSecurity = security
                    except Exception:
                        logger.warning('无法恢复演示软件安全设置', exc_info=True)
        raise RuntimeError('无法启动 PPT 放映，请确认已安装 PowerPoint 或 WPS 演示，且文稿能正常打开。')

    @staticmethod
    def _run_open_document(document, windowed):
        if not windowed:
            return document.SlideShowSettings.Run()
        settings = document.SlideShowSettings
        original, scrollbar, saved = settings.ShowType, settings.ShowScrollbar, document.Saved
        try:
            settings.ShowType = 2
            settings.ShowScrollbar = 0
            return settings.Run()
        finally:
            # 只影响本次启动；保留用户文稿的原设置及未保存状态。
            try:
                settings.ShowScrollbar = scrollbar
            finally:
                try:
                    settings.ShowType = original
                finally:
                    document.Saved = saved

    def _show_index(self):
        windows = self.application.SlideShowWindows
        for index in range(1, int(windows.Count) + 1):
            try:
                if windows.Item(index)._oleobj_ == self.window._oleobj_:
                    return index
            except AttributeError:
                if windows.Item(index) is self.window:
                    return index
        raise RuntimeError('未找到刚启动的 PPT 放映。')

    def remember_position(self):
        try:
            view = self.presentation.window.View
            page = int(view.Slide.SlideIndex)
            try:
                click = int(view.GetClickIndex())
            except Exception:
                click = None
            self.original_position = page, click
        except Exception:
            pass

    def refresh_window(self):
        if self.presentation.hwnd:
            self.presentation._verify_window()
            return True
        if self.application is not None:
            self.presentation.show_index = self._show_index()
        self.presentation.restore_auto_window()
        return True

    def close(self, *, restore_position=True):
        if self.presentation is not None:
            self.presentation.restore()
            if restore_position and self.original_position is not None and self.presentation.is_active():
                try:
                    page, click = self.original_position
                    view = self.presentation.window.View
                    view.GotoSlide(page, True)
                    if click is not None and self.presentation.provider != 'WPS 演示':
                        view.GotoClick(click)
                except Exception:
                    logger.warning('无法恢复原放映位置', exc_info=True)
            self.presentation = None
            self.original_position = None
        # 只读副本的放映设置和加载项也可能标记为已修改。
        # 退出放映前丢弃副本变更，避免保存提示阻塞清理；不涉及用户文稿。
        if self.document is not None and self.directory is not None:
            try:
                self.document.Saved = -1
            except Exception:
                logger.debug('无法清除放映副本的修改状态', exc_info=True)
        if self.window is not None:
            try:
                self.window.View.Exit()
            except Exception:
                logger.debug('本次放映已结束', exc_info=True)
            self.window = None
        if self.document is not None:
            try:
                self.document.Close()
            except Exception as error:
                # 隐藏打开的 PowerPoint 文稿可能随放映退出而自动关闭。
                still_open = True
                try:
                    documents = self.application.Presentations
                    still_open = any(documents.Item(index)._oleobj_ == self.document._oleobj_
                                     for index in range(1, int(documents.Count) + 1))
                except Exception:
                    pass
                if still_open:
                    logger.warning('无法关闭放映文稿副本', exc_info=True)
                else:
                    logger.debug('放映文稿副本已随放映关闭')
            self.document = None
        if self.application is not None and self.owned_pid:
            try:
                if int(self.application.Presentations.Count) == 0:
                    self.application.Quit()
            except Exception:
                logger.debug('未退出放映使用的演示软件', exc_info=True)
        self.application = None
        self.owned_pid = None
        if self.directory is not None:
            try:
                self.directory.cleanup()
            except OSError:
                logger.warning('放映文稿副本清理失败', exc_info=True)
            self.directory = None
