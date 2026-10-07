"""定向翻页：把按键投递给用户指定的窗口，而不是当前前台窗口。

用于 COM 接口覆盖不到的演示软件。PowerPoint 与 WPS 演示优先使用 slideshow 模块，
那是它们的正规接口。

投递的是滚轮消息而非按键消息：实测 PowerPoint 放映会忽略投递的 WM_KEYDOWN，
它通过低级输入路径读键盘；而鼠标消息走常规窗口过程，滚轮向下与翻页键效果相同。

窗口句柄会被系统回收，跨次启动后可能指向别的窗口，因此除句柄外还记录
进程、窗口类与标题，取用前逐项核对，对不上就当作目标已失效。
"""

import ctypes
import os
from ctypes import wintypes


WM_MOUSEWHEEL = 0x020A
WHEEL_DELTA = 120

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 自身窗口不作为候选
_SELF_PID = os.getpid()

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _window_text(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ''
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_class(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _window_area(hwnd) -> int:
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0
    return max(rect.right - rect.left, 0) * max(rect.bottom - rect.top, 0)


def _process_name(pid: int) -> str:
    """返回进程可执行文件名；受保护进程可能取不到，此时返回空串。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ''
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ''
    finally:
        _kernel32.CloseHandle(handle)


def list_windows() -> list:
    """枚举可见且有标题的顶层窗口，按面积降序返回。"""
    found = []

    def collect(hwnd, _param):
        # 最小化的窗口不可能是放映目标
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return True
        title = _window_text(hwnd)
        if not title:
            return True

        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _SELF_PID:
            return True

        found.append({
            'hwnd': int(hwnd),
            'pid': int(pid.value),
            'process': _process_name(pid.value),
            'title': title,
            'class_name': _window_class(hwnd),
            'area': _window_area(hwnd),
        })
        return True

    _user32.EnumWindows(_EnumWindowsProc(collect), 0)
    found.sort(key=lambda item: item['area'], reverse=True)
    return found


def make_target(window: dict) -> dict:
    """把候选窗口转换成可持久化的目标记录。"""
    return {
        'hwnd': int(window['hwnd']),
        'process': window['process'],
        'class_name': window['class_name'],
        'title': window['title'],
    }


def resolve(target) -> int:
    """校验目标记录仍指向同一个窗口，返回句柄；失效时返回 0。

    句柄会被系统回收，只看句柄有效可能命中无关窗口，因此逐项核对身份。
    """
    if not isinstance(target, dict):
        return 0

    hwnd = int(target.get('hwnd') or 0)
    if not hwnd or not _user32.IsWindow(hwnd) or not _user32.IsWindowVisible(hwnd):
        return 0

    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if _process_name(pid.value) != target.get('process', ''):
        return 0
    if _window_class(hwnd) != target.get('class_name', ''):
        return 0
    return hwnd


def describe(target) -> str:
    """返回目标窗口的简要描述，用于界面提示。"""
    if not resolve(target):
        return ''
    process = target.get('process', '')
    title = _window_text(int(target['hwnd'])) or target.get('title', '')
    return f'{process}｜{title}' if process else title


def _window_center(hwnd):
    """返回窗口中心的屏幕坐标，滚轮消息的 lParam 使用屏幕坐标。"""
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def send_scroll(hwnd, forward: bool = True) -> bool:
    """向指定窗口投递一次滚轮消息，窗口无效时返回 False。

    向下滚动对应下一页，向上滚动对应上一页。
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return False

    delta = -WHEEL_DELTA if forward else WHEEL_DELTA
    wparam = (delta & 0xFFFF) << 16
    x, y = _window_center(hwnd)
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    _user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
    return True
