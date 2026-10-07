"""全局热键：注册系统级快捷键，并把触发转成 Qt 信号。

演讲时前台窗口是 PowerPoint，应用内快捷键收不到按键，所以走 Win32 RegisterHotKey。
"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# 按住不放时只触发一次
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

# 顺序决定组合键的显示写法，例如 Ctrl+Alt+P
MODIFIERS = (('Ctrl', MOD_CONTROL), ('Alt', MOD_ALT), ('Shift', MOD_SHIFT), ('Win', MOD_WIN))

# 可作为主键的按键：显示名 -> 虚拟键码
NAMED_KEYS = {
    'Space': 0x20, 'Enter': 0x0D, 'Tab': 0x09, 'Backspace': 0x08,
    'Left': 0x25, 'Up': 0x26, 'Right': 0x27, 'Down': 0x28,
    'PageUp': 0x21, 'PageDown': 0x22, 'Home': 0x24, 'End': 0x23,
    'Insert': 0x2D, 'Delete': 0x2E,
    '`': 0xC0, '-': 0xBD, '=': 0xBB, '[': 0xDB, ']': 0xDD,
    ';': 0xBA, "'": 0xDE, ',': 0xBC, '.': 0xBE, '/': 0xBF, '\\': 0xDC,
}
for _n in range(1, 13):
    NAMED_KEYS[f'F{_n}'] = 0x6F + _n
for _code in range(ord('A'), ord('Z') + 1):
    NAMED_KEYS[chr(_code)] = _code
for _code in range(ord('0'), ord('9') + 1):
    NAMED_KEYS[chr(_code)] = _code

_VK_TO_NAME = {}
for _name, _vk in NAMED_KEYS.items():
    _VK_TO_NAME.setdefault(_vk, _name)


# 可绑定的动作：ID -> 界面显示名。顺序即配置界面中的行序。
ACTIONS = (
    ('play_pause', '播放 / 停止'),
    ('stop', '停止'),
    ('prev_segment', '上一段'),
    ('next_segment', '下一段'),
    ('restart_segment', '重播当前段'),
)

DEFAULT_BINDINGS = {
    'play_pause': 'Ctrl+Alt+Space',
    'stop': 'Ctrl+Alt+S',
    'prev_segment': 'Ctrl+Alt+Left',
    'next_segment': 'Ctrl+Alt+Right',
    'restart_segment': 'Ctrl+Alt+R',
}


def action_labels() -> dict:
    """返回动作 ID 到显示名的映射。"""
    return dict(ACTIONS)


def normalize_bindings(value) -> dict:
    """清洗持久化的绑定数据，只保留已知动作且可解析的组合。"""
    result = dict(DEFAULT_BINDINGS)
    if not isinstance(value, dict):
        return result
    known = {action for action, _ in ACTIONS}
    for action, text in value.items():
        if action not in known:
            continue
        text = str(text or '').strip()
        result[action] = text if (not text or parse_combo(text)) else DEFAULT_BINDINGS[action]
    return result


class _MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND),
        ('message', wintypes.UINT),
        ('wParam', wintypes.WPARAM),
        ('lParam', wintypes.LPARAM),
        ('time', wintypes.DWORD),
        ('pt_x', wintypes.LONG),
        ('pt_y', wintypes.LONG),
    ]


def format_combo(mods: int, vk: int) -> str:
    """把修饰键掩码与虚拟键码渲染成 Ctrl+Alt+P 形式。"""
    name = _VK_TO_NAME.get(vk)
    if name is None:
        return ''
    parts = [label for label, flag in MODIFIERS if mods & flag]
    parts.append(name)
    return '+'.join(parts)


def parse_combo(text: str):
    """解析 Ctrl+Alt+P，返回 (修饰键掩码, 虚拟键码)。无法解析时返回 None。

    组合至少包含一个修饰键，纯字母会与正常输入冲突。
    """
    parts = [part.strip() for part in str(text or '').split('+') if part.strip()]
    if len(parts) < 2:
        return None

    *modifier_names, key_name = parts
    lookup = {label.lower(): flag for label, flag in MODIFIERS}
    mods = 0
    for name in modifier_names:
        flag = lookup.get(name.lower())
        if flag is None:
            return None
        mods |= flag

    vk = NAMED_KEYS.get(key_name)
    if vk is None:
        vk = NAMED_KEYS.get(key_name.upper())
    if vk is None:
        vk = NAMED_KEYS.get(key_name.title())
    if vk is None:
        return None
    return mods, vk


def find_conflicts(bindings: dict) -> dict:
    """找出绑定到同一组合的动作，返回 {组合: [动作, ...]}。"""
    seen = {}
    for action, text in bindings.items():
        combo = parse_combo(text)
        if combo is None:
            continue
        seen.setdefault(combo, []).append(action)
    return {format_combo(*combo): actions
            for combo, actions in seen.items() if len(actions) > 1}


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    """注册全局热键并在触发时发出动作 ID。"""

    triggered = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._user32 = ctypes.windll.user32
        self._actions = {}
        self._next_id = 1
        self._filter_installed = False

    def apply(self, bindings: dict) -> dict:
        """注册一组热键，返回未能注册的 {动作: 组合}。

        Windows 不提供“查询某组合被谁占用”的接口，只能依据注册结果判断可用性。
        """
        self.clear()
        rejected = {}

        for action, text in bindings.items():
            text = str(text or '').strip()
            if not text:
                continue
            combo = parse_combo(text)
            if combo is None:
                rejected[action] = text
                continue

            mods, vk = combo
            hotkey_id = self._next_id
            if self._user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                self._actions[hotkey_id] = action
                self._next_id += 1
            else:
                rejected[action] = text

        if self._actions and not self._filter_installed:
            app = QCoreApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self)
                self._filter_installed = True
        return rejected

    def clear(self):
        """注销当前已注册的全部热键。"""
        for hotkey_id in list(self._actions):
            self._user32.UnregisterHotKey(None, hotkey_id)
        self._actions.clear()
        # 热键 ID 只需在注册期间唯一，复位可避免长期运行后超出系统上限
        self._next_id = 1

    def is_active(self) -> bool:
        """返回当前是否有热键处于注册状态。"""
        return bool(self._actions)

    def nativeEventFilter(self, event_type, message):
        if event_type != b'windows_generic_MSG':
            return False, 0

        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        if msg.message == WM_HOTKEY:
            action = self._actions.get(int(msg.wParam))
            if action:
                self.triggered.emit(action)
                return True, 0
        return False, 0
