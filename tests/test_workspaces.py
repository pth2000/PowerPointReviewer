"""异常退出、并发实例、文件占用与目录边界下的临时工作区清理。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workspaces import TemporaryWorkspace, cleanup_stale_workspaces


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def link_directory(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except OSError:
            if sys.platform != 'win32':
                self.skipTest('系统不允许创建目录符号链接')
        # Windows 普通账户可以创建目录联接，用它验证相同的目录边界。
        result = subprocess.run(
            f'cmd.exe /d /c mklink /J "{link}" "{target}"',
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            self.skipTest('系统不允许创建目录联接')

    def child(self, *, crash):
        finish = 'os._exit(0)' if crash else 'sys.stdin.readline(); workspace.cleanup()'
        code = (
            'import os,sys; from pathlib import Path; '
            'from app.workspaces import TemporaryWorkspace; '
            f'workspace=TemporaryWorkspace(prefix="slide-",dir={str(self.root)!r}); '
            'Path(workspace.name,"payload.bin").write_bytes(b"preview"); '
            'print(workspace.name,flush=True); ' + finish
        )
        child = subprocess.Popen(
            [sys.executable, '-B', '-c', code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )

        def stop():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

        self.addCleanup(stop)
        path = Path(child.stdout.readline().strip())
        self.assertEqual(path.parent, self.root)
        return child, path

    def test_normal_exit_removes_entire_workspace(self):
        with TemporaryWorkspace(prefix='slide-', dir=self.root) as folder:
            path = Path(folder)
            (path / 'image.png').write_bytes(b'png')
            self.assertEqual(cleanup_stale_workspaces([self.root]), 0)
        self.assertFalse(path.exists())

    def test_killed_process_residue_is_removed_at_next_cleanup(self):
        child, directory = self.child(crash=True)
        _out, error = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, error)
        self.assertTrue(directory.exists())
        self.assertEqual(cleanup_stale_workspaces([self.root]), 1)
        self.assertFalse(directory.exists())

    def test_other_running_instance_is_never_deleted(self):
        child, directory = self.child(crash=False)
        self.assertEqual(cleanup_stale_workspaces([self.root]), 0)
        self.assertEqual((directory / 'payload.bin').read_bytes(), b'preview')
        child.communicate('\n', timeout=10)
        self.assertEqual(child.returncode, 0)
        self.assertFalse(directory.exists())

    def test_failed_cleanup_preserves_marker_and_is_retried(self):
        workspace = TemporaryWorkspace(prefix='audio-review-', dir=self.root)
        self.addCleanup(workspace.cleanup)
        directory = Path(workspace.name)
        payload = directory / 'locked.wav'
        payload.write_bytes(b'audio')
        unlink = Path.unlink

        def locked(path, *args, **kwargs):
            if path == payload:
                raise PermissionError('in use')
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', locked):
            with self.assertRaises(PermissionError):
                workspace.cleanup()
        self.assertTrue((directory / '.pptreviewer-workspace.json').is_file())
        self.assertEqual(cleanup_stale_workspaces([self.root]), 1)
        self.assertFalse(directory.exists())

    def test_unknown_and_corrupt_directories_and_user_files_are_preserved(self):
        for name in ('slide-old-legacy', 'PPT-video-recovery-keep', 'sessions', 'audio_chunks'):
            directory = self.root / name
            directory.mkdir()
            (directory / 'valuable.bin').write_bytes(b'keep')
        corrupt = self.root / 'slide-invalid-0123456789abcdef'
        corrupt.mkdir()
        (corrupt / '.pptreviewer-workspace.json').write_text('{broken', encoding='utf-8')
        forged = self.root / 'slide-forged-0123456789abcdef'
        forged.mkdir()
        (forged / '.pptreviewer-workspace.json').write_text(json.dumps({
            'version': 1, 'prefix': 'slide-', 'token': 'fedcba9876543210',
        }), encoding='utf-8')
        self.assertEqual(cleanup_stale_workspaces([self.root]), 0)
        self.assertEqual(len(list(self.root.iterdir())), 6)

    def test_workspace_link_cannot_redirect_cleanup_outside_root(self):
        outside = self.root / 'outside'
        scan = self.root / 'scan'
        outside.mkdir()
        scan.mkdir()
        (outside / 'keep.txt').write_text('keep', encoding='utf-8')
        linked = scan / 'slide-link-0123456789abcdef'
        self.link_directory(linked, outside)
        self.assertEqual(cleanup_stale_workspaces([scan]), 0)
        self.assertTrue((outside / 'keep.txt').exists())

    def test_cleanup_does_not_follow_links_inside_workspace(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('keep', encoding='utf-8')
        with TemporaryWorkspace(prefix='slide-', dir=self.root) as folder:
            self.link_directory(Path(folder) / 'external', outside)
        self.assertTrue((outside / 'keep.txt').exists())

    def test_context_cleanup_failure_does_not_hide_original_error(self):
        workspace = TemporaryWorkspace(prefix='slide-', dir=self.root)
        self.addCleanup(workspace.cleanup)
        with patch.object(workspace, 'cleanup', side_effect=PermissionError('busy')), \
             patch('app.workspaces.get_logger'):
            with self.assertRaisesRegex(ValueError, 'original failure'):
                with workspace:
                    raise ValueError('original failure')

    def test_marker_write_failure_cleans_partial_creation_and_keeps_original_error(self):
        write = Path.write_text

        def fail(path, *args, **kwargs):
            write(path, '{partial', encoding='utf-8')
            raise PermissionError('marker write failed')

        with patch.object(Path, 'write_text', fail):
            with self.assertRaisesRegex(PermissionError, 'marker write failed'):
                TemporaryWorkspace(prefix='slide-', dir=self.root)
        self.assertFalse(list(self.root.iterdir()))
