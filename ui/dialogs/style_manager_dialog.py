"""编辑 AI 改写风格、提示词模板和请求预览。"""

import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
)

from app import rewrite


class StyleManagerDialog(QDialog):
    """管理改写风格和提示词模板，并实时预览最终请求。

    编辑先作用于本地副本，只有确认后才写回配置；用户模板必须保留 ``{text}``，
    否则讲稿正文无法进入模型请求。
    """

    # 示例包含页内分隔符，确保预览覆盖条件模板的完整渲染路径。
    SAMPLE_SEGMENT = '（此处为当前页的讲稿原文）'

    def __init__(self, app_settings, mark: str, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.mark = mark
        self.styles = rewrite.normalize_styles(copy.deepcopy(app_settings.get('llm_styles')))
        self.templates = rewrite.normalize_templates(app_settings.get('llm_templates'))
        self._current_index = -1
        self._syncing = False

        self.setWindowTitle('改写风格与提示词')
        self.setMinimumSize(960, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.pivot = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.build_style_page())
        self.stack.addWidget(self.build_template_page())
        self.pivot.addItem(routeKey='styles', text='改写风格',
                           onClick=lambda: self.stack.setCurrentIndex(0))
        self.pivot.addItem(routeKey='templates', text='提示词模板',
                           onClick=lambda: self.stack.setCurrentIndex(1))
        self.pivot.setCurrentItem('styles')

        root.addWidget(self.pivot)
        root.addWidget(self.stack, 1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.ok_button = PrimaryPushButton('确定', self)
        self.ok_button.clicked.connect(self.commit_and_accept)
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.clicked.connect(self.reject)
        action_row.addWidget(self.ok_button)
        action_row.addWidget(self.cancel_button)
        root.addLayout(action_row)

        self.reload_list(select=0)

    # 风格分栏

    def build_style_page(self) -> QWidget:
        """创建风格列表、编辑器和最终提示词预览。"""
        page = QWidget(self)
        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(8)
        self.style_list = ListWidget(page)
        self.style_list.setMinimumSize(QSize(200, 0))
        self.style_list.setMaximumSize(QSize(220, 16777215))
        self.style_list.currentRowChanged.connect(self.on_row_changed)
        left.addWidget(self.style_list, 1)

        list_buttons = QHBoxLayout()
        self.new_button = PushButton('新建', page)
        self.new_button.clicked.connect(self.create_style)
        self.delete_button = PushButton('删除', page)
        self.delete_button.clicked.connect(self.delete_style)
        list_buttons.addWidget(self.new_button)
        list_buttons.addWidget(self.delete_button)
        left.addLayout(list_buttons)

        self.reset_styles_button = PushButton('恢复默认风格', page)
        self.reset_styles_button.clicked.connect(self.reset_styles)
        left.addWidget(self.reset_styles_button)
        body.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.addWidget(BodyLabel('名称', page))
        self.name_edit = LineEdit(page)
        self.name_edit.setPlaceholderText('显示在改写窗口的风格下拉框里')
        self.name_edit.textChanged.connect(self.on_name_edited)
        name_row.addWidget(self.name_edit, 1)
        right.addLayout(name_row)

        right.addWidget(BodyLabel('改写要求', page))
        self.instruction_edit = PlainTextEdit(page)
        self.instruction_edit.setMinimumSize(QSize(0, 170))
        self.instruction_edit.textChanged.connect(self.on_instruction_edited)
        right.addWidget(self.instruction_edit)

        right.addWidget(BodyLabel('实际发送的提示词', page))
        self.preview_edit = PlainTextEdit(page)
        self.preview_edit.setReadOnly(True)
        right.addWidget(self.preview_edit, 1)
        body.addLayout(right, 1)
        return page

    # 模板分栏

    def build_template_page(self) -> QWidget:
        """创建系统提示词、用户模板和条件规则编辑区。"""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        hint = CaptionLabel(
            '占位符：{instruction} 改写要求 / {text} 讲稿原文 / {mark} 分隔符 / '
            '{segment_count} 分隔符数量 / {segment_rule} 分隔条件规则。', page)
        hint.setWordWrap(True)
        header.addWidget(hint, 1)
        self.reset_templates_button = PushButton('恢复默认模板', page)
        self.reset_templates_button.setMinimumSize(QSize(130, 33))
        self.reset_templates_button.clicked.connect(self.reset_templates)
        header.addWidget(self.reset_templates_button)
        layout.addLayout(header)

        self.system_edit = self._add_template_editor(layout, page, '系统提示词', 70)
        self.user_edit = self._add_template_editor(
            layout, page, '用户提示词模板', 190)
        self.segment_edit = self._add_template_editor(
            layout, page, '分隔符规则', 90)

        self.load_templates_into_editors()
        return page

    def _add_template_editor(self, layout, parent, label: str, height: int):
        """追加带标题的多行编辑器，并返回输入控件。"""
        layout.addWidget(BodyLabel(label, parent))
        editor = PlainTextEdit(parent)
        editor.setMinimumSize(QSize(0, height))
        editor.setMaximumSize(QSize(16777215, height + 60))
        editor.textChanged.connect(self.on_template_edited)
        layout.addWidget(editor)
        return editor

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
        box = MessageBox('恢复默认模板', '将丢弃当前全部提示词模板改动，确定继续吗？', self)
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
            self.style_list.addItem(QListWidgetItem(style['name']))
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
        self.styles[self._current_index] = {
            'name': self.name_edit.text().strip(),
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
            instruction=self.instruction_edit.toPlainText(), templates=templates)
        self.preview_edit.setPlainText(
            f'【系统提示词】\n{templates["system_prompt"]}\n\n【用户提示词】\n{user_prompt}')

    # 增删与恢复

    def create_style(self):
        """创建具有唯一名称的风格并立即选中。"""
        self.flush_editor()
        self.styles.append({
            'name': rewrite.unique_name(self.styles),
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
            self._info('warning', '无法删除', '至少需要保留一个改写风格。')
            return

        name = self.styles[self._current_index]['name']
        box = MessageBox('删除风格', f'确定删除"{name}"吗？', self)
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
            '恢复默认风格',
            '将丢弃当前全部风格（含自定义），恢复为内置的几种。\n此操作不可恢复，确定继续吗？',
            self,
        )
        box.yesButton.setText('恢复')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        self.styles = rewrite.default_styles()
        self.reload_list(select=0)
        self._info('success', '已恢复', '风格列表已重置为内置内容')

    # 提交

    def commit_and_accept(self):
        """校验名称、要求和必需占位符后写回应用偏好。"""
        self.flush_editor()

        seen = set()
        for index, style in enumerate(self.styles, start=1):
            if not style['name']:
                self._info('warning', '名称为空', f'第 {index} 个风格还没有名称。')
                return
            if not style['instruction']:
                self._info('warning', '改写要求为空', f'"{style["name"]}"还没有填写改写要求。')
                return
            if style['name'] in seen:
                self._info('warning', '名称重复', f'存在两个名为"{style["name"]}"的风格。')
                return
            seen.add(style['name'])

        templates = self.current_templates()
        if not templates['user_template'].strip():
            self._info('warning', '模板为空', '用户提示词模板不能为空。')
            self.pivot.setCurrentItem('templates')
            self.stack.setCurrentIndex(1)
            return
        if rewrite.REQUIRED_PLACEHOLDER not in templates['user_template']:
            self._info('warning', '缺少占位符',
                       f'用户提示词模板必须包含 {rewrite.REQUIRED_PLACEHOLDER}，'
                       f'否则讲稿正文不会被发送给模型。')
            self.pivot.setCurrentItem('templates')
            self.stack.setCurrentIndex(1)
            return

        self.app_settings.set('llm_styles', copy.deepcopy(self.styles))
        self.app_settings.set('llm_templates', templates)
        # 若原选择已被删除或改名，则将首项作为持久化选择。
        if str(self.app_settings.get('llm_style') or '') not in seen:
            self.app_settings.set('llm_style', self.styles[0]['name'])
        self.accept()

    def get_styles(self) -> list[dict]:
        """返回确认后写入配置的风格副本。"""
        return copy.deepcopy(self.styles)

    def _info(self, level: str, title: str, text: str):
        factory = {'success': InfoBar.success, 'warning': InfoBar.warning, 'error': InfoBar.error}[level]
        factory(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=-1 if level == 'error' else 4000,
                parent=self)
