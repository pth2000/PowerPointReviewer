"""将主窗口中的本地文件拖放统一交给导入入口。"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

from ui.file_drop_overlay import FileDropOverlay
from ui.widget_state import active_modal_dialog

class FileDropImport(QObject):
    """拦截主窗口子控件的文件拖放，避免文件路径落入输入框。"""

    files_dropped = Signal(list)
    _DROP_EVENTS = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.DragLeave, QEvent.Type.Drop)

    def __init__(self, window, on_drop, *, overlay_parent=None, busy_check=None, linked_check=None):
        super().__init__(window)
        self.window = window
        self.busy_check = busy_check or (lambda: False)
        self.linked_check = linked_check or (lambda: False)
        self._overlay_parent = overlay_parent if overlay_parent is not None else window
        self.overlay = FileDropOverlay(self._overlay_parent)
        self._drag_active = False
        self.overlay.destroyed.connect(self._remove_event_filter)
        window.destroyed.connect(self._remove_event_filter)
        window.setAcceptDrops(True)
        # 排队处理使文件解析、通知和对话框发生在系统拖放结束之后。
        self.files_dropped.connect(on_drop, Qt.ConnectionType.QueuedConnection)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.window and event.type() == QEvent.Type.Hide:
            self._dismiss()
        if obj is self._overlay_parent and event.type() == QEvent.Type.Resize:
            self.overlay.setGeometry(obj.rect())
        if event.type() not in self._DROP_EVENTS:
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.window:
            return False
        if event.type() == QEvent.Type.DragLeave:
            if not self._drag_active:
                return False
            self._drag_active = False
            # 同一轮事件中的子控件切换会再次进入，不在它们之间闪烁。
            QTimer.singleShot(0, self, self._dismiss_after_leave)
            return False
        # 子对话框（例如视频录制和音色管理）保留各自的操作流程。
        if active_modal_dialog(self.window) is not None:
            self._dismiss()
            event.ignore()
            return True
        urls = event.mimeData().urls()
        if not urls or not all(url.isLocalFile() for url in urls):
            self._dismiss()
            return False
        if not event.possibleActions() & Qt.DropAction.CopyAction:
            self._dismiss()
            event.ignore()
            return True
        # 导入不会移动源文件，即使拖放源建议使用 MoveAction。
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        files = [url.toLocalFile() for url in urls]
        if event.type() == QEvent.Type.Drop:
            self._dismiss()
            self.files_dropped.emit(files)
        else:
            self._drag_active = True
            self.overlay.show_files(files, busy=bool(self.busy_check()), updating=bool(self.linked_check()))
        return True

    def _dismiss(self):
        self._drag_active = False
        self.overlay.dismiss()

    def _dismiss_after_leave(self):
        if not self._drag_active:
            self.overlay.dismiss()

    def _remove_event_filter(self, *_):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
