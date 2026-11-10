"""有界合成任务、缓存互斥及可取消的请求节流。"""

import threading
import time
import weakref
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

_cache_guard = threading.Lock()
_cache_locks = {}
_gate_guard = threading.Lock()
_gates = weakref.WeakValueDictionary()


class SynthesisHttpError(RuntimeError):
    def __init__(self, message, response):
        super().__init__(message)
        self.status_code = response.status_code
        self.response = response


def check_cancelled(cancelled):
    if cancelled():
        raise InterruptedError('配音生成已停止')


def interruptible_wait(seconds, cancelled):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled(cancelled)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


@contextmanager
def cache_lock(path, cancelled=lambda: False):
    key = str(path.resolve())
    with _cache_guard:
        entry = _cache_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    acquired = False
    try:
        while not acquired:
            check_cancelled(cancelled)
            acquired = entry[0].acquire(timeout=0.05)
        check_cancelled(cancelled)
        yield
    finally:
        if acquired:
            entry[0].release()
        with _cache_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _cache_locks[key]


def completed_map(function, items, workers, cancelled, stop):
    """仅保留 workers 个在途任务，错误或取消后停止派发并回收活动线程。"""
    iterator = iter(items)
    if workers == 1:
        for item in iterator:
            check_cancelled(cancelled)
            yield function(item)
        return
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='tts')
    pending = set()
    try:
        for _ in range(workers):
            check_cancelled(cancelled)
            item = next(iterator, None)
            if item is None:
                break
            pending.add(executor.submit(function, item))
        while pending:
            check_cancelled(cancelled)
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            # 先检查本批完成项的异常，再补充任务。
            values = [future.result() for future in done]
            yield from values
            for _ in done:
                check_cancelled(cancelled)
                item = next(iterator, None)
                if item is None:
                    break
                pending.add(executor.submit(function, item))
    except BaseException:
        stop()
        raise
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


class RequestGate:
    def __init__(self, workers, interval):
        self.workers = self.limit = workers
        self.interval = interval
        self.active = self.successes = 0
        self.next_start = self.blocked_until = 0.0
        self.condition = threading.Condition()

    @contextmanager
    def slot(self, cancelled):
        with self.condition:
            while True:
                check_cancelled(cancelled)
                delay = max(self.next_start, self.blocked_until) - time.monotonic()
                if self.active < self.limit and delay <= 0:
                    self.active += 1
                    self.next_start = time.monotonic() + self.interval
                    break
                self.condition.wait(min(0.05, max(0.001, delay)) if delay > 0 else 0.05)
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def throttle(self, delay):
        with self.condition:
            self.blocked_until = max(self.blocked_until, time.monotonic() + delay)
            self.limit, self.successes = 1, 0
            self.condition.notify_all()

    def succeeded(self):
        with self.condition:
            self.successes += 1
            if self.successes >= 8:
                self.limit, self.successes = self.workers, 0
                self.condition.notify_all()


def request_gate(identity, workers, interval=0.0):
    with _gate_guard:
        gate = _gates.get(identity)
        if gate is None:
            gate = _gates[identity] = RequestGate(workers, interval)
        return gate


class SynthesisSession:
    """单次生成复用每个工作线程的 HTTP 会话，共享账号级请求上限。"""

    def __init__(self, gate, cancelled):
        self.gate, self.cancelled = gate, cancelled
        self.local = threading.local()
        self.sessions = []
        self.lock = threading.Lock()

    def http(self):
        import requests

        if not hasattr(self.local, 'http'):
            session = self.local.http = requests.Session()
            with self.lock:
                self.sessions.append(session)
        return self.local.http

    def close(self):
        for session in self.sessions:
            session.close()
        self.sessions.clear()


def error_status(error):
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        status = (getattr(error, 'status_code', None) or getattr(error, 'status', None)
                  or getattr(getattr(error, 'response', None), 'status_code', None))
        if isinstance(status, int):
            return status
        error = error.__cause__ or error.__context__
    return None


def retry_after(error):
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        response = getattr(error, 'response', None)
        headers = response.get('headers') if isinstance(response, dict) else getattr(response, 'headers', None)
        value = headers.get('Retry-After') if headers is not None else None
        if isinstance(value, str):
            try:
                try:
                    seconds = float(value)
                except ValueError:
                    seconds = parsedate_to_datetime(value).timestamp() - time.time()
                return min(120, max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        error = error.__cause__ or error.__context__
    return 0.0
