"""Qt 界面测试共用的应用生命周期初始化。"""

import unittest

from PySide6.QtWidgets import QApplication


class QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qt = QApplication.instance() or QApplication([])
