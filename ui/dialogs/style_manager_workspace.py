"""优化场景编辑、通用模板和可选提示词预览的布局。"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QListWidgetItem, QSplitter, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel, LineEdit, ListWidget, PrimaryPushButton, PushButton,
    StrongBodyLabel, SubtitleLabel, TransparentPushButton, setCustomStyleSheet,
)

from ui.dialogs.rewrite_widgets import WorkspaceSwitch, metadata_label, text_editor, workspace_frame


TEMPLATE_PARTS = (
    ('系统提示词', '设定所有场景共用的角色与处理原则。', ''),
    ('用户模板', '组合场景要求与讲稿正文，保留 {text} 以填入原文。',
     '可用占位符\n{instruction} 处理要求    {text} 讲稿原文\n'
     '{mark} 分隔符    {segment_count} 分隔符数量    {segment_rule} 分隔符规则'),
    ('分隔符规则', '约束页内分段与点击节奏，保留分隔符的数量与位置。',
     '可用占位符\n{mark} 分隔符    {segment_count} 分隔符数量'),
)


def _listing(parent):
    listing = ListWidget(parent)
    style = 'ListWidget { border: none; background: transparent; outline: none; padding: 0; }'
    setCustomStyleSheet(listing, style, style)
    return listing


class StyleManagerWorkspace:
    def setup_workspace(self):
        self.setWindowTitle('管理场景')
        self.setMinimumSize(960, 680)
        self.resize(1120, 780)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(18)
        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(6)
        title.addWidget(SubtitleLabel('管理场景', self))
        title.addWidget(metadata_label(CaptionLabel('为不同讲解任务设置处理要求。', self)))
        header.addLayout(title, 1)
        self.pivot = WorkspaceSwitch(252, self)
        self.pivot.addItem('styles', '优化场景', onClick=lambda: self.show_section(0))
        self.pivot.addItem('templates', '通用模板', onClick=lambda: self.show_section(1))
        self.pivot.setCurrentItem('styles')
        header.addWidget(self.pivot)
        root.addLayout(header)

        surface = workspace_frame(self, 'sceneSurface',
                                  'QFrame#sceneSurface { background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 12px; }',
                                  'QFrame#sceneSurface { background: #292929; border: 1px solid #414141; border-radius: 12px; }')
        body = QHBoxLayout(surface)
        body.setContentsMargins(1, 1, 1, 1)
        body.setSpacing(0)
        sidebar = workspace_frame(surface, 'sceneSidebar',
                                  'QFrame#sceneSidebar { background: #F8F8FA; border-top-left-radius: 11px; border-bottom-left-radius: 11px; }',
                                  'QFrame#sceneSidebar { background: #252525; border-top-left-radius: 11px; border-bottom-left-radius: 11px; }')
        sidebar.setFixedWidth(228)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(16, 20, 16, 12)
        self.navigation = QStackedWidget(sidebar)
        self.navigation.addWidget(self._scene_navigation(sidebar))
        self.navigation.addWidget(self._template_navigation(sidebar))
        navigation.addWidget(self.navigation)
        body.addWidget(sidebar)
        separator = QFrame(surface)
        separator.setFixedWidth(1)
        separator.setStyleSheet('background: rgba(128,128,128,0.16); border: none;')
        body.addWidget(separator)

        content = QWidget(surface)
        detail = QVBoxLayout(content)
        detail.setContentsMargins(22, 22, 22, 20)
        detail.setSpacing(16)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)
        self.heading_stack = QStackedWidget(content)
        name = QWidget(content)
        name_row = QHBoxLayout(name)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(14)
        name_row.addWidget(StrongBodyLabel('名称', name))
        self.name_edit = LineEdit(name)
        self.name_edit.setFixedHeight(36)
        self.name_edit.setPlaceholderText('场景名称')
        self.name_edit.textChanged.connect(self.on_name_edited)
        name_row.addWidget(self.name_edit, 1)
        self.heading_stack.addWidget(name)
        self.template_title = StrongBodyLabel('系统提示词', content)
        self.heading_stack.addWidget(self.template_title)
        toolbar.addWidget(self.heading_stack, 1)
        self.preview_button = PushButton('预览提示词', content)
        self.preview_button.setFixedSize(112, 36)
        self.preview_button.setCheckable(True)
        self.preview_button.toggled.connect(self.toggle_preview)
        toolbar.addWidget(self.preview_button)
        detail.addLayout(toolbar)

        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal, content)
        self.editor_splitter.setHandleWidth(16)
        self.editor_splitter.setChildrenCollapsible(False)
        self.stack = QStackedWidget(content)
        self.stack.setMinimumWidth(240)
        scene = QWidget(content)
        scene_body = QVBoxLayout(scene)
        scene_body.setContentsMargins(0, 0, 0, 0)
        scene_body.setSpacing(12)
        scene_body.addWidget(StrongBodyLabel('处理要求', scene))
        self.instruction_edit = text_editor(scene, font_size=14)
        self.instruction_edit.textChanged.connect(self.on_instruction_edited)
        scene_body.addWidget(self.instruction_edit, 1)
        self.stack.addWidget(scene)
        self.stack.addWidget(self._template_editor(content))
        self.editor_splitter.addWidget(self.stack)

        self.preview_panel = QWidget(content)
        self.preview_panel.setMinimumWidth(240)
        preview = QVBoxLayout(self.preview_panel)
        preview.setContentsMargins(0, 0, 0, 0)
        preview.setSpacing(12)
        preview.addWidget(StrongBodyLabel('完整提示词', self.preview_panel))
        hint = metadata_label(CaptionLabel('使用示例讲稿展示组合后的提示词。', self.preview_panel))
        hint.setWordWrap(True)
        preview.addWidget(hint)
        self.preview_edit = text_editor(self.preview_panel, reading=True, font_size=14)
        self.preview_edit.setReadOnly(True)
        preview.addWidget(self.preview_edit, 1)
        self.editor_splitter.addWidget(self.preview_panel)
        self.preview_panel.hide()
        detail.addWidget(self.editor_splitter, 1)
        body.addWidget(content, 1)
        root.addWidget(surface, 1)

        actions = QHBoxLayout()
        self.reset_button = TransparentPushButton('恢复默认场景', self)
        self.reset_button.clicked.connect(self.reset_current_section)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.clicked.connect(self.reject)
        self.cancel_button.setFixedSize(88, 36)
        actions.addWidget(self.cancel_button)
        self.ok_button = PrimaryPushButton('保存更改', self)
        self.ok_button.clicked.connect(self.commit_and_accept)
        self.ok_button.setFixedSize(128, 36)
        actions.addWidget(self.ok_button)
        root.addLayout(actions)
        self.template_list.currentRowChanged.connect(self.show_template)
        self.template_list.setCurrentRow(0)

    def _scene_navigation(self, parent):
        pane = QWidget(parent)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        toolbar = QHBoxLayout()
        toolbar.addWidget(StrongBodyLabel('优化场景', pane), 1)
        self.new_button = PushButton('新建', pane)
        self.new_button.setFixedSize(60, 32)
        self.new_button.clicked.connect(self.create_style)
        toolbar.addWidget(self.new_button)
        layout.addLayout(toolbar)
        self.style_list = _listing(pane)
        self.style_list.currentRowChanged.connect(self.on_row_changed)
        layout.addWidget(self.style_list, 1)
        self.delete_button = TransparentPushButton('删除场景', pane)
        self.delete_button.clicked.connect(self.delete_style)
        layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignLeft)
        return pane

    def _template_navigation(self, parent):
        pane = QWidget(parent)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = StrongBodyLabel('模板内容', pane)
        title.setFixedHeight(32)
        layout.addWidget(title)
        self.template_list = _listing(pane)
        for name, _, _ in TEMPLATE_PARTS:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 44))
            self.template_list.addItem(item)
        layout.addWidget(self.template_list, 1)
        return pane

    def _template_editor(self, parent):
        pane = QWidget(parent)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.template_hint = metadata_label(CaptionLabel(pane))
        self.template_hint.setWordWrap(True)
        layout.addWidget(self.template_hint)
        self.template_editors = QStackedWidget(pane)
        self.system_edit, self.user_edit, self.segment_edit = [text_editor(pane, font_size=14) for _ in TEMPLATE_PARTS]
        for editor in (self.system_edit, self.user_edit, self.segment_edit):
            editor.textChanged.connect(self.on_template_edited)
            self.template_editors.addWidget(editor)
        layout.addWidget(self.template_editors, 1)
        self.placeholder_hint = metadata_label(CaptionLabel(pane))
        self.placeholder_hint.setWordWrap(True)
        self.placeholder_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.placeholder_hint)
        return pane

    def show_section(self, index):
        self.navigation.setCurrentIndex(index)
        self.heading_stack.setCurrentIndex(index)
        self.stack.setCurrentIndex(index)
        self.pivot.setCurrentItem('templates' if index else 'styles')
        self.reset_button.setText('恢复默认模板' if index else '恢复默认场景')

    def show_template(self, index):
        if not 0 <= index < len(TEMPLATE_PARTS):
            return
        title, hint, placeholders = TEMPLATE_PARTS[index]
        self.template_title.setText(title)
        self.template_hint.setText(hint)
        self.template_editors.setCurrentIndex(index)
        self.placeholder_hint.setText(placeholders)
        self.placeholder_hint.setVisible(bool(placeholders))

    def toggle_preview(self, visible):
        self.preview_panel.setVisible(visible)
        self.preview_button.setText('收起预览' if visible else '预览提示词')
        if visible:
            self.editor_splitter.setSizes([600, 400])

    def reset_current_section(self):
        if self.stack.currentIndex():
            self.reset_templates()
        else:
            self.reset_styles()
