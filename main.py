"""创建 Qt 应用、恢复持久化配置并启动主窗口。"""

import sys

from PySide6.QtWidgets import QApplication

from app import logging_setup, paths
from app.app_context import AppContext
from app.updates import manager as updater
from app.window import Window
from app.workspaces import start_cleanup
from tts_engine import TTSEngine
from ui import theme
from ui.tooltips import install_fluent_tooltips

VERSION = '1.9.0'


def main():
    """启动应用并返回 Qt 事件循环的退出码。"""
    updated_to = updater.run_startup_hooks()
    # AppContext 会构造 QObject；Qt 要求先存在 QApplication 实例。
    logging_setup.setup(VERSION)
    if updated_to:
        print(f'[更新] 已更新至 {updated_to}')

    app = QApplication(sys.argv)
    install_fluent_tooltips(app)
    logging_setup.log_display()
    paths.ensure_runtime_dirs()
    start_cleanup()

    context = AppContext(
        version=VERSION,
        tts_engine=TTSEngine(),
        updated_to=updated_to,
    )
    # 页面会在构造阶段读取偏好，因此必须先恢复配置再创建窗口。
    context.config.load()
    theme.apply_theme(context.app_settings.get('theme_mode'),
                      context.app_settings.get('theme_color'))
    theme.apply_font(context.app_settings.get('ui_font'))


    window = Window(context)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
