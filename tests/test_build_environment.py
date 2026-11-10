"""发布版本与 uv 项目元数据保持一致。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build


class BuildVersionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'main.py').write_text("VERSION = '1.8.2'\n", encoding='utf-8')
        self.project = self.root / 'pyproject.toml'
        self.project.write_text('[project]\nversion = "1.8.2"\n', encoding='utf-8')

    def test_matching_runtime_and_project_versions_are_accepted(self):
        with patch.object(build, 'ROOT', self.root):
            self.assertEqual(build.read_version(), '1.8.2')

    def test_mismatched_version_stops_release_before_building(self):
        self.project.write_text('[project]\nversion = "1.8.1"\n', encoding='utf-8')
        with patch.object(build, 'ROOT', self.root):
            with self.assertRaisesRegex(SystemExit, '版本号不一致'):
                build.read_version()
