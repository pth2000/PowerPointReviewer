"""放映控制：通过演示软件的 COM 接口翻页。

PowerPoint 的放映视图不响应投递的窗口消息，模拟按键只在它是前台窗口时有效。
COM 是演示软件提供的正规接口，与前台窗口无关，因此用它驱动翻页。

只连接已在运行的实例，不会启动演示软件；没有正在进行的放映时调用方需自行回退。
"""

import threading

from PySide6.QtCore import QObject, QTimer, Signal

# PowerPoint 与 WPS 演示提供兼容的对象模型，按顺序尝试
PROG_IDS = (
    ('PowerPoint.Application', 'PowerPoint'),
    ('Kwpp.Application', 'WPS 演示'),
)

# CoInitialize 按线程生效，标志也要按线程记录
_com_state = threading.local()


def _ensure_com() -> bool:
    """在当前线程初始化 COM，成功或已初始化时返回 True。"""
    if getattr(_com_state, 'ready', False):
        return True
    try:
        import pythoncom
        try:
            pythoncom.CoInitialize()
        except Exception:
            # Qt 可能已经初始化过，这种情况直接继续
            pass
        _com_state.ready = True
    except ImportError:
        return False
    return True


def _running_apps():
    """逐个连接已在运行的演示软件，返回 (显示名, 应用对象)。"""
    if not _ensure_com():
        return

    import win32com.client
    for prog_id, label in PROG_IDS:
        try:
            yield label, win32com.client.GetActiveObject(prog_id)
        except Exception:
            continue


def _active_views():
    """返回 (显示名, [放映视图, ...])，当前没有放映时返回 (None, [])。

    演讲者视图下可能存在多个放映窗口，全部返回以便同步推进。
    """
    for label, app in _running_apps():
        try:
            count = app.SlideShowWindows.Count
            if not count:
                continue
            views = []
            for index in range(1, count + 1):
                try:
                    views.append(app.SlideShowWindows(index).View)
                except Exception:
                    continue
            if views:
                return label, views
        except Exception:
            continue
    return None, []


def _active_show():
    """返回 (显示名, 首个放映视图)，当前没有放映时返回 (None, None)。"""
    label, views = _active_views()
    return (label, views[0]) if views else (None, None)


def is_available() -> bool:
    """返回当前是否存在可控制的放映。"""
    return _active_show()[1] is not None


def describe() -> str:
    """返回当前放映的简要信息，用于界面提示。"""
    label, view = _active_show()
    if view is None:
        return ''
    try:
        return f'{label}｜第 {view.CurrentShowPosition} 页'
    except Exception:
        return str(label)


def advance() -> bool:
    """让正在放映的演示前进一步；没有放映或全部调用失败时返回 False。

    与按一次翻页键等价，会依次触发页内动画。存在多个放映窗口时逐一推进。
    """
    _label, views = _active_views()
    if not views:
        return False

    advanced = False
    for view in views:
        try:
            view.Next()
            advanced = True
        except Exception as e:
            print(f'[放映] COM 翻页失败：{e}')
    return advanced


def back() -> bool:
    """让正在放映的演示后退一步。"""
    _label, view = _active_show()
    if view is None:
        return False
    try:
        view.Previous()
        return True
    except Exception as e:
        print(f'[放映] COM 后退失败：{e}')
        return False


def current_position() -> int:
    """返回当前放映页码，没有放映时返回 0。"""
    _label, view = _active_show()
    if view is None:
        return 0
    try:
        return int(view.CurrentShowPosition)
    except Exception:
        return 0


def goto(page: int) -> bool:
    """跳转到指定页；没有放映或页码越界时返回 False。"""
    _label, views = _active_views()
    if not views:
        return False

    jumped = False
    for view in views:
        try:
            view.GotoSlide(int(page))
            jumped = True
        except Exception as e:
            print(f'[放映] COM 跳页失败：{e}')
    return jumped


