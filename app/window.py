"""装配主窗口导航，并协调页面之间的少量跨页事件。"""

from PySide6.QtCore import QSize, QThread, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentIcon, FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition, SplashScreen,
)

from app.app_context import AppContext
from ui.file_drop import FileDropImport
from ui.onboarding import UsageGuide
from ui.widget_state import active_modal_dialog
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.settings_page import SettingInterface
from ui.pages.tools_page import ToolsInterface


class Window(FluentWindow):
    """应用主窗体，负责页面生命周期和跨页信号接线。"""

    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.resize(850, 750)
        # 页面内容最宽的一屏约需 680，加上导航栏与边距即为可用下限
        self.setMinimumSize(QSize(760, 560))
        self.setWindowTitle('PowerPointReviewer')
        self.setWindowIcon(QIcon(':/image/image/ppt_ico.svg'))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.show()
        # 页面构造是同步的，期间事件循环尚未启动。不主动跑一次事件处理，
        # 启动页在 finish() 之前得不到绘制机会，用户看到的就是一片空窗。
        QApplication.processEvents()

        self.ppt_r = PPTReviewer(self.context, self)
        self.setting_interface = SettingInterface(self.context, self)
        self.tools_interface = ToolsInterface(self.context, self.ppt_r, self)

        self.addSubInterface(self.ppt_r, FluentIcon.HOME, '主页')
        self.addSubInterface(self.tools_interface, FluentIcon.APPLICATION, '实用工具')
        self.addSubInterface(self.setting_interface, FluentIcon.SETTING, '设置', NavigationItemPosition.BOTTOM)
        self.file_drop_import = FileDropImport(
            self, self._import_dropped_files, overlay_parent=self.stackedWidget,
            busy_check=self.ppt_r.is_busy, linked_check=self.ppt_r.has_presentation)

        # 设置页通过回调和信号表达意图，避免直接依赖主页实例。
        self.setting_interface.can_regenerate_check = (
            lambda: bool(self.ppt_r.notes) and not self.ppt_r.is_busy())
        self.setting_interface.regenerate_requested.connect(self.ppt_r.regenerate)

        self.context.hotkeys.triggered.connect(self.ppt_r.handle_hotkey)
        self.context.slideshow_watcher.slide_changed.connect(
            self.ppt_r.on_slideshow_slide_changed)
        self.context.slideshow_watcher.show_ended.connect(self.ppt_r.on_slideshow_ended)
        self.context.slideshow_watcher.show_checked.connect(self.ppt_r.on_slideshow_checked)
        self.setting_interface.page_turn_mode_changed.connect(
            self.ppt_r.refresh_slideshow_watch)
        self._bind_sync_playback()
        self.ppt_r.refresh_slideshow_watch()
        self.setting_interface.hotkeys_changed.connect(self.ppt_r.refresh_hotkeys)

        self.usage_guide = UsageGuide(self)

        self.splashScreen.finish()
        if self.context.updated_to:
            QTimer.singleShot(300, self.show_updated_tip)
        QTimer.singleShot(750, self, self._show_startup_guidance)

    def _show_startup_guidance(self):
        if self.usage_guide.step is not None:
            return
        self.ppt_r.prompt_resume_session()
        self.usage_guide.maybe_offer()

    def _bind_sync_playback(self):
        page, settings = self.ppt_r, self.setting_interface
        settings.sync_playback_available_check = page.can_sync_playback
        settings.sync_playback_unavailable_hint = page.sync_playback_unavailable_reason
        settings.page_turn_source = page.slideshow_source
        settings.page_turn_description = page.describe_slideshow
        settings.sync_playback_changed.connect(page.set_sync_playback_enabled)
        settings.slideshow_preferences_changed.connect(page.refresh_slideshow_watch)
        page.sync_playback_changed.connect(lambda _enabled: settings.refresh_sync_playback_state())
        page.presentation_state_changed.connect(settings.refresh_sync_playback_state)
        page.save_thread.started.connect(settings.refresh_sync_playback_state)
        page.save_thread.finished.connect(settings.refresh_sync_playback_state)
        settings.refresh_sync_playback_state()

    def show_updated_tip(self):
        """更新后首次启动时提示新版本号。"""
        InfoBar.success('已更新', f'当前版本 {self.context.updated_to}', isClosable=True,
                        position=InfoBarPosition.TOP, duration=5000, parent=self.ppt_r)

    def _import_dropped_files(self, files):
        if active_modal_dialog(self) is not None:
            return
        settings = getattr(self, 'setting_interface', None)
        if settings is not None and settings.is_dirty():
            if not settings.prompt_unsaved_changes(allow_regenerate=False):
                return
        self.switchTo(self.ppt_r)
        if len(files) != 1:
            self.ppt_r.create_warning_info_bar('请选择一个文件', '每次拖入一个讲稿文件或工程包')
            return
        self.ppt_r.import_file(files[0])

    def closeEvent(self, event):
        """处理未保存设置，并在退出前清空延迟写入队列。"""
        workers = [task for task in self.findChildren(QThread) if task.isRunning()]
        if workers:
            event.ignore()
            if not getattr(self, '_waiting_to_close', False):
                self._waiting_to_close = True
                self.setting_interface.on_stop_requested(self)
                if self.ppt_r.save_thread.isRunning():
                    self.ppt_r.save_thread.requestInterruption()
                audio_review = getattr(self.ppt_r, 'audio_review_dialog', None)
                if audio_review is not None and audio_review.task is not None:
                    audio_review.task.requestInterruption()
                for worker in workers:
                    worker.finished.connect(self._finish_pending_close)
                InfoBar.warning('正在退出', '正在结束当前任务，完成后将自动退出。',
                                isClosable=False, position=InfoBarPosition.TOP,
                                duration=-1, parent=self.ppt_r)
                self.setEnabled(False)
            return
        self._waiting_to_close = False
        self.setEnabled(True)
        audio_review = getattr(self.ppt_r, 'audio_review_dialog', None)
        if audio_review is not None and audio_review.isVisible() and not audio_review.close():
            event.ignore()
            return
        # 窗口即将关闭，此时重新生成音频没有可见收益。
        if self.setting_interface.prompt_unsaved_changes(allow_regenerate=False) is False:
            event.ignore()
            return
        if not self.ppt_r.presentation_monitor.shutdown():
            event.ignore()
            return
        self.ppt_r.remember_session()
        self.ppt_r.stop_audio()
        self.ppt_r.release_playback_show()
        self.context.hotkeys.clear()
        self.context.slideshow_watcher.stop()
        self.context.config.flush()
        super().closeEvent(event)

    def _finish_pending_close(self):
        if getattr(self, '_waiting_to_close', False):
            self.close()
