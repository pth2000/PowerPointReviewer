"""程序入口"""

import sys
from PySide6.QtWidgets import QApplication

from app.app_context import AppContext
from app.window import Window
from tts_engine import TTSEngine


VERSION = '1.4.1'


def main():
    """应用启动入口"""
    context = AppContext(
        version=VERSION,
        tts_engine=TTSEngine(),
    )

    app = QApplication(sys.argv)
    window = Window(context)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())

