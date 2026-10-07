"""创建 Qt 应用、恢复持久化配置并启动主窗口。"""

import sys
from PySide6.QtWidgets import QApplication

from app import logging_setup, paths, theme, updater
from app.app_context import AppContext
from app.window import Window
from tts_engine import TTSEngine


VERSION = '1.6.1'


def main():
    """启动应用并返回 Qt 事件循环的退出码。"""
    updated_to = updater.run_startup_hooks()
    # AppContext 会构造 QObject；Qt 要求先存在 QApplication 实例。
    logging_setup.setup(VERSION)
    if updated_to:
        print(f'[更新] 已更新至 {updated_to}')

    app = QApplication(sys.argv)
    logging_setup.log_display()
    paths.ensure_runtime_dirs()

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

