"""入口整理不能提前加载低频 SDK，或改变 Qt / COM 初始化边界。"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class StartupImportTests(unittest.TestCase):
    def imported_modules(self, target):
        code = (
            'import importlib,json,sys; '
            f'importlib.import_module({target!r}); '
            'print("MODULES="+json.dumps(sorted(sys.modules)))'
        )
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=Path(__file__).resolve().parent.parent,
                                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                                     'PYTHONIOENCODING': 'utf-8'},
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        line = next(line for line in result.stdout.splitlines() if line.startswith('MODULES='))
        return set(json.loads(line.removeprefix('MODULES=')))

    def test_main_keeps_network_document_video_and_com_dependencies_deferred(self):
        modules = self.imported_modules('main')
        for name in ('requests', 'docx', 'pptx', 'dashscope', 'edge_tts', 'velopack',
                     'pythoncom', 'win32com.client', 'comtypes',
                     'app.video.powerpoint', 'app.video.obs'):
            self.assertNotIn(name, modules)
        self.assertIn('ui.pages.reviewer_page', modules)
        self.assertIn('ui.pages.settings_page', modules)
        self.assertIn('ui.pages.tools_page', modules)

    def test_video_window_import_does_not_initialize_com_or_online_engines(self):
        modules = self.imported_modules('ui.dialogs.video_recording_dialog')
        self.assertIn('app.video.obs', modules)
        self.assertIn('app.video.powerpoint', modules)
        for name in ('pythoncom', 'win32com.client', 'comtypes', 'dashscope', 'edge_tts'):
            self.assertNotIn(name, modules)
