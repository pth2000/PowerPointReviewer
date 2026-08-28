"""在后台执行逐页 AI 改写和接口连通性测试。"""

import traceback

from PySide6.QtCore import QThread, Signal

from app import llm_client, rewrite


class RewriteTask(QThread):
    """逐页调用大模型，并通过信号流式回传各页结果和进度。"""

    signal_page_done = Signal(int, str, str)     # 页码、改写结果、结构警告。
    signal_page_failed = Signal(int, str)        # 页码、错误信息。
    signal_progress = Signal(int, int)           # 已处理数、总数。
    signal_finish = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages: list[tuple[int, str]] = []
        self.llm_config: dict = {}
        self.instruction = ''
        self.templates = rewrite.default_templates()
        self.mark = '●'
        self._cancelled = False

    def configure(self, pages, llm_config: dict, *, instruction: str, templates: dict,
                  mark: str):
        """复制待改写页面、模型配置和提示词数据，并清除取消标记。"""
        self.pages = list(pages)
        self.llm_config = dict(llm_config)
        self.instruction = instruction
        self.templates = dict(templates)
        self.mark = mark
        self._cancelled = False

    def cancel(self):
        """请求协作式取消；正在进行的网络请求完成后停止后续页面。"""
        self._cancelled = True

    def run(self):
        """顺序处理页面，使取消和逐页状态更新保持可预测。"""
        total = len(self.pages)
        for index, (page, text) in enumerate(self.pages, start=1):
            if self._cancelled:
                break

            try:
                messages = rewrite.build_messages(
                    text, mark=self.mark, instruction=self.instruction,
                    templates=self.templates)
                content = rewrite.clean_response(llm_client.chat(
                    messages=messages, **self.llm_config))

                if not content:
                    raise llm_client.LLMError('模型返回了空内容')

                self.signal_page_done.emit(
                    page, content, rewrite.check_segments(text, content, self.mark))
            except Exception as e:
                traceback.print_exc()
                self.signal_page_failed.emit(page, str(e))

            self.signal_progress.emit(index, total)

        self.signal_finish.emit()


class LLMTestTask(QThread):
    """在后台执行最小模型请求，避免连通性测试阻塞界面。"""

    signal_finish = Signal(bool, str)

    def __init__(self, llm_config: dict, parent=None):
        super().__init__(parent)
        self.llm_config = dict(llm_config)

    def run(self):
        """执行连接测试，并将异常转为统一的成功状态和消息。"""
        try:
            reply = llm_client.test_connection(
                base_url=self.llm_config.get('base_url', ''),
                api_key=self.llm_config.get('api_key', ''),
                model=self.llm_config.get('model', ''),
                timeout=min(int(self.llm_config.get('timeout', 30)), 30),
            )
        except Exception as e:
            self.signal_finish.emit(False, str(e))
            return

        self.signal_finish.emit(True, reply or '（模型返回了空内容，但连接正常）')