def read_open_presentation():
    """读取演示软件中已打开的演示文稿，返回 (文件路径, {页码: 备注})。

    不要求正在放映，编辑状态下同样可读。没有打开文稿时返回 (None, {})。
    """
    for _label, app in _running_apps():
        try:
            if not app.Presentations.Count:
                continue
            presentation = app.ActivePresentation
        except Exception:
            continue

        notes = {}
        try:
            for index in range(1, presentation.Slides.Count + 1):
                text = ''
                for shape in presentation.Slides(index).NotesPage.Shapes:
                    if shape.HasTextFrame and shape.TextFrame.HasText:
                        text += shape.TextFrame.TextRange.Text
                notes[index] = text
            return str(presentation.FullName), notes
        except Exception as e:
            print(f'[放映] 读取备注失败：{e}')
            continue
    return None, {}


class SlideshowWatcher(QObject):
    """订阅演示软件的放映事件，把换页通知转成 Qt 信号。

    COM 事件要在有消息泵的线程上派发，这里用定时器在 GUI 线程调用
    PumpWaitingMessages；仅在启用期间占用该定时器。
    """

    slide_changed = Signal(int)
    show_began = Signal()
    show_ended = Signal()

    PUMP_INTERVAL_MS = 120
    RETRY_INTERVAL_MS = 3000

    # 每隔若干次消息泵探测一次连接存活，约合一秒
    PROBE_EVERY = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connection = None
        self._app = None
        self._pump_ticks = 0
        self._pump_timer = QTimer(self)
        self._pump_timer.setInterval(self.PUMP_INTERVAL_MS)
        self._pump_timer.timeout.connect(self._pump)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(self.RETRY_INTERVAL_MS)
        self._retry_timer.timeout.connect(self._try_attach)

    def start(self):
        """开始订阅；演示软件未运行时会定期重试。"""
        if self._connection is not None:
            return
        if not self._try_attach():
            self._retry_timer.start()

    def stop(self):
        """停止订阅并释放连接。"""
        self._retry_timer.stop()
        self._pump_timer.stop()
        self._release()

    def _release(self):
        """断开事件接收器，避免演示软件继续持有回调引用。"""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._app = None

    def is_attached(self) -> bool:
        """返回当前是否已连上演示软件。"""
        return self._connection is not None

    def _try_attach(self) -> bool:
        for _label, app in _running_apps():
            try:
                import win32com.client
                self._connection = win32com.client.WithEvents(app, _make_handler(self))
            except Exception:
                continue
            self._app = app
            self._pump_ticks = 0
            self._retry_timer.stop()
            self._pump_timer.start()
            return True
        return False

    def _detach(self):
        """丢弃失效连接并回到重试状态。"""
        self._pump_timer.stop()
        self._release()
        self._retry_timer.start()

    def _alive(self) -> bool:
        """探测演示软件是否仍在运行。"""
        if self._app is None:
            return False
        try:
            self._app.Presentations.Count
            return True
        except Exception:
            return False

    def _pump(self):
        """派发挂起的 COM 事件，并定期确认连接仍然有效。

        演示软件退出时 PumpWaitingMessages 不会报错，只能主动探测，
        否则重开演示软件后无法重新订阅。
        """
        try:
            import pythoncom
            pythoncom.PumpWaitingMessages()
        except Exception:
            self._detach()
            return

        self._pump_ticks += 1
        if self._pump_ticks >= self.PROBE_EVERY:
            self._pump_ticks = 0
            if not self._alive():
                self._detach()


def _make_handler(watcher):
    """生成绑定到指定监听器的事件处理类。"""

    class _Handler:
        def OnSlideShowBegin(self, Wn):
            watcher.show_began.emit()

        def OnSlideShowNextSlide(self, Wn):
            try:
                watcher.slide_changed.emit(int(Wn.View.CurrentShowPosition))
            except Exception:
                pass

        def OnSlideShowEnd(self, Pres):
            watcher.show_ended.emit()

    return _Handler
