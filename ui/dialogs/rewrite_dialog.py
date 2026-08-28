"""逐页对照、编辑并确认 AI 讲稿改写结果。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    ListWidget,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
)

from app import rewrite
from tasks.rewrite_task import RewriteTask
from ui.dialogs.llm_config_dialog import LLMConfigMessageBox
from ui.dialogs.style_manager_dialog import StyleManagerDialog

STATUS_PENDING = '待改写'
STATUS_DONE = '已改写'
STATUS_WARN = '需确认'
STATUS_FAILED = '失败'
STATUS_BLANK = '空白页'


class RewriteDialog(QDialog):
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
        self.test_thread = None

        self.setWindowTitle('AI 优化')
        self.setMinimumSize(960, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        caption = CaptionLabel(
            '左侧勾选需处理的页后可进行批量处理。模型生成的候选结果可在手动编辑后再应用。', self)
        caption.setWordWrap(True)
        root.addWidget(caption)

        # 改写参数
        option_row = QHBoxLayout()
        option_row.addWidget(BodyLabel('改写风格', self))
        self.style_combo = ComboBox(self)
        option_row.addWidget(self.style_combo)
        self.reload_styles()

        self.style_button = PushButton('管理风格', self)
        self.style_button.clicked.connect(self.open_style_manager)
        option_row.addWidget(self.style_button)

        option_row.addStretch(1)

        self.config_button = PushButton('接口配置', self)
        self.config_button.clicked.connect(self.open_config_dialog)
        option_row.addWidget(self.config_button)

        self.start_button = PrimaryPushButton('开始改写', self)
        self.start_button.clicked.connect(self.start_rewrite)
        option_row.addWidget(self.start_button)

        self.cancel_task_button = PushButton('停止', self)
        self.cancel_task_button.setEnabled(False)
        self.cancel_task_button.clicked.connect(self.cancel_rewrite)
        option_row.addWidget(self.cancel_task_button)
        root.addLayout(option_row)

        self.progress = ProgressBar(self)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # 页面选择与原文/结果对照
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self.page_list = ListWidget(splitter)
        # 高亮只控制右侧展示，勾选框才定义改写和写回范围。
        self.page_list.currentRowChanged.connect(self.show_page)
        self.page_list.itemChanged.connect(self.on_item_changed)
        splitter.addWidget(self.page_list)

        right = QSplitter(Qt.Orientation.Vertical, splitter)

        original_box = QVBoxLayout()
        self.original_edit = PlainTextEdit(right)
        self.original_edit.setReadOnly(True)
        original_box.addWidget(self.original_edit)

        self.original_label = StrongBodyLabel('原文', right)
        self.rewritten_label = StrongBodyLabel('改写结果', right)
        self.rewritten_edit = PlainTextEdit(right)
        self.rewritten_edit.textChanged.connect(self.on_rewritten_edited)

        top_pane = _pane(right, self.original_label, self.original_edit)
        bottom_pane = _pane(right, self.rewritten_label, self.rewritten_edit)
        right.addWidget(top_pane)
        right.addWidget(bottom_pane)
        right.setSizes([260, 300])

        splitter.addWidget(right)
        splitter.setSizes([240, 700])
        root.addWidget(splitter, 1)

        self.warning_label = CaptionLabel('', self)
        self.warning_label.setWordWrap(True)
        root.addWidget(self.warning_label)

        # 批量操作
        action_row = QHBoxLayout()
        self.select_all_button = PushButton('全选', self)
        self.select_all_button.clicked.connect(lambda: self.set_all_checked(True))
        action_row.addWidget(self.select_all_button)

        self.select_none_button = PushButton('清空选择', self)
        self.select_none_button.clicked.connect(lambda: self.set_all_checked(False))
        action_row.addWidget(self.select_none_button)

        action_row.addStretch(1)

        self.apply_button = PrimaryPushButton('应用勾选页', self)
        self.apply_button.clicked.connect(self.apply_selected)
        action_row.addWidget(self.apply_button)

        self.close_button = PushButton('取消', self)
        self.close_button.clicked.connect(self.reject)
        action_row.addWidget(self.close_button)
        root.addLayout(action_row)

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

    def reload_styles(self):
        """重建风格下拉，并尽量恢复原有选择。"""
        self.styles = rewrite.normalize_styles(self.app_settings.get('llm_styles'))
        wanted = self.style_combo.currentText() or str(self.app_settings.get('llm_style') or '')

        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItems(rewrite.style_names(self.styles))
        index = self.style_combo.findText(wanted)
        self.style_combo.setCurrentIndex(index if index >= 0 else 0)
        self.style_combo.blockSignals(False)

    def open_style_manager(self):
        """打开风格管理器，并在确认后刷新选择列表。"""
        dialog = StyleManagerDialog(self.app_settings, self.mark, self)
        if dialog.exec():
            self.reload_styles()

    # 列表与展示

    def populate_pages(self):
        """按页码填充列表，并将空白页设为不可勾选。"""
        self._syncing = True
        self.page_list.clear()
        for page in sorted(self.notes):
            text = str(self.notes[page]).strip()
            item = QListWidgetItem(self._label_for(page, STATUS_PENDING if text else STATUS_BLANK))
            item.setData(Qt.ItemDataRole.UserRole, page)
            if text:
                # 整篇处理是常见路径，非空白页默认全部勾选。
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.page_list.addItem(item)
        self._syncing = False

        if self.page_list.count():
            self.page_list.setCurrentRow(0)
        self.update_action_state()

    def update_action_state(self):
        """根据选择范围和任务状态更新操作按钮。"""
        running = self.task is not None
        checked = self.checked_pages()
        applicable = [p for p in checked if self.results.get(p, {}).get('text')]

        # 选择工具不依赖任务状态，用户可提前调整下一批范围。
        self.start_button.setEnabled(not running and bool(checked))
        self.apply_button.setEnabled(not running and bool(applicable))
        self.cancel_task_button.setEnabled(running)

    def on_item_changed(self, _item):
        """在页面勾选状态变化后刷新操作状态。"""
        if not self._syncing:
            self.update_action_state()

    def checked_pages(self):
        """返回当前勾选页码；改写和写回共用该范围。"""
        pages = []
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                pages.append(item.data(Qt.ItemDataRole.UserRole))
        return pages

    # 图标表达处理状态，勾选框只表达用户选择，二者职责保持独立。
    STATUS_ICONS = {
        STATUS_DONE: FluentIcon.ACCEPT,
        STATUS_WARN: FluentIcon.INFO,
        STATUS_FAILED: FluentIcon.CLOSE,
    }

    @staticmethod
    def _label_for(page: int, status: str) -> str:
        return f'第 {page} 页 / {status}'

    def _set_item_status(self, page: int, status: str):
        """更新页面状态标签和图标，不改变用户勾选。"""
        item = self._item_for_page(page)
        if item is None:
            return
        self._syncing = True
        item.setText(self._label_for(page, status))
        icon = self.STATUS_ICONS.get(status)
        item.setIcon(icon.icon() if icon is not None else QIcon())
        self._syncing = False

    def _item_for_page(self, page: int):
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == page:
                return item
        return None

    def current_page(self):
        item = self.page_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_page(self, _row: int):
        """切换高亮页时刷新原文、候选结果和警告。"""
        page = self.current_page()
        if page is None:
            return

        self.original_edit.setPlainText(str(self.notes.get(page, '')))

        result = self.results.get(page, {})
        self.rewritten_edit.blockSignals(True)
        self.rewritten_edit.setPlainText(str(result.get('text', '')))
        self.rewritten_edit.blockSignals(False)
        self.rewritten_edit.setReadOnly(not result.get('text'))

        message = result.get('error') or result.get('warning') or ''
        self.warning_label.setText(f'提示：{message}' if message else '')

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
            self._set_item_status(page, STATUS_WARN if warning else STATUS_DONE)
        self.warning_label.setText(f'提示：{warning}' if warning else '')

    # 改写流程

    def pending_pages(self):
        """收集勾选且非空白的待处理页面。"""
        wanted = set(self.checked_pages())
        return [(page, str(self.notes[page])) for page in sorted(self.notes)
                if page in wanted and str(self.notes[page]).strip()]

    def start_rewrite(self):
        """启动当前勾选页的改写，并保留未勾选页的已有结果。"""
        pages = self.pending_pages()
        if not pages:
            self._info_warning('没有可改写的页', '请先在左侧勾选要改写的页。')
            return

        llm_config = self.current_llm_config()
        if not llm_config['base_url'] or not llm_config['model']:
            self._info_warning('尚未配置接口', '请先点击"接口配置"填写服务地址与模型名称。')
            return

        self.app_settings.set('llm_style', self.style_combo.currentText())

        for page, _text in pages:
            self.results.pop(page, None)
            self._set_item_status(page, STATUS_PENDING)

        self.progress.setRange(0, len(pages))
        self.progress.setValue(0)
        self.progress.setVisible(True)

        self.task = RewriteTask(self)
        self.update_action_state()
        self.task.configure(
            pages, llm_config,
            instruction=rewrite.find_instruction(self.styles, self.style_combo.currentText()),
            templates=rewrite.normalize_templates(self.app_settings.get('llm_templates')),
            mark=self.mark,
        )
        self.task.signal_page_done.connect(self.on_page_done)
        self.task.signal_page_failed.connect(self.on_page_failed)
        self.task.signal_progress.connect(self.on_progress)
        self.task.signal_finish.connect(self.on_finish)
        self.task.finished.connect(self.task.deleteLater)
        self.task.start()

    def cancel_rewrite(self):
        """请求协作式停止；当前网络请求结束后退出。"""
        if self.task is not None:
            self.task.cancel()
        self.cancel_task_button.setEnabled(False)

    def on_page_done(self, page: int, text: str, warning: str):
        """保存单页候选结果，并更新其结构状态。"""
        self.results[page] = {'text': text, 'warning': warning, 'error': ''}
        self._set_item_status(page, STATUS_WARN if warning else STATUS_DONE)

        if self.current_page() == page:
            self.show_page(self.page_list.currentRow())
        self.update_action_state()

    def on_page_failed(self, page: int, message: str):
        """记录单页错误，其余页面继续处理。"""
        self.results[page] = {'text': '', 'warning': '', 'error': message}
        self._set_item_status(page, STATUS_FAILED)

        if self.current_page() == page:
            self.show_page(self.page_list.currentRow())
        self.update_action_state()

    def on_progress(self, done: int, total: int):
        self.progress.setValue(done)

    def on_finish(self):
        """清理任务引用并恢复界面操作状态。"""
        # 线程随后由 deleteLater 销毁；先断开引用，避免关闭弹窗时访问失效的 C++ 对象。
        self.task = None
        self.progress.setVisible(False)
        self.update_action_state()

        succeeded = sum(1 for r in self.results.values() if r.get('text'))
        failed = sum(1 for r in self.results.values() if r.get('error'))

        if failed and succeeded:
            self._info_warning('部分完成', f'成功 {succeeded} 页，失败 {failed} 页，可对失败页重试。')
        elif failed:
            self._info_error('改写失败', '所有页面均未成功，请检查服务地址、API Key 与模型名称。')
        else:
            self._info_success('改写完成', f'已生成 {succeeded} 页候选文本，请逐页确认后应用。')

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

    def apply_selected(self):
        """校验并收集勾选页结果，确认后关闭弹窗。"""
        applied = {}
        risky = []
        for page in self.checked_pages():
            result = self.results.get(page, {})
            if result.get('text'):
                applied[page] = result['text']
                if result.get('warning'):
                    risky.append(page)

        if not applied:
            self._info_warning('没有可应用的结果', '请勾选至少一页已改写的内容。')
            return

        if risky:
            pages_text = '、'.join(f'第 {p} 页' for p in risky[:8])
            box = MessageBox(
                '存在结构变化',
                f'{pages_text} 的页内分隔符数量与原文不一致，写回后点击节奏会改变。'
                f'\n\n仍要应用吗？',
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
            self.task.cancel()
            self.task.wait(5000)
        super().reject()

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


def _pane(parent, label, editor):
    """将标题和编辑器封装为可放入分割器的面板。"""
    from PySide6.QtWidgets import QWidget

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(label)
    layout.addWidget(editor, 1)
    return widget
