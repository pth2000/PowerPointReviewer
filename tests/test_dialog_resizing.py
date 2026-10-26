"""小型表单按内容固定尺寸，避免手动拉伸破坏布局。"""

from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from app.settings_store import AppSettings
from qt_test_case import QtTestCase
from ui.dialogs.audio_merge_dialog import AudioMergeDialog
from ui.dialogs.hotkey_dialog import HotkeyDialog
from ui.dialogs.presentation_link_dialog import PresentationLinkDialog


class FormDialogResizeTests(QtTestCase):
    def dispose(self, dialog):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def assert_fixed_form(self, dialog, width):
        dialog.show()
        QTest.qWait(20)
        size = dialog.size()
        self.assertEqual(size.width(), width)
        self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
        self.assertFalse(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
        dialog.resize(1600, 1000)
        QTest.qWait(20)
        self.assertEqual(dialog.size(), size)

    def test_hotkey_window_cannot_be_stretched(self):
        dialog = HotkeyDialog(AppSettings(), Mock())
        self.addCleanup(self.dispose, dialog)
        self.assert_fixed_form(dialog, 520)

    def test_merge_progress_does_not_stretch_cancel_button_or_text(self):
        with patch('ui.dialogs.audio_merge_dialog.AudioMergeTask') as task_type:
            task_type.return_value.isRunning.return_value = False
            task_type.return_value.isFinished.return_value = True
            dialog = AudioMergeDialog([], 'unused.wav')
        self.addCleanup(self.dispose, dialog)
        self.assert_fixed_form(dialog, 400)
        self.assertLess(dialog.cancel_button.width(), 120)

    def test_presentation_link_window_cannot_be_stretched(self):
        with patch('ui.dialogs.presentation_link_dialog.slideshow.list_open_presentations', return_value=[]):
            dialog = PresentationLinkDialog({}, [])
        self.addCleanup(self.dispose, dialog)
        self.assert_fixed_form(dialog, 520)
        height = dialog.height()
        dialog.error_label.setText('未能读取所选文稿，请重新选择。')
        dialog.error_label.show()
        QTest.qWait(20)
        self.assertEqual(dialog.width(), 520)
        self.assertGreater(dialog.height(), height)
        self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
