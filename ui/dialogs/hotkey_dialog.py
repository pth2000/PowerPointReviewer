"""全局热键配置对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
)

from app import hotkeys
from ui.dialogs.base import ThemedDialog


# Qt 按键到本项目主键名的映射，仅覆盖可作为热键的按键
_QT_KEY_NAMES = {
    Qt.Key.Key_Space: 'Space', Qt.Key.Key_Return: 'Enter', Qt.Key.Key_Enter: 'Enter',
    Qt.Key.Key_Tab: 'Tab', Qt.Key.Key_Backspace: 'Backspace',
    Qt.Key.Key_Left: 'Left', Qt.Key.Key_Up: 'Up',
    Qt.Key.Key_Right: 'Right', Qt.Key.Key_Down: 'Down',
    Qt.Key.Key_PageUp: 'PageUp', Qt.Key.Key_PageDown: 'PageDown',
    Qt.Key.Key_Home: 'Home', Qt.Key.Key_End: 'End',
    Qt.Key.Key_Insert: 'Insert', Qt.Key.Key_Delete: 'Delete',
    Qt.Key.Key_QuoteLeft: '`', Qt.Key.Key_Minus: '-', Qt.Key.Key_Equal: '=',
    Qt.Key.Key_BracketLeft: '[', Qt.Key.Key_BracketRight: ']',
    Qt.Key.Key_Semicolon: ';', Qt.Key.Key_Apostrophe: "'",
    Qt.Key.Key_Comma: ',', Qt.Key.Key_Period: '.',
    Qt.Key.Key_Slash: '/', Qt.Key.Key_Backslash: '\\',
}
for _n in range(1, 13):
    _QT_KEY_NAMES[getattr(Qt.Key, f'Key_F{_n}')] = f'F{_n}'

_MODIFIER_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
}


class HotkeyEdit(LineEdit):
    """按下组合键即写入的只读输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText('点击后按下组合键')
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event):
        key = Qt.Key(event.key())
        if key in _MODIFIER_KEYS:
            return

        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace) and not event.modifiers():
            self.clear()
            return

        name = _QT_KEY_NAMES.get(key)
        if name is None:
            text = event.text().upper()
            if text and text in hotkeys.NAMED_KEYS:
                name = text
        if name is None:
            return

        mods = event.modifiers()
        value = 0
        if mods & Qt.KeyboardModifier.ControlModifier:
            value |= hotkeys.MOD_CONTROL
        if mods & Qt.KeyboardModifier.AltModifier:
            value |= hotkeys.MOD_ALT
        if mods & Qt.KeyboardModifier.ShiftModifier:
            value |= hotkeys.MOD_SHIFT
        if mods & Qt.KeyboardModifier.MetaModifier:
            value |= hotkeys.MOD_WIN
        if not value:
            return

        self.setText(hotkeys.format_combo(value, hotkeys.NAMED_KEYS[name]))


class HotkeyDialog(ThemedDialog):
    """配置全局热键，保存前提示内部冲突与系统占用。"""

    def __init__(self, app_settings, manager, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.manager = manager
        self.edits = {}

        self.setWindowTitle('全局热键')
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel('全局热键', self))

        tip = CaptionLabel(self)
        tip.setText('讲稿合成完成后生效')
        tip.setWordWrap(True)
        layout.addWidget(tip)

        switch_row = QHBoxLayout()
        switch_row.addWidget(BodyLabel('启用全局热键', self), stretch=1)
        self.enable_switch = SwitchButton(self)
        self.enable_switch.setOnText('启用')
        self.enable_switch.setOffText('停用')
        self.enable_switch.setChecked(bool(app_settings.get('hotkeys_enabled')))
        switch_row.addWidget(self.enable_switch)
        layout.addLayout(switch_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        bindings = hotkeys.normalize_bindings(app_settings.get('hotkeys'))
        for row, (action, label) in enumerate(hotkeys.ACTIONS):
            grid.addWidget(BodyLabel(label, self), row, 0)
            edit = HotkeyEdit(self)
            edit.setText(bindings.get(action, ''))
            edit.textChanged.connect(self.refresh_state)
            grid.addWidget(edit, row, 1)
            self.edits[action] = edit
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.status_label = CaptionLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        reset_button = PushButton('恢复默认', self)
        reset_button.clicked.connect(self.restore_defaults)
        button_row.addWidget(reset_button)
        button_row.addStretch(1)

        cancel_button = PushButton('取消', self)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        self.save_button = PrimaryPushButton('保存', self)
        self.save_button.clicked.connect(self.save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.refresh_state()

    def current_bindings(self) -> dict:
        """读取界面上的绑定。"""
        return {action: edit.text().strip() for action, edit in self.edits.items()}

    def restore_defaults(self):
        """把全部绑定还原为内置默认值。"""
        for action, edit in self.edits.items():
            edit.setText(hotkeys.DEFAULT_BINDINGS.get(action, ''))

    def refresh_state(self):
        """刷新内部冲突提示，并据此决定能否保存。"""
        labels = hotkeys.action_labels()
        conflicts = hotkeys.find_conflicts(self.current_bindings())
        if conflicts:
            detail = '；'.join(
                f'{combo} 被 {" 和 ".join(labels[a] for a in actions)} 共用'
                for combo, actions in conflicts.items())
            self.status_label.setText(f'存在重复绑定：{detail}')
            self.save_button.setEnabled(False)
        else:
            self.status_label.setText('')
            self.save_button.setEnabled(True)

    def save(self):
        """写回配置；系统占用的组合会在此暴露并阻止保存。"""
        bindings = self.current_bindings()

        if self.enable_switch.isChecked():
            rejected = self.manager.apply(bindings)
            self.manager.clear()
            if rejected:
                labels = hotkeys.action_labels()
                detail = '、'.join(
                    f'{labels[action]}（{combo}）' for action, combo in rejected.items())
                self.status_label.setText(f'已被其他程序占用：{detail}')
                return

        self.app_settings.set('hotkeys_enabled', bool(self.enable_switch.isChecked()))
        self.app_settings.set('hotkeys', bindings)
        self.accept()
