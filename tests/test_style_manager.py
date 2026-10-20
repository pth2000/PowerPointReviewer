"""场景与模板草稿、预览、保存校验和关闭行为。"""

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from app.ai import rewrite
from app.settings_store import AppSettings
from qt_test_case import QtTestCase
from ui.dialogs.rewrite_dialog import RewriteDialog
from ui.dialogs.style_manager_dialog import StyleManagerDialog


class SceneManagerTests(QtTestCase):
    def setUp(self):
        self.settings = AppSettings()
        self.before = self.settings.to_dict()
        self.dialog = StyleManagerDialog(self.settings, '●')
        self.addCleanup(self.dispose)
        patcher = patch.object(self.dialog, '_info')
        self.notice = patcher.start()
        self.addCleanup(patcher.stop)

    def dispose(self):
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def test_drafts_survive_navigation_and_cancel_does_not_save(self):
        dialog = self.dialog
        dialog.new_button.click()
        row = dialog.style_list.currentRow()
        dialog.name_edit.setText('我的技术讲解')
        dialog.instruction_edit.setPlainText('只修改明确误读的术语。')
        dialog.pivot.widget('templates').click()
        dialog.system_edit.setPlainText('我自己的通用原则。')
        dialog.pivot.widget('styles').click()
        dialog.style_list.setCurrentRow(0)
        dialog.style_list.setCurrentRow(row)
        self.assertEqual(dialog.name_edit.text(), '我的技术讲解')
        self.assertEqual(dialog.instruction_edit.toPlainText(), '只修改明确误读的术语。')
        self.assertEqual(dialog.system_edit.toPlainText(), '我自己的通用原则。')
        dialog.cancel_button.click()
        self.assertEqual(self.settings.to_dict(), self.before)

    def test_preview_tracks_scene_and_template_edits_across_sections(self):
        dialog = self.dialog
        dialog.show()
        self.qt.processEvents()
        self.assertTrue(dialog.preview_panel.isHidden())
        dialog.preview_button.click()
        self.assertTrue(dialog.preview_panel.isVisible())
        dialog.instruction_edit.setPlainText('仅修正英文缩写。')
        dialog.pivot.widget('templates').click()
        for row, editor, text in ((0, dialog.system_edit, '共同约束。'),
                                  (1, dialog.user_edit, '要求：{instruction}\n{segment_rule}\n讲稿：{text}'),
                                  (2, dialog.segment_edit, '原样保留 {segment_count} 个 {mark}。')):
            dialog.template_list.setCurrentRow(row)
            self.assertIs(dialog.template_editors.currentWidget(), editor)
            editor.setPlainText(text)
        preview = dialog.preview_edit.toPlainText()
        self.assertIn('共同约束。', preview)
        self.assertIn('仅修正英文缩写。', preview)
        self.assertIn('原样保留 1 个 ●。', preview)
        self.assertIn(dialog.SAMPLE_SEGMENT, preview)
        dialog.pivot.widget('styles').click()
        self.assertTrue(dialog.preview_panel.isVisible())
        dialog.preview_button.click()
        self.assertTrue(dialog.preview_panel.isHidden())

    def test_invalid_hidden_template_opens_the_correct_section_before_saving(self):
        dialog = self.dialog
        dialog.user_edit.setPlainText('缺少讲稿正文占位符。')
        dialog.ok_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.navigation.currentIndex(), 1)
        self.assertEqual(dialog.stack.currentIndex(), 1)
        self.assertEqual(dialog.heading_stack.currentIndex(), 1)
        self.assertEqual(dialog.template_list.currentRow(), 1)
        self.assertEqual(self.settings.to_dict(), self.before)
        dialog.user_edit.setPlainText('要求：{instruction}\n讲稿：{text}')
        dialog.ok_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.settings.get('llm_templates')['user_template'], '要求：{instruction}\n讲稿：{text}')

    def test_renaming_the_selected_scene_keeps_the_main_selection(self):
        parent = RewriteDialog({1: '讲稿'}, '●', self.settings)
        self.addCleanup(parent.deleteLater)
        parent.style_combo.setCurrentText('教学讲解')
        dialog = StyleManagerDialog(self.settings, '●', selected_style=parent.style_combo.currentText())
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.name_edit.text(), '教学讲解')
        dialog.name_edit.setText('课程讲解')
        dialog.ok_button.click()
        self.assertEqual(self.settings.get('llm_style'), '课程讲解')
        parent.reload_styles()
        self.assertEqual(parent.style_combo.currentText(), '课程讲解')

    def test_duplicate_scene_name_returns_to_the_invalid_field(self):
        dialog = self.dialog
        dialog.style_list.setCurrentRow(1)
        dialog.name_edit.setText('朗读纠错')
        dialog.pivot.widget('templates').click()
        dialog.ok_button.click()
        self.assertEqual(dialog.stack.currentIndex(), 0)
        self.assertEqual(dialog.navigation.currentIndex(), 0)
        self.assertEqual(dialog.style_list.currentRow(), 1)
        self.assertEqual(self.settings.to_dict(), self.before)

    def test_delete_and_restore_only_change_the_draft_until_save(self):
        dialog = self.dialog
        dialog.new_button.click()
        dialog.name_edit.setText('待删除的场景')
        with patch('ui.dialogs.style_manager_dialog.MessageBox') as confirm:
            confirm.return_value.exec.return_value = True
            dialog.delete_button.click()
            self.assertIn('待删除的场景', confirm.call_args.args[1])
            self.assertNotIn('待删除的场景', rewrite.style_names(dialog.styles))
            dialog.reset_button.click()
        self.assertEqual(dialog.styles, rewrite.default_styles())
        self.assertEqual(self.settings.to_dict(), self.before)
        self.settings.set('llm_styles', [{'name': '唯一场景', 'instruction': '保持正文。'}])
        single = StyleManagerDialog(self.settings, '●')
        self.addCleanup(single.deleteLater)
        self.assertFalse(single.delete_button.isEnabled())

    def test_escape_closes_both_dialogs_without_applying_changes(self):
        self.dialog.show()
        self.dialog.instruction_edit.setPlainText('尚未保存的要求。')
        QTest.keyClick(self.dialog, Qt.Key.Key_Escape)
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.settings.to_dict(), self.before)
        rewrite_dialog = RewriteDialog({1: '原文'}, '●', self.settings)
        self.addCleanup(rewrite_dialog.deleteLater)
        rewrite_dialog.on_page_done(1, '候选修改', '')
        rewrite_dialog.show()
        QTest.keyClick(rewrite_dialog, Qt.Key.Key_Escape)
        self.assertFalse(rewrite_dialog.isVisible())
        self.assertEqual(rewrite_dialog.get_applied(), {})
        self.assertEqual(rewrite_dialog.notes, {1: '原文'})
