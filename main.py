"""创建 Qt 应用、恢复持久化配置并启动主窗口。"""

import sys
from PySide6.QtWidgets import QApplication

from app import paths, theme
from app.app_context import AppContext
from app.window import Window
from tts_engine import TTSEngine


VERSION = '1.5.1'


def main():
    """启动应用并返回 Qt 事件循环的退出码。"""
    # AppContext 会构造 QObject；Qt 要求先存在 QApplication 实例。
    app = QApplication(sys.argv)
    paths.ensure_runtime_dirs()

    context = AppContext(
        version=VERSION,
        tts_engine=TTSEngine(),
    )
    # 页面会在构造阶段读取偏好，因此必须先恢复配置再创建窗口。
    context.config.load()
    theme.apply_theme(context.app_settings.get('theme_mode'),
                      context.app_settings.get('theme_color'))


    window = Window(context)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

