"""分隔符示例、输入校验及窄窗口下的弹窗布局。"""

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QWidget

from qt_test_case import QtTestCase
from ui.dialogs.edit_mark_dialog import EditMarkMessageBox


class EditMarkDialogTests(QtTestCase):
    def setUp(self):
        self.window = QWidget()
        self.window.resize(760, 560)
        self.window.show()
        self.dialog = EditMarkMessageBox('●', self.window, has_script=True)
        self.dialog.show()
        self.qt.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def test_custom_separator_updates_example_and_restore_default(self):
        self.dialog.mark_edit.setText(' || ')
        self.assertEqual(self.dialog.get_mark(), '||')
        self.assertIn('||', self.dialog.example_text.text())
        self.assertIn('重新生成', self.dialog.hint_label.text())
        self.dialog.reset_button.click()
        self.assertEqual(self.dialog.get_mark(), '●')
        self.assertNotIn('重新生成', self.dialog.hint_label.text())

    def test_blank_separator_cannot_be_saved(self):
        for mark in ('', '   ', '\t'):
            self.dialog.mark_edit.setText(mark)
            self.assertFalse(self.dialog.validate())
            self.assertFalse(self.dialog.yesButton.isEnabled())
            self.assertIn('非空白', self.dialog.hint_label.text())

    def test_separator_is_escaped_and_long_values_do_not_expand_dialog(self):
        self.dialog.mark_edit.setText('<&>')
        self.assertIn('&lt;&amp;&gt;', self.dialog.example_text.text())
        self.dialog.mark_edit.setText('abcdefghij' * 40)
        self.qt.processEvents()
        self.assertEqual(self.dialog.get_mark(), 'abcdefghij' * 40)
        self.assertLessEqual(self.dialog.widget.width(), self.window.width() - 40)
        self.assertTrue(self.window.rect().contains(self.dialog.widget.geometry()))

    def test_example_has_two_parts_even_with_punctuation_or_chinese_separator(self):
        for mark in ('●', '。', '介', '||', '<&>'):
            self.dialog.mark_edit.setText(mark)
            document = QTextDocument()
            document.setHtml(self.dialog.example_text.text())
            parts = document.toPlainText().split(mark)
            self.assertEqual(len(parts), 2)
            self.assertTrue(all(part.strip() for part in parts))

    def test_dialog_fits_small_window_and_accepts_valid_separator(self):
        self.assertTrue(self.window.rect().contains(self.dialog.widget.geometry()))
        self.dialog.mark_edit.setText('||')
        QTimer.singleShot(0, self.dialog.yesButton.click)
        self.assertEqual(self.dialog.exec(), self.dialog.DialogCode.Accepted)
