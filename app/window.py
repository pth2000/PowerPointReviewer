"""装配主窗口导航，并协调页面之间的少量跨页事件。"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, SplashScreen

from app.app_context import AppContext
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.settings_page import SettingInterface
from ui.pages.tools_page import ToolsInterface


class Window(FluentWindow):
    """应用主窗体，负责页面生命周期和跨页信号接线。"""

    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.resize(850, 750)
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

        # 设置页通过回调和信号表达意图，避免直接依赖主页实例。
        self.setting_interface.can_regenerate_check = (
            lambda: bool(self.ppt_r.notes) and not self.ppt_r.is_busy())
        self.setting_interface.regenerate_requested.connect(self.ppt_r.regenerate)

        self.splashScreen.finish()

    def closeEvent(self, event):
        """处理未保存设置，并在退出前清空延迟写入队列。"""
        # 窗口即将关闭，此时重新生成音频没有可见收益。
        self.setting_interface.prompt_unsaved_changes(allow_regenerate=False)
        self.context.config.flush()
        super().closeEvent(event)
