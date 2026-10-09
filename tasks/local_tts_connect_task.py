"""本地 TTS 服务连接测试与音色刷新线程。"""

from PySide6.QtCore import QThread, Signal

from engines import qwentts


class LocalTtsConnectTask(QThread):
    signal_finish = Signal(object)
    signal_error = Signal(str)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self.base_url = base_url

    def run(self):
        try:
            info = qwentts.query_service(self.base_url, cancelled=self.isInterruptionRequested)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.signal_error.emit(str(exc))
            return
        if not self.isInterruptionRequested():
            self.signal_finish.emit(info)
