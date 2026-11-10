"""排除可选模块后，程序入口和真实语音 SDK 接口仍应可用。"""

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class PackagingImportTests(unittest.TestCase):
    def test_packaging_exclusions_preserve_application_and_speech_interfaces(self):
        root = Path(__file__).resolve().parent.parent
        tree = ast.parse((root / 'PowerPointReviewer.spec').read_text(encoding='utf-8'))
        analysis = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'Analysis')
        excluded = ast.literal_eval(next(keyword.value for keyword in analysis.keywords
                                         if keyword.arg == 'excludes'))
        code = '''
import json
import sys
import unittest
from pathlib import Path

for name in json.loads(sys.argv[1]):
    sys.modules[name] = None
sys.path.insert(0, str(Path.cwd() / 'tests'))
import main
suite = unittest.defaultTestLoader.loadTestsFromName(
    'test_dashscope_compatibility.DashScopeCompatibilityTests')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', code, json.dumps(excluded)], cwd=root,
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8'},
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
