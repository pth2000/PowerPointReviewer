"""在后台生成设置页使用的单条试听音频。"""

from PySide6.QtCore import QThread, Signal


class PreviewTask(QThread):
    """在后台生成单条试听音频，避免 TTS 请求阻塞界面。"""

    signal_finish = Signal(str)
    signal_error = Signal(str)

    def __init__(self, tts_engine, text, path, parent=None):
        super().__init__(parent)
        self.tts_engine = tts_engine
        self.text = text
        self.path = path

    def run(self):
        """生成试听文件，并通过成功或错误信号返回结果。"""
        try:
            self.tts_engine.save_file(self.text, self.path)
            self.signal_finish.emit(self.path)
        except Exception as e:
            self.signal_error.emit(str(e))
