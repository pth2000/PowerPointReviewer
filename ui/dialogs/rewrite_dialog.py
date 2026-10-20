"""选择优化场景，逐页审查接口生成或外部导入的候选讲稿。"""

from difflib import SequenceMatcher

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QListWidgetItem,
    QTextEdit,
)
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    MessageBox,
)

from app.ai import exchange, rewrite
from tasks.rewrite_task import RewriteTask
from ui.dialogs.llm_config_dialog import LLMConfigMessageBox
from ui.dialogs.style_manager_dialog import StyleManagerDialog
from ui.dialogs.base import ThemedDialog
from ui.dialogs.rewrite_exchange_dialog import RewriteExchangeDialog
from ui.dialogs.rewrite_workspace import RewriteWorkspace
from ui.dialogs.rewrite_widgets import PREVIEW_ROLE, STATUS_ROLE

STATUS_PENDING = '待优化'
STATUS_DONE = '已优化'
STATUS_UNCHANGED = '未改动'
STATUS_WARN = '需确认'
STATUS_FAILED = '失败'
STATUS_BLANK = '空白页'


class RewriteDialog(ThemedDialog, RewriteWorkspace):
    """逐页展示原文和候选结果，并由用户明确选择处理与写回范围。

    模型输出不会自动覆盖讲稿；分隔符结构变化的页面会额外警告。
    """

    def __init__(self, notes: dict, mark: str, app_settings, parent=None):
        super().__init__(parent)
        self.notes = dict(notes)
        self.mark = mark
        self.app_settings = app_settings
        self.styles: list[dict] = []
        self._syncing = False
        self.results: dict[int, dict] = {}
        self.applied: dict[int, str] = {}
        self.task = None
        self._stop_requested = False
        self._closing = False

        self.phase = 'setup'
        self.processing_mode = 'api'
        self._job_pages = set()
        self.setup_workspace()
        self.page_list.currentRowChanged.connect(self.preview_source)
        self.page_list.itemChanged.connect(self.on_item_changed)
        self.review_list.currentRowChanged.connect(self.show_page)
        self.review_list.itemChanged.connect(self.on_item_changed)
        self.rewritten_edit.textChanged.connect(self.on_rewritten_edited)
        self.style_combo.currentIndexChanged.connect(self.update_scene_hint)
        self.reload_styles()
        self.update_scene_hint()
        self.populate_pages()

    # 接口配置

    def current_llm_config(self) -> dict:
        """从应用偏好组装大模型客户端配置。"""
        return {
            'base_url': str(self.app_settings.get('llm_base_url') or '').strip(),
            'api_key': str(self.app_settings.get('llm_api_key') or ''),
            'model': str(self.app_settings.get('llm_model') or '').strip(),
            'timeout': int(self.app_settings.get('llm_timeout') or 120),
        }

    def open_config_dialog(self):
        """编辑接口参数，并在确认后写回应用偏好。"""
        box = LLMConfigMessageBox(self.app_settings, self)
        if box.exec():
            box.apply_to_settings()
        self.refresh_connection_hint()

    def refresh_connection_hint(self):
        config = self.current_llm_config()
        self.connection_hint.setText(
            f'当前模型：{config["model"]}' if config['base_url'] and config['model']
            else '先配置服务地址与模型，再开始优化。')

    def show_setup(self):
        if self.task is not None:
            return
        self.phase = 'setup'
        self.stage_stack.setCurrentIndex(0)
        self.stage_tabs.setCurrentItem('setup')
        self.update_action_state()

    def show_review(self):
        if not self.results and not self._job_pages:
            return
        self.phase = 'review'
        self.stage_stack.setCurrentIndex(1)
        self.stage_tabs.setCurrentItem('review')
        self.show_page(self.review_list.currentRow())
        self.update_action_state()

    def set_processing_mode(self, mode):
        if self.task is not None or mode not in ('api', 'external'):
            return
        self.processing_mode = mode
        self.mode_tabs.setCurrentItem(mode)
        self.mode_stack.setCurrentIndex(int(mode == 'external'))
        self.update_action_state()

    def preview_source(self, *_):
        item = self.page_list.currentItem()
        page = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.source_label.setText(f'讲稿预览 · 第 {page} 页' if page is not None else '讲稿预览')
        text = str(self.notes.get(page, ''))
        self.source_preview.setPlainText(text)
        segments = len(text.split(self.mark)) if self.mark else 1
        self.source_meta.setText(f'{segments} 段讲稿' if text.strip() else '本页没有讲稿')

    def reload_styles(self):
        """重建风格下拉，并尽量恢复原有选择。"""
        self.styles = rewrite.normalize_styles(self.app_settings.get('llm_styles'))
        wanted = self.style_combo.currentText() or str(self.app_settings.get('llm_style') or '')

        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItems(rewrite.style_names(self.styles))
        index = self.style_combo.findText(wanted)
        if index < 0:
            index = self.style_combo.findText(str(self.app_settings.get('llm_style') or ''))
        self.style_combo.setCurrentIndex(index if index >= 0 else 0)
        self.style_combo.blockSignals(False)

    def open_style_manager(self):
        """打开风格管理器，并在确认后刷新选择列表。"""
        dialog = StyleManagerDialog(self.app_settings, self.mark, self,
                                    selected_style=self.style_combo.currentText())
        if dialog.exec():
            self.reload_styles()
            self.update_scene_hint()

    def update_scene_hint(self, *_):
        hints = {
            '朗读纠错': '保留正文，只纠正可能误读的缩写、数字、单位和符号。',
            '汇报讲解': '理清汇报表达，保留观点、数据与限定条件。',
            '教学讲解': '梳理讲解步骤，保持术语与知识内容准确。',
            '精简讲解': '删去重复与铺垫，保留支撑结论的关键信息。',
        }
        self.scene_hint.setText(hints.get(self.style_combo.currentText(), '按自定义要求处理所选页面。'))

    def current_instruction(self):
        return rewrite.find_instruction(self.styles, self.style_combo.currentText())

    def export_task(self):
        if self.task is not None:
            return
        try:
            text = exchange.build_task(
                self.pending_pages(), mark=self.mark, instruction=self.current_instruction(),
                templates=self.app_settings.get('llm_templates'))
        except ValueError as exc:
            self._info_warning('无法导出任务', str(exc))
            return
        self.app_settings.set('llm_style', self.style_combo.currentText())
        RewriteExchangeDialog(self, task_text=text).exec()

    def import_result(self):
        if self.task is not None:
            return
        dialog = RewriteExchangeDialog(
            self, validator=lambda text: exchange.parse_result(text, self.notes, self.mark))
        if not dialog.exec():
            return
        # 全部校验通过后再合并。未返回的页面和已有候选结果保持原样。
        self._syncing = True
        for page, text in dialog.imported.items():
            self.on_page_done(page, text, rewrite.check_segments(self.notes[page], text, self.mark))
        self._syncing = False
        self.review_list.setCurrentItem(self._item_for_page(min(dialog.imported)))
        self.show_review()
        self.update_action_state()
        self._info_success('结果已导入', f'已导入 {len(dialog.imported)} 页，请对照原文后选择应用。')

    # 列表与展示

    def populate_pages(self):
        """按页码填充列表，并将空白页设为不可勾选。"""
        self._syncing = True
        self.page_list.clear()
        self.review_list.clear()
        for page in sorted(self.notes):
            text = str(self.notes[page]).strip()
            item = QListWidgetItem(f'第 {page} 页' if text else f'第 {page} 页 · {STATUS_BLANK}')
            item.setData(Qt.ItemDataRole.UserRole, page)
            item.setData(PREVIEW_ROLE, self.page_excerpt(self.notes[page]))
            item.setData(STATUS_ROLE, '' if text else STATUS_BLANK)
            if text:
                # 整篇处理是常见路径，非空白页默认全部勾选。
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.page_list.addItem(item)
            result_item = QListWidgetItem(self._label_for(page, '未处理' if text else STATUS_BLANK))
            result_item.setData(Qt.ItemDataRole.UserRole, page)
            result_item.setData(PREVIEW_ROLE, self.page_excerpt(self.notes[page]))
            result_item.setData(STATUS_ROLE, '未处理' if text else STATUS_BLANK)
            result_item.setFlags(result_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.review_list.addItem(result_item)
        self._syncing = False

        if self.page_list.count():
            self.page_list.setCurrentRow(0)
            self.review_list.setCurrentRow(0)
        self.refresh_connection_hint()
        self.update_action_state()

    def update_action_state(self):
        """根据选择范围和任务状态更新操作按钮。"""
        running = self.task is not None
        checked = self.checked_pages()
        applicable = [p for p in self.checked_result_pages() if self.results.get(p, {}).get('text')
                      and self.results[p]['text'] != self.notes.get(p)]
        reviewing = self.phase == 'review'
        external = self.processing_mode == 'external'

        # 选择工具不依赖任务状态，用户可提前调整下一批范围。
        self.start_button.setEnabled(not running and bool(checked) and not reviewing)
        self.start_button.setVisible(not reviewing and not external)
        self.apply_button.setEnabled(not running and bool(applicable))
        self.apply_button.setVisible(reviewing)
        self.apply_button.setText('应用所选修改')
        self.cancel_task_button.setEnabled(running and not self._stop_requested and not self._closing)
        self.cancel_task_button.setText('正在停止…' if self._stop_requested or self._closing else '停止处理')
        self.cancel_task_button.setVisible(running)
        self.export_task_button.setEnabled(not running and bool(checked) and not reviewing)
        self.export_task_button.setVisible(not reviewing and external)
        self.import_result_button.setEnabled(not running)
        self.import_result_button.setVisible(not reviewing and external)
        self.config_button.setVisible(not external)
        self.setup_step.setEnabled(not running)
        self.review_step.setEnabled(reviewing or bool(self.results or self._job_pages))
        self.mode_tabs.setEnabled(not running)
        for control in (self.style_combo, self.style_button, self.config_button):
            control.setEnabled(not running)
        self.refresh_selection_summary(self.page_list, self.select_pages_checkbox, self.page_selection_summary)
        self.refresh_selection_summary(self.review_list, self.select_results_checkbox, self.result_selection_summary)
        completed = len(self.results)
        changed = sum(bool(result.get('text') and result['text'] != self.notes[page])
                      for page, result in self.results.items())
        self.review_summary.setText(f'已处理 {self.progress.value()} / {self.progress.maximum()} 页' if running
                                    else f'{completed} 页结果 · {changed} 页有修改')

    @staticmethod
    def refresh_selection_summary(listing, checkbox, label):
        items = [listing.item(row) for row in range(listing.count())
                 if listing.item(row).flags() & Qt.ItemFlag.ItemIsUserCheckable]
        selected = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        state = Qt.CheckState.Unchecked if not selected else (
            Qt.CheckState.Checked if selected == len(items) else Qt.CheckState.PartiallyChecked)
        previous = checkbox.blockSignals(True)
        checkbox.setCheckState(state)
        checkbox.blockSignals(previous)
        checkbox.setEnabled(bool(items))
        label.setText(f'已选 {selected} / {len(items)} 页')

    def on_item_changed(self, _item):
        """在页面勾选状态变化后刷新操作状态。"""
        if not self._syncing:
            self.update_action_state()

    def checked_pages(self):
        """返回任务设置中勾选的处理页码。"""
        pages = []
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                pages.append(item.data(Qt.ItemDataRole.UserRole))
        return pages

    def checked_result_pages(self):
        return [item.data(Qt.ItemDataRole.UserRole) for row in range(self.review_list.count())
                if (item := self.review_list.item(row)).checkState() == Qt.CheckState.Checked]

    @staticmethod
    def _label_for(page: int, status: str) -> str:
        return f'第 {page} 页 · {status}'

    def page_excerpt(self, text):
        text = str(text).replace(self.mark, ' ') if self.mark else str(text)
        return ' '.join(text.split())

    def _set_item_status(self, page: int, status: str):
        """更新结果状态，仅允许勾选存在修改的有效候选。"""
        item = self._item_for_page(page)
        if item is None:
            return
        syncing = self._syncing
        self._syncing = True
        was_checkable = bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        was_checked = item.checkState() == Qt.CheckState.Checked
        item.setText(self._label_for(page, status))
        result = self.results.get(page, {})
        item.setData(STATUS_ROLE, status)
        item.setData(PREVIEW_ROLE, self.page_excerpt(result.get('text') or self.notes.get(page, '')))
        changed = bool(result.get('text') and not result.get('error') and result['text'] != self.notes.get(page))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable if changed
                      else item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        if changed:
            item.setCheckState(Qt.CheckState.Checked if was_checked or not was_checkable
                               else Qt.CheckState.Unchecked)
        else:
            item.setData(Qt.ItemDataRole.CheckStateRole, None)
        self._syncing = syncing

    def _item_for_page(self, page: int):
        for row in range(self.review_list.count()):
            item = self.review_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == page:
                return item
        return None

    def current_page(self):
        item = self.review_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_page(self, _row: int):
        """切换高亮页时刷新原文、候选结果和警告。"""
        page = self.current_page()
        if page is None:
            return
        self.review_title.setText(f'第 {page} 页')

        self.original_edit.setPlainText(str(self.notes.get(page, '')))

        result = self.results.get(page, {})
        self.rewritten_edit.blockSignals(True)
        self.rewritten_edit.setPlainText(str(result.get('error') or result.get('text', '')))
        self.rewritten_edit.blockSignals(False)
        self.rewritten_edit.setReadOnly(page not in self.results or bool(result.get('error')))

        message = result.get('error') or result.get('warning') or ''
        if result.get('error'):
            message = message.split('\n\n', 1)[0]
        self.warning_label.setText(f'提示：{message}' if message else '')
        self.warning_label.setVisible(bool(message))
        self.warning_panel.setVisible(bool(message))
        self.rewritten_edit.setPlaceholderText('正在等待这一页的优化结果…' if self.task is not None
                                             else '本页尚无结果，可返回设置重新处理。')
        self.highlight_changes()

    def highlight_changes(self):
        page = self.current_page()
        result = self.results.get(page, {})
        before, after = self.original_edit.toPlainText(), self.rewritten_edit.toPlainText()
        selections = [[], []]
        count = 0
        if result.get('text') and not result.get('error'):
            for operation, a, b, c, d in SequenceMatcher(None, before, after, autojunk=len(before) > 8000).get_opcodes():
                if operation == 'equal':
                    continue
                count += 1
                for index, (editor, text, start, end, color) in enumerate((
                        (self.original_edit, before, a, b, QColor(220, 150, 40, 55)),
                        (self.rewritten_edit, after, c, d, QColor(60, 170, 105, 55)))):
                    if start == end:
                        continue
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = editor.textCursor()
                    selection.cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
                    selection.cursor.setPosition(len(text[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
                    selection.format.setBackground(color)
                    selections[index].append(selection)
        self.original_edit.setExtraSelections(selections[0])
        self.rewritten_edit.setExtraSelections(selections[1])
        self.rewritten_label.setText(f'优化结果 · {count} 处调整' if count else '优化结果')

    def on_rewritten_edited(self):
        """保存用户手工修改，并重新校验分隔符结构。"""
        page = self.current_page()
        if page is None or page not in self.results:
            return

        text = self.rewritten_edit.toPlainText()
        self.results[page]['text'] = text
        warning = rewrite.check_segments(str(self.notes.get(page, '')), text, self.mark)
        self.results[page]['warning'] = warning

        if not self.results[page].get('error'):
            self._set_item_status(page, self.result_status(page))
        self.warning_label.setText(f'提示：{warning}' if warning else '')
        self.warning_label.setVisible(bool(warning))
        self.warning_panel.setVisible(bool(warning))
        self.highlight_changes()
        self.update_action_state()

    def result_status(self, page):
        result = self.results[page]
        if result.get('warning'):
            return STATUS_WARN
        return STATUS_UNCHANGED if result['text'] == self.notes.get(page) else STATUS_DONE

    # 改写流程

    def pending_pages(self):
        """收集勾选且非空白的待处理页面。"""
        wanted = set(self.checked_pages())
        return [(page, str(self.notes[page])) for page in sorted(self.notes)
                if page in wanted and str(self.notes[page]).strip()]

    def start_rewrite(self):
        """启动当前勾选页的改写，并保留未勾选页的已有结果。"""
        if self.task is not None:
            return
        pages = self.pending_pages()
        if not pages:
            self._info_warning('没有可优化的页', '在左侧勾选要优化的页')
            return

        llm_config = self.current_llm_config()
        if not llm_config['base_url'] or not llm_config['model']:
            self._info_warning('尚未配置接口', '在"接口配置"中填写服务地址与模型名称')
            return
        instruction = self.current_instruction()

        self.app_settings.set('llm_style', self.style_combo.currentText())

        for page, _text in pages:
            self.results.pop(page, None)
            self._set_item_status(page, STATUS_PENDING)
        self.show_page(self.review_list.currentRow())

        self.progress.setRange(0, len(pages))
        self.progress.setValue(0)
        self.progress.setVisible(True)

        self.task = RewriteTask(self)
        self._stop_requested = False
        self._job_pages = {page for page, _ in pages}
        self.review_list.setCurrentItem(self._item_for_page(pages[0][0]))
        self.show_review()
        self.update_action_state()
        self.task.configure(
            pages, llm_config,
            instruction=instruction,
            templates=rewrite.normalize_templates(self.app_settings.get('llm_templates')),
            mark=self.mark,
        )
        self.task.signal_page_done.connect(self.on_page_done)
        self.task.signal_page_failed.connect(self.on_page_failed)
        self.task.signal_progress.connect(self.on_progress)
        self.task.signal_finish.connect(self.on_finish)
        self.task.finished.connect(self._worker_finished)
        self.task.finished.connect(self.task.deleteLater)
        self.task.start()

    def cancel_rewrite(self):
        """请求协作式停止；当前网络请求结束后退出。"""
        if self.task is not None:
            self._stop_requested = True
            self.task.cancel()
        self.cancel_task_button.setEnabled(False)
        self.cancel_task_button.setText('正在停止…')

    def on_page_done(self, page: int, text: str, warning: str):
        """保存单页候选结果，并更新其结构状态。"""
        self.results[page] = {'text': text, 'warning': warning, 'error': ''}
        self._set_item_status(page, self.result_status(page))
        if self.phase == 'setup' and self.task is None:
            self.show_review()

        if self.current_page() == page:
            self.show_page(self.review_list.currentRow())
        self.update_action_state()

    def on_page_failed(self, page: int, message: str):
        """记录单页错误，其余页面继续处理。"""
        self.results[page] = {'text': '', 'warning': '', 'error': message}
        self._set_item_status(page, STATUS_FAILED)
        if self.phase == 'setup' and self.task is None:
            self.show_review()

        if self.current_page() == page:
            self.show_page(self.review_list.currentRow())
        self.update_action_state()

    def on_progress(self, done: int, total: int):
        self.progress.setValue(done)
        self.review_summary.setText(f'已处理 {done} / {total} 页')

    def on_finish(self):
        """清理任务引用并恢复界面操作状态。"""
        # 线程随后由 deleteLater 销毁；先断开引用，避免关闭弹窗时访问失效的 C++ 对象。
        self.task = None
        pending, self._job_pages = self._job_pages, set()
        for page in pending:
            if page not in self.results:
                self._set_item_status(page, '未处理')
        self.progress.setVisible(False)
        self.show_page(self.review_list.currentRow())
        self.update_action_state()

        if self._closing:
            return
        succeeded = sum(bool(self.results.get(page, {}).get('text')) for page in pending)
        failed = sum(bool(self.results.get(page, {}).get('error')) for page in pending)

        if self._stop_requested:
            self._info_success('已停止处理', f'已保留 {succeeded} 页结果，可继续审查或返回设置。')
        elif failed and succeeded:
            self._info_warning('部分完成', f'成功 {succeeded} 页，失败 {failed} 页')
        elif failed:
            self._info_error('优化失败', '所有页面均未成功，请检查服务地址、API Key 与模型名称')
        else:
            self._info_success('优化完成', f'已完成 {succeeded} 页，可查看结果并选择应用')

    # 采用

    def set_all_checked(self, checked: bool):
        """统一设置所有非空白页的勾选状态。"""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._syncing = True
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)
        self._syncing = False
        self.update_action_state()

    def set_all_results_checked(self, checked: bool):
        self._syncing = True
        for row in range(self.review_list.count()):
            item = self.review_list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._syncing = False
        self.update_action_state()

    def apply_selected(self):
        """校验并收集勾选页结果，确认后关闭弹窗。"""
        if self.task is not None:
            return
        applied = {}
        risky = []
        for page in self.checked_result_pages():
            result = self.results.get(page, {})
            if result.get('text') and result['text'] != self.notes.get(page):
                applied[page] = result['text']
                if result.get('warning'):
                    risky.append(page)

        if not applied:
            self._info_warning('没有可应用的修改', '请选择内容有变化的页面')
            return

        if risky:
            pages_text = '、'.join(f'第 {p} 页' for p in risky[:8])
            box = MessageBox(
                '讲稿分段有变化',
                f'{pages_text} 的分隔符数量与原文不同，应用后 PPT 动画的点击节奏会改变',
                self)
            box.yesButton.setText('仍要应用')
            box.cancelButton.setText('返回检查')
            if not box.exec():
                return

        self.applied = applied
        self.accept()

    def get_applied(self) -> dict:
        """返回用户最终确认写回的页码到正文映射。"""
        return dict(self.applied)

    def reject(self):
        """拒绝关闭前请求任务停止，避免后台信号访问已销毁控件。"""
        if self.task is not None and self.task.isRunning():
            if self._closing:
                return
            self._closing = True
            self.cancel_rewrite()
            self.cancel_task_button.show()
            return
        super().reject()

    def _worker_finished(self):
        if self._closing:
            super().reject()

    def closeEvent(self, event):
        if self.task is not None and self.task.isRunning():
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    # 提示

    def _info_success(self, title, text):
        InfoBar.success(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=4000, parent=self)

    def _info_warning(self, title, text):
        InfoBar.warning(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=5000, parent=self)

    def _info_error(self, title, text):
        InfoBar.error(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                      position=InfoBarPosition.TOP, duration=-1, parent=self)
