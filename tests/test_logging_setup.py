"""隔离验证日志入口、异常堆栈、脱敏及文件滚动，避免改写测试进程的全局钩子。"""

from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

from app.logging_setup import _redact


class LoggingSetupTests(unittest.TestCase):
    def run_script(self, code):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', textwrap.dedent(code), directory],
                                    capture_output=True, text=True, encoding='utf-8',
                                    env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                                         'PYTHONIOENCODING': 'utf-8'}, timeout=30)
            logs = {p.name: p.read_text(encoding='utf-8') for p in Path(directory).glob('*.log*')}
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr + str(logs))
            return logs

    def test_error_paths_reach_file_with_tracebacks_and_secrets_redacted(self):
        logs = self.run_script('''
            import logging, sys, threading
            from pathlib import Path
            from PySide6.QtCore import QCoreApplication, QThread, QTimer, qWarning
            from app import paths
            from app.logging_setup import setup, log_error
            paths.LOG_DIR = Path(sys.argv[1])
            paths.LOG_FILE = paths.LOG_DIR / 'app.log'
            app = QCoreApplication([])
            logger = setup('fixture')
            setup('same-process')
            logging.getLogger('app.video.fixture').info('module-info-marker')
            logging.getLogger('dependency').warning('dependency-warning-marker')
            print('stdout-marker')
            sys.stderr.write('stderr-without-newline-marker')
            sys.stderr.flush()
            try:
                raise ValueError('handled-error-marker api_key=private-123')
            except ValueError:
                log_error('生成失败', 'handled-notice-marker')
            def fail():
                raise RuntimeError('thread-error-marker')
            thread = threading.Thread(target=fail)
            thread.start()
            thread.join()
            class Worker(QThread):
                def run(self):
                    raise RuntimeError('qthread-error-marker')
            worker = Worker()
            worker.start()
            worker.wait()
            def callback():
                raise RuntimeError('qt-callback-error-marker')
            QTimer.singleShot(0, callback)
            QTimer.singleShot(30, app.quit)
            app.exec()
            class Unraisable:
                def __del__(self):
                    raise ValueError('unraisable-error-marker')
            del_object = Unraisable()
            del del_object
            try:
                raise RuntimeError('main-error-marker')
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
            qWarning('qt-warning-marker')
            logger.error('Authorization: Bearer sample-token-value password=private-pass')
            logging.shutdown()
        ''')
        text = logs['app.log']
        for marker in ('module-info', 'dependency-warning', 'stdout', 'stderr-without-newline',
                       'handled-error', 'handled-notice', 'thread-error', 'qthread-error',
                       'qt-callback-error', 'unraisable-error', 'main-error', 'qt-warning'):
            self.assertEqual(len(re.findall(r'(?<![\w-])' + marker + r'-marker\b', text)),
                             1, marker + ': ' + text)
        self.assertIn('Traceback (most recent call last)', text)
        for secret in ('private-123', 'sample-token-value', 'private-pass'):
            self.assertNotIn(secret, text)

    def test_rotation_has_bounded_backups(self):
        logs = self.run_script('''
            import sys, logging
            from pathlib import Path
            from app import paths
            from app.logging_setup import setup
            paths.LOG_DIR = Path(sys.argv[1])
            paths.LOG_FILE = paths.LOG_DIR / 'app.log'
            logger = setup('fixture', max_bytes=1024, backup_count=2)
            for i in range(80):
                logger.error('rotation %s %s', i, 'x' * 100)
            logging.shutdown()
        ''')
        self.assertEqual(set(logs), {'app.log', 'app.log.1', 'app.log.2'})
        self.assertIn('rotation 79', logs['app.log'])

    def test_common_secret_formats_are_redacted(self):
        for text in ('{"api_key": "private123"}', 'https://host?access_token=private123&ok=1',
                     'GITEE_TOKEN=private123', "{'server_password': 'private123'}",
                     'Authorization: Basic private123', 'sk-private123456789',
                     '"password": "two words private123"', 'https://private123:password@host',
                     '?TrustedClientToken=private123&query=ok'):
            self.assertNotIn('private123', _redact(text))

