"""优化场景、外部任务往返、候选审查与自定义配置兼容。"""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtWidgets import QDialog
from PySide6.QtTest import QTest

from app.ai import exchange, rewrite
from app.settings_store import AppSettings
from qt_test_case import QtTestCase
from ui.dialogs.rewrite_dialog import RewriteDialog, STATUS_UNCHANGED, STATUS_WARN
from ui.dialogs.rewrite_exchange_dialog import RewriteExchangeDialog


def result_payload(notes, mark='●'):
    return {'format': exchange.RESULT_FORMAT, 'version': 1, 'pages': [
        {'page': page, 'source_hash': exchange.source_hash(page, text, mark),
         'segments': text.split(mark) if mark else [text]}
        for page, text in notes.items()]}


class OptimizationDataTests(unittest.TestCase):
    def setUp(self):
        self.notes = {1: '🙂PHY 电压为 3.3 V。●延迟为 25 ms。', 4: '结论保持不变。'}

    def test_export_contains_selected_pages_rules_and_return_contract(self):
        instruction = rewrite.BUILTIN_STYLES[0]['instruction']
        task = exchange.build_task([(4, self.notes[4])], mark='●', instruction=instruction)
        self.assertIn('结论保持不变', task)
        self.assertNotIn('电压为', task)
        self.assertIn(instruction, task)
        self.assertIn('不得自行猜测读音', task)
        self.assertIn(exchange.source_hash(4, self.notes[4], '●'), task)
        self.assertIn(exchange.RESULT_FORMAT, task)
        self.assertNotIn('api_key', task)

    def test_export_writes_shared_instructions_once_for_all_pages(self):
        notes = {page: f'本页独有内容 {page}。●第二段 {page}。' for page in range(1, 21)}
        instruction = rewrite.BUILTIN_STYLES[0]['instruction']
        task = exchange.build_task(notes.items(), mark='●', instruction=instruction)
        self.assertEqual(task.count(rewrite.DEFAULT_SYSTEM_PROMPT), 1)
        self.assertEqual(task.count(instruction), 1)
        for text in notes.values():
            for segment in text.split('●'):
                self.assertEqual(task.count(segment), 1)
        input_text, example_text = task.split('输入讲稿：\n', 1)[1].split('\n\n返回格式', 1)
        inputs = json.loads(input_text)
        self.assertEqual([item['page'] for item in inputs], list(notes))
        self.assertEqual([item['segments'] for item in inputs], [text.split('●') for text in notes.values()])
        for item in inputs:
            self.assertEqual(item['source_hash'], exchange.source_hash(item['page'], notes[item['page']], '●'))
        example = json.loads(example_text[example_text.index('{'):])
        self.assertEqual(len(example['pages']), 1)

    def test_batch_export_preserves_custom_rules_without_repeating_them(self):
        templates = {
            'system_prompt': '用户自定义系统规则',
            'user_template': '自定义要求：{instruction}\n{segment_rule}\n原文：{text}',
            'segment_rule': '用户分段规则：{mark}，分隔符数是 {segment_count}。',
        }
        notes = {1: '首段||||尾段||', 9: '不要展开 {instruction}。\n下一行。'}
        task = exchange.build_task(notes.items(), mark='||', instruction='用户选定的任务要求', templates=templates)
        for rule in ('用户自定义系统规则', '自定义要求：', '用户选定的任务要求', '用户分段规则：'):
            self.assertEqual(task.count(rule), 1)
        self.assertIn('该页 segments 的项数减一', task)
        inputs = json.loads(task.split('输入讲稿：\n', 1)[1].split('\n\n返回格式', 1)[0])
        self.assertEqual(inputs[0]['segments'], ['首段', '', '尾段', ''])
        self.assertEqual(inputs[1]['segments'], [notes[9]])

    def test_roundtrip_keeps_unicode_partial_pages_and_surrounding_code_fence(self):
        payload = result_payload({1: self.notes[1]})
        payload['pages'][0]['segments'][1] = '延迟为二十五毫秒。'
        result = exchange.parse_result('\ufeff```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```', self.notes, '●')
        self.assertEqual(result, {1: '🙂PHY 电压为 3.3 V。●延迟为二十五毫秒。'})
        self.assertEqual(self.notes[4], '结论保持不变。')

    def test_stale_source_changed_separator_unknown_and_duplicate_pages_are_rejected(self):
        for change in ('stale', 'separator', 'unknown', 'duplicate'):
            with self.subTest(change=change):
                payload = result_payload(self.notes)
                if change == 'stale':
                    payload['pages'][1]['source_hash'] = 'old-source'
                elif change == 'separator':
                    payload['pages'][0]['source_hash'] = exchange.source_hash(1, self.notes[1], '|')
                elif change == 'unknown':
                    payload['pages'][1]['page'] = 99
                else:
                    payload['pages'].append(copy.deepcopy(payload['pages'][0]))
                with self.assertRaises(ValueError):
                    exchange.parse_result(json.dumps(payload), self.notes, '●')

    def test_invalid_and_empty_content_is_not_coerced_to_script(self):
        for value in (None, [], [''], [123], [{'text': '不能当成讲稿'}]):
            with self.subTest(value=value):
                payload = result_payload(self.notes)
                payload['pages'][0]['segments'] = value
                with self.assertRaises(ValueError):
                    exchange.parse_result(json.dumps(payload), self.notes, '●')
        with self.assertRaises(ValueError):
            exchange.parse_result('{"pages": [], "pages": []}', self.notes, '●')
        with self.assertRaises(ValueError):
            exchange.parse_result('普通改写结果，没有页码', self.notes, '●')

    def test_custom_separator_and_empty_segments_survive_roundtrip(self):
        notes = {5: '开头||结尾|'}
        self.assertEqual(exchange.parse_result(json.dumps(result_payload(notes, '|')), notes, '|'), notes)
        notes = {5: '单段讲稿'}
        self.assertEqual(exchange.parse_result(json.dumps(result_payload(notes, '')), notes, ''), notes)

    def test_segment_change_is_reviewable_instead_of_silently_discarded(self):
        payload = result_payload(self.notes)
        payload['pages'][0]['segments'] = ['将原有两段合并后的内容']
        result = exchange.parse_result(json.dumps(payload), self.notes, '●')
        self.assertIn('分隔符数量', rewrite.check_segments(self.notes[1], result[1], '●'))

    def test_zero_separator_rule_and_literal_template_tokens_in_source(self):
        text = '不要展开原文里的 {instruction} 和 {text}。'
        prompt = rewrite.build_user_prompt(text, instruction='保留原文')
        self.assertIn('恰好是 0 个', prompt)
        self.assertIn(text, prompt)

    def test_legacy_defaults_upgrade_without_overwriting_customized_presets(self):
        settings = AppSettings()
        old = {'name': '更精简', 'instruction': '在不丢失关键信息的前提下压缩篇幅，删掉冗余修饰与重复表述，让每句话都有信息量。'}
        custom = {'name': '朗读友好', 'instruction': '这是用户自己修改的要求'}
        template = {'system_prompt': '我自己的系统提示词', 'user_template': '自定义 {text}'}
        settings.load_from({'llm_styles': [old, custom], 'llm_style': '更精简', 'llm_templates': template})
        self.assertEqual(settings.get('llm_style'), '精简讲解')
        self.assertIn(custom, settings.get('llm_styles'))
        self.assertNotIn(old, settings.get('llm_styles'))
        self.assertEqual(settings.get('llm_templates')['user_template'], template['user_template'])
        self.assertEqual(settings.get('llm_templates')['system_prompt'], template['system_prompt'])
        previous = settings.to_dict()
        settings.load_from(previous)
        self.assertEqual(settings.to_dict(), previous)

    def test_unchanged_legacy_system_template_is_upgraded(self):
        old_system = ('你是一位演讲稿润色专家，擅长在尽量保留作者原意与措辞的前提下，'
                      '把讲稿调整为适合口头讲述与语音合成的形式。你只输出改写后的讲稿正文，不做任何解释。')
        settings = AppSettings()
        settings.load_from({'llm_templates': {'system_prompt': old_system}})
        self.assertEqual(settings.get('llm_templates')['system_prompt'], rewrite.DEFAULT_SYSTEM_PROMPT)

    @staticmethod
    def previous_reading_instruction():
        return (
            '这份讲稿的内容与措辞已经确认。默认逐字保留，只修正语音合成可能误读的局部表达。\n'
            '1. 不润色、扩写、概括、翻译或调整句序。不添加开场白、过渡、括号解释或总结。\n'
            '2. 数字与单位只有在需要明确读法时才转换，例如“3.3 V”→“三点三伏”、'
            '“25 ms”→“二十五毫秒”、“2.5%”→“百分之二点五”。'
            '保持数值、正负号、精度、范围和单位含义。注意单位大小写，不擅自换算。\n'
            '3. 年份、编号、型号、版本号和数学表达式根据上下文确定读法，不统一逐位读或统一改为数量。\n'
            '4. 缩写和专业术语仅在读音明确时改写，可以使用明确的同音词辅助发音，'
            '但不得自行猜测读音、展开为全称，或把所有缩写都拆成字母。无法确定时保留原文。\n'
            '5. 仅在读法明确时，将歧义符号或多音表达替换为等义、易读的文字。'
            '其余内容、换行和分隔符原样保留。没有需要纠正的内容时逐字返回原文。'
        )

    def test_previous_reading_defaults_upgrade_and_remain_stable_on_reload(self):
        previous = self.previous_reading_instruction()
        older = previous.replace(
            '4. 缩写和专业术语仅在读音明确时改写，可以使用明确的同音词辅助发音，',
            '4. 缩写和专业术语优先遵循指定读法。可以使用明确的同音词辅助发音，')
        custom = {'name': '我的汇报场景', 'instruction': '保留我自己的要求。'}
        for instruction in (previous, older):
            with self.subTest(instruction=instruction):
                settings = AppSettings()
                styles = rewrite.default_styles()
                styles[0]['instruction'] = instruction
                styles.append(custom)
                settings.load_from({'llm_styles': styles, 'llm_style': rewrite.DEFAULT_STYLE_NAME})
                self.assertEqual(rewrite.find_instruction(settings.get('llm_styles'), rewrite.DEFAULT_STYLE_NAME),
                                 rewrite.BUILTIN_STYLES[0]['instruction'])
                self.assertIn(custom, settings.get('llm_styles'))
                self.assertEqual(settings.get('llm_style'), rewrite.DEFAULT_STYLE_NAME)
                updated = settings.to_dict()
                settings.load_from(updated)
                self.assertEqual(settings.to_dict(), updated)

    def test_edited_reading_instruction_is_not_overwritten(self):
        custom = {'name': rewrite.DEFAULT_STYLE_NAME,
                  'instruction': self.previous_reading_instruction() + '\n保留我自己添加的领域要求。'}
        settings = AppSettings()
        settings.load_from({'llm_styles': [custom], 'llm_style': custom['name']})
        self.assertEqual(settings.get('llm_styles'), [custom])
        self.assertEqual(settings.get('llm_style'), custom['name'])


