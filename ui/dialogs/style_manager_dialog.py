"""编辑 AI 改写风格、提示词模板和请求预览。"""

import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QListWidgetItem
from qfluentwidgets import InfoBar, InfoBarPosition, MessageBox

from app.ai import rewrite
from ui.dialogs.base import ThemedDialog
from ui.dialogs.style_manager_workspace import StyleManagerWorkspace


class StyleManagerDialog(ThemedDialog, StyleManagerWorkspace):
    """管理改写风格和提示词模板，并实时预览最终请求。

    编辑先作用于本地副本，只有确认后才写回配置；用户模板必须保留 ``{text}``，
    否则讲稿正文无法进入模型请求。
    """

    # 示例包含页内分隔符，确保预览覆盖条件模板的完整渲染路径。
    SAMPLE_SEGMENT = '（此处为当前页的讲稿原文）'

    def __init__(self, app_settings, mark: str, parent=None, *, selected_style=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.mark = mark
        self.styles = rewrite.normalize_styles(copy.deepcopy(app_settings.get('llm_styles')))
        self.templates = rewrite.normalize_templates(app_settings.get('llm_templates'))
        self._current_index = -1
        self._syncing = False
        self._selected_style_name = str(selected_style if selected_style is not None else app_settings.get('llm_style') or '')

        self.setup_workspace()
        self.load_templates_into_editors()
        names = rewrite.style_names(self.styles)
        wanted = self._selected_style_name
        self.reload_list(select=names.index(wanted) if wanted in names else 0)

    def load_templates_into_editors(self):
        """将当前模板副本恢复到对应编辑器。"""
        self._syncing = True
        self.system_edit.setPlainText(self.templates['system_prompt'])
        self.user_edit.setPlainText(self.templates['user_template'])
        self.segment_edit.setPlainText(self.templates['segment_rule'])
        self._syncing = False

    def current_templates(self) -> dict:
        """读取编辑器并组装模板映射。"""
        return {
            'system_prompt': self.system_edit.toPlainText(),
            'user_template': self.user_edit.toPlainText(),
            'segment_rule': self.segment_edit.toPlainText(),
        }

    def on_template_edited(self):
        """模板变化后刷新最终请求预览。"""
        if self._syncing:
            return
        self.refresh_preview()

    def reset_templates(self):
        """确认后用内置值覆盖当前模板副本。"""
        box = MessageBox('恢复默认模板', '将丢弃对提示词模板的全部改动', self)
        box.yesButton.setText('恢复')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        self.templates = rewrite.default_templates()
        self.load_templates_into_editors()
        self.refresh_preview()
        self._info('success', '已恢复', '提示词模板已重置为内置内容')

    # 列表与编辑器同步

    def reload_list(self, select: int = 0):
        """按本地风格副本重建列表，并选择指定行。"""
        self._syncing = True
        self.style_list.clear()
        for style in self.styles:
            item = QListWidgetItem(style['name'])
            item.setSizeHint(QSize(0, 44))
            self.style_list.addItem(item)
        self._syncing = False

        self._current_index = -1
        if self.styles:
            self.style_list.setCurrentRow(max(0, min(select, len(self.styles) - 1)))
        else:
            self.load_editor(-1)

    def on_row_changed(self, row: int):
        """切换行前保存上一风格的未提交编辑。"""
        if self._syncing:
            return
        self.flush_editor()
        self.load_editor(row)

    def flush_editor(self):
        """将编辑器内容同步到当前风格副本。"""
        if not (0 <= self._current_index < len(self.styles)):
            return
        name = self.name_edit.text().strip()
        if self.styles[self._current_index]['name'] == self._selected_style_name:
            self._selected_style_name = name
        self.styles[self._current_index] = {
            'name': name,
            'instruction': self.instruction_edit.toPlainText().strip(),
        }

    def load_editor(self, row: int):
        """将指定风格载入编辑器，并同步预览。"""
        self._current_index = row
        has_style = 0 <= row < len(self.styles)

        self._syncing = True
        self.name_edit.setText(self.styles[row]['name'] if has_style else '')
        self.instruction_edit.setPlainText(self.styles[row]['instruction'] if has_style else '')
        self._syncing = False

        self.name_edit.setEnabled(has_style)
        self.instruction_edit.setEnabled(has_style)
        self.delete_button.setEnabled(has_style and len(self.styles) > 1)
        self.refresh_preview()

    def on_name_edited(self, text: str):
        """将名称编辑实时同步到列表项。"""
        if self._syncing or not (0 <= self._current_index < len(self.styles)):
            return
        item = self.style_list.item(self._current_index)
        if item is not None:
            item.setText(text)

    def on_instruction_edited(self):
        """改写要求变化后更新本地副本和提示词预览。"""
        if self._syncing:
            return
        self.refresh_preview()

    def refresh_preview(self):
        """使用生产渲染函数生成与实际请求一致的预览。"""
        templates = self.current_templates()
        sample_text = f'{self.SAMPLE_SEGMENT}{self.mark}{self.SAMPLE_SEGMENT}' \
            if self.mark else self.SAMPLE_SEGMENT
        user_prompt = rewrite.build_user_prompt(
            sample_text, mark=self.mark,
            instruction=self.instruction_edit.toPlainText(),
            templates=templates)
        self.preview_edit.setPlainText(
            f'系统提示词\n{templates["system_prompt"]}\n\n用户提示词\n{user_prompt}')

    # 增删与恢复

    def create_style(self):
        """创建具有唯一名称的风格并立即选中。"""
        self.flush_editor()
        self.styles.append({
            'name': rewrite.unique_name(self.styles, '新场景'),
            'instruction': rewrite.NEW_STYLE_INSTRUCTION,
        })
        self.reload_list(select=len(self.styles) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def delete_style(self):
        """删除当前风格，但始终保留至少一个可用项。"""
        if not (0 <= self._current_index < len(self.styles)):
            return
        if len(self.styles) <= 1:
            self._info('warning', '无法删除', '至少保留一个优化场景')
            return

        self.flush_editor()
        name = self.styles[self._current_index]['name']
        box = MessageBox('删除场景', f'将删除场景“{name}”', self)
        box.yesButton.setText('删除')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        removed = self._current_index
        del self.styles[removed]
        self.reload_list(select=min(removed, len(self.styles) - 1))

    def reset_styles(self):
        """确认后以全部内置风格替换当前副本。"""
        box = MessageBox(
            '恢复默认场景',
            '将替换全部场景（含自定义），恢复为内置场景。',
            self,
        )
        box.yesButton.setText('恢复')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        self.styles = rewrite.default_styles()
        self.reload_list(select=0)
        self._info('success', '已恢复', '已恢复默认优化场景')

    # 提交

    def _focus_invalid_style(self, row, editor):
        self.show_section(0)
        self.style_list.setCurrentRow(row)
        editor.setFocus()

    def commit_and_accept(self):
        """校验名称、要求和必需占位符后写回应用偏好。"""
        self.flush_editor()

        seen = set()
        for index, style in enumerate(self.styles, start=1):
            if not style['name']:
                self._info('warning', '名称为空', f'第 {index} 个场景没有名称')
                self._focus_invalid_style(index - 1, self.name_edit)
                return
            if not style['instruction']:
                self._info('warning', '处理要求为空', f'“{style["name"]}”没有处理要求')
                self._focus_invalid_style(index - 1, self.instruction_edit)
                return
            if style['name'] in seen:
                self._info('warning', '名称重复', f'存在两个名为“{style["name"]}”的场景')
                self._focus_invalid_style(index - 1, self.name_edit)
                return
            seen.add(style['name'])

        templates = self.current_templates()
        if not templates['user_template'].strip():
            self._info('warning', '模板为空', '用户提示词模板不能为空')
            self.show_section(1)
            self.template_list.setCurrentRow(1)
            self.user_edit.setFocus()
            return
        if rewrite.REQUIRED_PLACEHOLDER not in templates['user_template']:
            self._info('warning', '缺少占位符',
                       f'用户提示词模板必须包含 {rewrite.REQUIRED_PLACEHOLDER}')
            self.show_section(1)
            self.template_list.setCurrentRow(1)
            self.user_edit.setFocus()
            return

        self.app_settings.set('llm_styles', copy.deepcopy(self.styles))
        self.app_settings.set('llm_templates', templates)
        selected = self._selected_style_name if self._selected_style_name in seen else self.styles[0]['name']
        self.app_settings.set('llm_style', selected)
        self.accept()

    def _info(self, level: str, title: str, text: str):
        factory = {'success': InfoBar.success, 'warning': InfoBar.warning, 'error': InfoBar.error}[level]
        factory(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=-1 if level == 'error' else 4000,
                parent=self)
