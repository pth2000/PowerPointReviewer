"""试听音频生成线程模块"""

from PySide6.QtCore import QThread, Signal


class PreviewTask(QThread):
    """试听语音生成线程，防止阻塞 UI"""

    signal_finish = Signal(str)
    signal_error = Signal(str)

    def __init__(self, tts_engine, text, path, parent=None):
        super().__init__(parent)
        self.tts_engine = tts_engine
        self.text = text
        self.path = path

    def run(self):
        """异步生成试听音频"""
        try:
            self.tts_engine.save_file(self.text, self.path)
            self.signal_finish.emit(self.path)
        except Exception as e:
            self.signal_error.emit(str(e))