class OptimizationInterfaceTests(QtTestCase):
    def setUp(self):
        self.notes = {1: '🙂PHY 电压为 3.3 V。●延迟为 25 ms。', 2: '结论保持不变。', 3: ''}
        self.settings = AppSettings()
        self.dialog = RewriteDialog(self.notes, '●', self.settings)
        self.addCleanup(self.dispose)
        for name in ('_info_success', '_info_warning', '_info_error'):
            patcher = patch.object(self.dialog, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def dispose(self):
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def test_change_highlights_use_utf16_offsets_and_clear_for_unchanged_page(self):
        self.dialog.on_page_done(1, '🙂物理层接口 电压为 3.3 V。●延迟为 25 ms。', '')
        self.assertTrue(self.dialog.apply_button.isEnabled())
        selected = [selection.cursor.selectedText() for selection in self.dialog.original_edit.extraSelections()]
        self.assertIn('PHY', selected)
        self.dialog.review_list.setCurrentRow(1)
        self.dialog.on_page_done(2, self.notes[2], '')
        self.assertFalse(self.dialog.original_edit.extraSelections())
        self.assertEqual(self.dialog._item_for_page(2).text(), self.dialog._label_for(2, STATUS_UNCHANGED))
        self.assertIsNone(self.dialog._item_for_page(2).data(Qt.ItemDataRole.CheckStateRole))
        self.assertEqual(self.dialog.notes, self.notes)

    def test_unchanged_results_do_not_enable_apply_and_empty_edit_can_be_corrected(self):
        self.dialog.on_page_done(1, self.notes[1], '')
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.dialog.rewritten_edit.setPlainText('')
        self.dialog.review_list.setCurrentRow(1)
        self.dialog.review_list.setCurrentRow(0)
        self.assertFalse(self.dialog.rewritten_edit.isReadOnly())
        self.dialog.rewritten_edit.setPlainText('用户校对的讲稿●保留第二段')
        self.assertTrue(self.dialog.apply_button.isEnabled())

    def test_external_import_enters_review_and_only_applies_checked_changes(self):
        child = RewriteExchangeDialog(
            self.dialog, validator=lambda text: exchange.parse_result(text, self.notes, '●'))
        payload = result_payload({2: self.notes[2]})
        payload['pages'][0]['segments'] = ['新的第二页讲稿']
        child.editor.setPlainText(json.dumps(payload))
        QTimer.singleShot(0, child.primary_button.click)
        with patch('ui.dialogs.rewrite_dialog.RewriteExchangeDialog', return_value=child):
            self.dialog.import_result()
        self.assertEqual(self.dialog.current_page(), 2)
        self.assertEqual(self.dialog.get_applied(), {})
        self.assertEqual(self.dialog.notes, self.notes)
        self.dialog.apply_button.click()
        self.assertEqual(self.dialog.get_applied(), {2: '新的第二页讲稿'})

    def test_invalid_external_result_stays_in_import_dialog(self):
        child = RewriteExchangeDialog(
            self.dialog, validator=lambda text: exchange.parse_result(text, self.notes, '●'))
        child.editor.setPlainText('{"pages": []}')
        child.primary_button.click()
        self.assertEqual(child.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(child.message.text())
        self.assertFalse(child.imported)
        self.assertFalse(self.dialog.results)

    def test_structure_change_requires_confirmation(self):
        self.dialog.on_page_done(1, '合并后的讲稿', rewrite.check_segments(self.notes[1], '合并后的讲稿', '●'))
        self.assertEqual(self.dialog._item_for_page(1).text(), self.dialog._label_for(1, STATUS_WARN))
        with patch('ui.dialogs.rewrite_dialog.MessageBox') as confirm:
            confirm.return_value.exec.return_value = False
            self.dialog.apply_selected()
        self.assertFalse(self.dialog.applied)

    def test_api_path_shares_scene_with_external_path(self):
        self.settings.set('llm_model', 'test-model')
        self.dialog.style_combo.setCurrentText('教学讲解')
        instruction = self.dialog.current_instruction()
        with patch('ui.dialogs.rewrite_dialog.RewriteExchangeDialog') as exporter:
            self.dialog.export_task()
        self.assertIn(instruction, exporter.call_args.kwargs['task_text'])
        with patch('tasks.rewrite_task.RewriteTask.start'):
            self.dialog.start_rewrite()
        worker = self.dialog.task
        self.assertFalse(self.dialog.export_task_button.isEnabled())
        self.assertFalse(self.dialog.import_result_button.isEnabled())
        self.assertEqual(instruction, worker.instruction)
        with patch('tasks.rewrite_task.llm_client.chat', side_effect=[self.notes[1], self.notes[2]]) as call:
            worker.run()
        self.assertIn(instruction, call.call_args_list[0].kwargs['messages'][1]['content'])
        self.assertEqual(self.dialog.results[1]['text'], self.notes[1])
        self.assertFalse(self.dialog.apply_button.isEnabled())
        self.assertIsNone(self.dialog.task)

    def test_file_export_and_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            task = Path(folder) / '优化任务.txt'
            child = RewriteExchangeDialog(self.dialog, task_text='包含提示词的任务')
            with patch('ui.dialogs.rewrite_exchange_dialog.QFileDialog.getSaveFileName', return_value=(str(task), '')):
                child.file_button.click()
            self.assertEqual(task.read_text(encoding='utf-8'), '包含提示词的任务')
            result = Path(folder) / '结果.json'
            result.write_text(json.dumps(result_payload({2: self.notes[2]})), encoding='utf-8-sig')
            child = RewriteExchangeDialog(self.dialog, validator=lambda text: exchange.parse_result(text, self.notes, '●'))
            with patch('ui.dialogs.rewrite_exchange_dialog.QFileDialog.getOpenFileName', return_value=(str(result), '')):
                child.file_button.click()
            child.primary_button.click()
            self.assertEqual(child.imported, {2: self.notes[2]})

    def test_processing_scope_and_application_scope_are_independent(self):
        dialog = self.dialog
        dialog.page_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        dialog.on_page_done(1, '第一段新讲稿●第二段新讲稿', '')
        dialog.on_page_done(2, '第二页新讲稿', '')
        dialog._item_for_page(2).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.checked_pages(), [1])
        self.assertEqual(dialog.checked_result_pages(), [1])
        dialog.show_setup()
        dialog.page_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        dialog.page_list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.checked_pages(), [2])
        self.assertEqual(dialog.checked_result_pages(), [1])
        dialog.show_review()
        dialog.apply_selected()
        self.assertEqual(dialog.get_applied(), {1: '第一段新讲稿●第二段新讲稿'})
        self.assertEqual(dialog.notes, self.notes)

    def test_stages_and_modes_only_expose_the_relevant_actions(self):
        dialog = self.dialog
        dialog.show()
        self.qt.processEvents()
        self.assertTrue(dialog.start_button.isVisible())
        self.assertFalse(dialog.export_task_button.isVisible())
        self.assertFalse(dialog.review_step.isEnabled())
        dialog.mode_tabs.widget('external').click()
        self.assertFalse(dialog.start_button.isVisible())
        self.assertTrue(dialog.export_task_button.isVisible())
        self.assertTrue(dialog.import_result_button.isVisible())
        self.assertFalse(dialog.config_button.isVisible())
        dialog.on_page_done(2, '第二页新讲稿', '')
        self.assertEqual(dialog.phase, 'review')
        self.assertTrue(dialog.apply_button.isVisible())
        self.assertTrue(dialog.setup_step.isEnabled())
        self.assertFalse(dialog.export_task_button.isVisible())
        self.assertFalse(dialog.import_result_button.isVisible())
        dialog.stage_tabs.widget('setup').click()
        self.assertEqual(dialog.processing_mode, 'external')
        self.assertEqual(dialog.results[2]['text'], '第二页新讲稿')

    def test_page_summary_rows_support_mouse_and_keyboard_selection(self):
        dialog = self.dialog
        dialog.show()
        self.qt.processEvents()
        listing = dialog.page_list
        row = listing.visualItemRect(listing.item(1))
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=row.topLeft() +
                         QPoint(24, row.height() // 2))
        self.assertEqual(dialog.checked_pages(), [1])
        listing.setCurrentRow(0)
        QTest.keyClick(listing, Qt.Key.Key_Space)
        self.assertEqual(dialog.checked_pages(), [])
        listing.setCurrentRow(2)
        QTest.keyClick(listing, Qt.Key.Key_Space)
        self.assertEqual(dialog.checked_pages(), [])
        self.assertEqual(dialog.source_preview.toPlainText(), '')

        dialog.on_page_done(1, '新第一段●新第二段', '')
        self.qt.processEvents()
        row = dialog.review_list.visualItemRect(dialog._item_for_page(1))
        QTest.mouseClick(dialog.review_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=row.topLeft() + QPoint(24, row.height() // 2))
        self.assertEqual(dialog.checked_result_pages(), [])
        self.assertEqual(dialog.checked_pages(), [])
        self.assertFalse(dialog.apply_button.isEnabled())

    def test_scope_checkbox_tracks_partial_selection_and_excludes_unavailable_pages(self):
        dialog = self.dialog
        dialog.show()
        self.qt.processEvents()
        selector = dialog.select_pages_checkbox
        self.assertEqual(selector.checkState(), Qt.CheckState.Checked)
        self.assertEqual(dialog.page_selection_summary.text(), '已选 2 / 2 页')
        selector.click()
        self.assertEqual(dialog.checked_pages(), [])
        selector.click()
        self.assertEqual(dialog.checked_pages(), [1, 2])
        dialog.page_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(selector.checkState(), Qt.CheckState.PartiallyChecked)
        QTest.keyClick(selector, Qt.Key.Key_Space)
        self.assertEqual(dialog.checked_pages(), [1, 2])
        self.assertEqual(selector.checkState(), Qt.CheckState.Checked)

        dialog.on_page_done(1, '新第一段●新第二段', '')
        dialog.on_page_done(2, self.notes[2], '')
        self.assertEqual(dialog.result_selection_summary.text(), '已选 1 / 1 页')
        dialog.select_results_checkbox.click()
        self.assertEqual(dialog.checked_result_pages(), [])
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.checked_pages(), [1, 2])
        dialog.select_results_checkbox.click()
        self.assertEqual(dialog.checked_result_pages(), [1])
        dialog.rewritten_edit.setPlainText(self.notes[1])
        self.assertFalse(dialog.select_results_checkbox.isEnabled())
        self.assertEqual(dialog.result_selection_summary.text(), '已选 0 / 0 页')
        self.assertEqual(dialog.notes, self.notes)

    def test_stop_preserves_completed_results_and_unlocks_setup(self):
        dialog = self.dialog
        self.settings.set('llm_model', 'test-model')
        with patch('tasks.rewrite_task.RewriteTask.start'):
            dialog.start_rewrite()
        worker = dialog.task
        self.assertEqual(dialog.phase, 'review')
        self.assertFalse(dialog.cancel_task_button.isHidden())
        dialog.show_setup()
        dialog.set_processing_mode('external')
        self.assertEqual(dialog.phase, 'review')
        self.assertEqual(dialog.processing_mode, 'api')
        dialog.on_page_done(1, '已完成的第一段●已完成的第二段', '')
        dialog.cancel_rewrite()
        with patch('tasks.rewrite_task.llm_client.chat') as chat:
            worker.run()
        chat.assert_not_called()
        self.assertIsNone(dialog.task)
        self.assertTrue(dialog.cancel_task_button.isHidden())
        self.assertEqual(dialog.checked_result_pages(), [1])
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn('未处理', dialog._item_for_page(2).text())
        dialog._info_success.assert_called_with('已停止处理', '已保留 1 页结果，可继续审查或返回设置。')
        dialog.show_setup()
        self.assertEqual(dialog.phase, 'setup')
        self.assertEqual(dialog.checked_pages(), [1, 2])

    def test_reprocessing_one_page_retains_other_candidate_and_adoption_choice(self):
        dialog = self.dialog
        dialog.on_page_done(1, '原有候选第一段●原有候选第二段', '')
        dialog.on_page_done(2, '原有第二页候选', '')
        dialog._item_for_page(1).setCheckState(Qt.CheckState.Unchecked)
        dialog.show_setup()
        dialog.page_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.settings.set('llm_model', 'test-model')
        with patch('tasks.rewrite_task.RewriteTask.start'):
            dialog.start_rewrite()
        worker = dialog.task
        with patch('tasks.rewrite_task.llm_client.chat', return_value='重新处理的第二页候选') as chat:
            worker.run()
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(dialog.results[1]['text'], '原有候选第一段●原有候选第二段')
        self.assertEqual(dialog.results[2]['text'], '重新处理的第二页候选')
        self.assertEqual(dialog.checked_result_pages(), [2])

