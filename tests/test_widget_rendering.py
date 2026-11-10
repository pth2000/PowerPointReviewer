"""检查控件实际绘制的文字，避免只验证其内部数值。"""

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSignalBlocker, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox
from qfluentwidgets import DoubleSpinBox, SpinBox, Theme, qconfig, setTheme
from qt_test_case import QtTestCase
from test_sync_playback import SyncPlaybackFixture


class NumericRenderAssertions:
    def assert_numeric_rendered(self, widget):
        editor = widget.lineEdit()
        # 长值允许横向滚动，但编辑区至少应容纳一个完整数字和边距。
        required = editor.fontMetrics().horizontalAdvance('0') + 4
        self.assertGreaterEqual(editor.width(), required)
        self.assertTrue(widget.rect().contains(editor.geometry()))
        self.assertTrue(editor.isVisibleTo(widget))
        self.assert_editor_rendered(editor)

    def assert_editor_rendered(self, editor):
        editor.deselect()
        editor.setCursorPosition(0)
        text = editor.text()
        self.assertTrue(text)
        painted = editor.grab().toImage()
        with QSignalBlocker(editor):
            editor.setText('')
            blank = editor.grab().toImage()
            editor.setText(text)
        self.assertEqual(painted.size(), blank.size())
        differences = sum(a != b for a, b in zip(
            bytes(painted.constBits()), bytes(blank.constBits())))
        self.assertGreater(differences, 12, '数值文字未绘制到输入框中')


class NumericRenderingTests(NumericRenderAssertions, QtTestCase):
    def test_numeric_editors_have_space_and_paint_their_values(self):
        self.addCleanup(setTheme, qconfig.themeMode.value)
        for theme in (Theme.LIGHT, Theme.DARK):
            setTheme(theme)
            self.check_numeric_widgets()

    def check_numeric_widgets(self):
        for widget_type in (SpinBox, DoubleSpinBox):
            for width, symbols in ((88, False), (120, True), (180, True)):
                for enabled in (True, False):
                    with self.subTest(widget=widget_type.__name__, width=width, enabled=enabled):
                        widget = widget_type()
                        try:
                            widget.setFixedWidth(width)
                            widget.setRange(-999, 999)
                            widget.setValue(12)
                            widget.setSymbolVisible(symbols)
                            widget.setEnabled(enabled)
                            widget.show()
                            QTest.qWait(10)
                            self.assert_numeric_rendered(widget)
                        finally:
                            widget.close()
                            widget.deleteLater()
                            self.qt.processEvents()


class PageRenderingTests(NumericRenderAssertions, SyncPlaybackFixture):
    def test_all_engine_settings_and_home_numeric_fields_remain_visible(self):
        self.page.show()
        self.settings.resize(850, 750)
        self.settings.show()
        engine = self.ctx.tts_engine
        with patch.object(engine, 'get_voices_list', return_value=['Test voice']), \
             patch.object(engine, 'list_qwen_clone_voice_items', return_value=[]):
            for index in range(self.settings.engineSelectComboBox.count()):
                self.settings.engineSelectComboBox.setCurrentIndex(index)
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                QTest.qWait(20)
                for key, control in self.settings.dynamic_option_widgets.items():
                    with self.subTest(engine=engine.get_mode(), option=key):
                        self.assertEqual(control.width(), self.settings.engineSelectComboBox.width())
                        self.assertEqual(control.height(), 33)
                fields = [field for field in self.settings.findChildren(QAbstractSpinBox)
                          if field.isVisibleTo(self.settings)]
                self.assertTrue(fields)
                for field in fields:
                    with self.subTest(engine=engine.get_mode(), field=field.objectName(),
                                      value=field.text()):
                        self.assert_numeric_rendered(field)
        duration = self.page.currentSpinBox
        duration.begin_edit()
        self.qt.processEvents()
        self.assertTrue(duration.editor.isVisibleTo(self.page))
        self.assertGreater(duration.editor.width(), 20)
        self.assert_editor_rendered(duration.editor)
        QTest.keyClick(duration.editor, Qt.Key.Key_Escape)

    def test_settings_dropdown_items_are_visible_and_can_be_selected(self):
        self.addCleanup(setTheme, qconfig.themeMode.value)
        self.settings.show()
        for combo in (self.settings.engineSelectComboBox, self.settings.theme_mode_combo,
                      self.settings.presentation_preference_combo):
            self.settings.bgScrollArea.ensureWidgetVisible(combo)
            QTest.qWait(20)
            combo._showComboMenu()
            try:
                QTest.qWait(200)
                menu = combo.dropMenu
                self.assertIsNotNone(menu)
                self.assertTrue(menu.isVisible())
                self.assertEqual(menu.view.count(), combo.count())
                for index in range(menu.view.count()):
                    item = menu.view.item(index)
                    self.assertTrue(item.text().strip())
                    rect = menu.view.visualItemRect(item)
                    self.assertGreater(rect.width(), 20)
                    self.assertGreater(rect.height(), 10)
                if combo is self.settings.theme_mode_combo:
                    rect = menu.view.visualItemRect(menu.view.item(1))
                    QTest.mouseClick(menu.view.viewport(), Qt.MouseButton.LeftButton,
                                     pos=rect.center())
                    self.assertEqual(combo.currentIndex(), 1)
            finally:
                if combo.dropMenu is not None:
                    combo.dropMenu.close()
                self.qt.processEvents()
