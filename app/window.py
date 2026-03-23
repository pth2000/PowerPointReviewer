"""主窗口装配模块"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, SplashScreen, setThemeColor

from app.app_context import AppContext
from ui.pages.reviewer_page import PPTReviewer
from ui.pages.settings_page import SettingInterface
from ui.pages.tools_page import ToolsInterface


class Window(FluentWindow):
    """主窗体：负责装配页面"""

    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        setThemeColor('#B7472A')
        self.resize(850, 750)
        self.setWindowTitle('PowerPointReviewer')
        self.setWindowIcon(QIcon(':/image/image/ppt_ico.svg'))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.show()

        self.ppt_r = PPTReviewer(self.context, self)
        self.setting_interface = SettingInterface(self.context, self)
        self.tools_interface = ToolsInterface(self.ppt_r, self)

        self.addSubInterface(self.ppt_r, FluentIcon.HOME, '主页')
        self.addSubInterface(self.tools_interface, FluentIcon.APPLICATION, '实用工具')
        self.addSubInterface(self.setting_interface, FluentIcon.SETTING, '设置', NavigationItemPosition.BOTTOM)
        self.splashScreen.finish()
