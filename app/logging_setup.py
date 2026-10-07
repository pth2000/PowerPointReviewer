"""运行日志：把诊断输出落盘，供打包版排查问题。

打包后控制台被关闭，print 的内容直接丢弃，出问题时用户手里只剩界面上的一句提示。
本模块接管标准输出与未捕获异常，统一写入滚动日志文件。
"""

import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from app import paths


LOGGER_NAME = 'pptreviewer'

_configured = False


def get_logger() -> logging.Logger:
    """返回应用日志器。"""
    return logging.getLogger(LOGGER_NAME)


# 来源列宽度，超出部分从左侧截断
_SOURCE_WIDTH = 22


def _caller_module() -> str:
    """取调用 print 的模块名。

    print 是 C 函数、不产生 Python 栈帧，因此从本函数向上数两层即调用点：
    0 为本函数、1 为 write、2 为调用 print 的代码。
    比解析文本前缀可靠，也能覆盖没有写前缀的调用。
    """
    try:
        frame = sys._getframe(2)
    except (ValueError, AttributeError):
        return 'stdout'
    name = frame.f_globals.get('__name__', 'stdout')
    return name or 'stdout'


class _SourceFilter(logging.Filter):
    """确保每条记录都带 source 字段。

    经标准流捕获的记录已在写入时指定来源；直接调用 logger 的记录回落到
    logging 自带的模块名。
    """

    def filter(self, record):
        source = getattr(record, 'source', None) or record.module
        if len(source) > _SOURCE_WIDTH:
            source = '…' + source[-(_SOURCE_WIDTH - 1):]
        record.source = source
        return True


class _StreamToLog:
    """把写往标准流的内容同时送到原始流与日志。

    项目里已有大量 print 调用，第三方库也会直接写标准输出。接管这两个流可以
    一次性覆盖，不用逐处改写。源码运行时原始流还在，控制台输出不受影响。
    """

    def __init__(self, original, level: int):
        self._original = original
        self._level = level
        self._buffer = ''

    def write(self, text):
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass

        self._buffer += text
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            if line.strip():
                get_logger().log(self._level, line.rstrip(),
                                 extra={'source': _caller_module()})
        return len(text)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._original is not None and self._original.isatty())
        except Exception:
            return False


def _install_excepthooks() -> None:
    """把主线程与子线程的未捕获异常写进日志。

    QThread 中抛出的异常原本只写往已关闭的控制台，表现为任务无声中断。
    """

    def handle_main(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        get_logger().error(
            '未捕获异常\n%s',
            ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip(),
            extra={'source': '!异常'})

    def handle_thread(args):
        if issubclass(args.exc_type, SystemExit):
            return
        name = args.thread.name if args.thread is not None else '未知线程'
        get_logger().error(
            '线程 %s 未捕获异常\n%s', name,
            ''.join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)).rstrip(),
            extra={'source': '!异常'})

    sys.excepthook = handle_main
    threading.excepthook = handle_thread


def setup(version: str = '', *, max_bytes: int = 1024 * 1024, backup_count: int = 3):
    """初始化日志，重复调用无副作用。"""
    global _configured
    if _configured:
        return get_logger()

    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    # 不向 root 传播，第三方库配置 root 时不会产生重复记录
    logger.propagate = False

    handler = RotatingFileHandler(
        paths.LOG_FILE, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        f'%(asctime)s %(levelname)-7s %(source)-{_SOURCE_WIDTH}s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'))
    handler.addFilter(_SourceFilter())
    logger.addHandler(handler)

    # logging 默认把处理器异常打印到 stderr，而 stderr 已被本模块接管，会形成递归
    logging.raiseExceptions = False

    sys.stdout = _StreamToLog(sys.stdout, logging.INFO)
    sys.stderr = _StreamToLog(sys.stderr, logging.ERROR)
    _install_excepthooks()

    _configured = True
    banner = {'source': '启动'}
    logger.info('=' * 60, extra=banner)
    logger.info('PowerPointReviewer %s（%s）', version or '未知版本',
                '打包运行' if getattr(sys, 'frozen', False) else '源码运行', extra=banner)
    logger.info('Python %s / 日志上限 %.1f MB × %d 个备份',
                sys.version.split()[0], max_bytes / 1024 / 1024, backup_count + 1,
                extra=banner)
    logger.info('应用根目录 %s', paths.APP_ROOT, extra=banner)
    logger.info('数据目录 %s', paths.DATA_ROOT, extra=banner)
    return logger
