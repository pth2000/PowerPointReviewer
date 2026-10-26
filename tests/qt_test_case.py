"""Qt 界面测试共用的应用生命周期初始化。"""

import unittest
import time

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


class QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qt = QApplication.instance() or QApplication([])

    def wait_for_history(self, dialog, timeout=10):
        deadline = time.monotonic() + timeout
        while dialog._loader is not None or dialog._pending_rows or dialog._render_timer.isActive():
            self.qt.processEvents()
            if time.monotonic() > deadline:
                dialog.reject()
                self.fail('历史记录后台读取超时')
            QTest.qWait(5)
