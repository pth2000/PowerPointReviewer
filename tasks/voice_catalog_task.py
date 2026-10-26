"""Edge-TTS 音色目录刷新线程"""

from app.logging_setup import get_logger

from PySide6.QtCore import QThread, Signal

from engines import edge_tts


class VoiceCatalogTask(QThread):
    """在后台强制刷新 Edge-TTS 音色目录。

    网络请求可能直到超时才失败，不能放在 GUI 线程中同步执行。
    """

    signal_finish = Signal(bool, str)

    def run(self):
        """刷新目录，并返回可直接展示的数量摘要或错误信息。"""
        try:
            catalog = edge_tts.load_catalog(force_refresh=True)
        except Exception as e:
            get_logger().exception('音色目录刷新失败')
            self.signal_finish.emit(False, str(e))
            return

        voices = sum(len(items) for items in catalog.values())
        self.signal_finish.emit(True, f'{voices} 个音色 / {len(catalog)} 个语言地区')
