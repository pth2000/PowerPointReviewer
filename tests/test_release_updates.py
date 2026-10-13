"""Gitee 历史附件、保留策略及真实跨版本更新验证。"""

from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from app.update_download import MirroredUpdateManager
import release


class ReleasePayloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = patch.object(release, 'RELEASES_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assets = []
        for name in (release.SETUP_NAME, release.PORTABLE_NAME):
            (self.root / name).write_bytes(b'installer')
        for version in ('1.6.0', '1.6.1', '1.6.2', '1.6.3'):
            self.package(version, 'Full')
            if version != '1.6.0':
                self.package(version, 'Delta')
        (self.root / release.FEED_NAME).write_text(json.dumps({'Assets': self.assets}), encoding='utf-8')

    def package(self, version, kind):
        data = f'{version}-{kind}'.encode()
        name = f'{release.PACK_ID}-{version}-{kind.lower()}.nupkg'
        (self.root / name).write_bytes(data)
        self.assets.append(dict(PackageId=release.PACK_ID, Version=version, Type=kind,
                                FileName=name, Size=len(data), SHA1=hashlib.sha1(data).hexdigest(),
                                SHA256=hashlib.sha256(data).hexdigest()))

    def test_publish_current_files_with_history_metadata(self):
        original = (self.root / release.FEED_NAME).read_bytes()
        files, data = release.gitee_payload('1.6.3')
        self.assertEqual([p.name for p in files], [release.SETUP_NAME, release.PORTABLE_NAME,
                         f'{release.PACK_ID}-1.6.3-full.nupkg', f'{release.PACK_ID}-1.6.3-delta.nupkg'])
        self.assertEqual(data, original)
        self.assertIn('1.6.2', [x['Version'] for x in json.loads(data)['Assets']])

    def test_historical_files_need_not_exist_locally(self):
        (self.root / f'{release.PACK_ID}-1.6.2-delta.nupkg').unlink()
        _, data = release.gitee_payload('1.6.3')
        self.assertEqual(len(json.loads(data)['Assets']), len(self.assets))

    def test_modified_current_delta_fails_before_creating_release(self):
        (self.root / f'{release.PACK_ID}-1.6.3-delta.nupkg').write_bytes(b'x' * len(b'1.6.3-Delta'))
        with patch.object(release, 'gitee_create_release') as create:
            with self.assertRaisesRegex(RuntimeError, 'SHA1'):
                release.publish_gitee('1.6.3', 'v1.6.3', 'notes', 'master', False)
            create.assert_not_called()

    def test_new_version_preview_before_build(self):
        files, feed = release.gitee_payload('1.6.4', required=False)
        self.assertEqual([p.name for p in files], [release.SETUP_NAME, release.PORTABLE_NAME])
        self.assertEqual(feed, b'')

    def test_feed_uploaded_last_and_local_feed_kept_intact(self):
        original = (self.root / release.FEED_NAME).read_bytes()
        uploads = []
        def upload(release_id, path, token):
            uploads.append((path.name, path.read_bytes()))
        with patch.object(release, 'gitee_create_release', return_value={'id': 123}), \
             patch.object(release, 'gitee_upload', side_effect=upload), \
             patch.object(release, 'gitee_attachments', side_effect=lambda *args: [{'name': name} for name, _ in uploads]):
            release.publish_gitee('1.6.3', 'v1.6.3', 'notes', 'master', False)
        self.assertEqual(len(uploads), 5)
        self.assertEqual(uploads[-1], (release.FEED_NAME, original))
        self.assertEqual((self.root / release.FEED_NAME).read_bytes(), original)

    def test_retention_for_latest_baseline_middle_and_classic(self):
        cases = [
            (0, 'v1.6.3', release.PORTABLE_NAME, True),
            (0, 'v1.6.3', release.FEED_NAME, True),
            (1, 'v1.6.2', f'{release.PACK_ID}-1.6.2-delta.nupkg', True),
            (1, 'v1.6.2', release.PORTABLE_NAME, False),
            (1, 'v1.6.2', release.SETUP_NAME, False),
            (1, 'v1.6.2', f'{release.PACK_ID}-1.6.1-delta.nupkg', False),
            (2, 'v1.7.0', release.SETUP_NAME, False),
            (3, 'v1.6.0', release.SETUP_NAME, True),
            (3, 'v1.6.0', release.PORTABLE_NAME, True),
            (3, 'v1.6.0', f'{release.PACK_ID}-1.6.0-full.nupkg', False),
            (3, 'v1.6.0', release.FEED_NAME, False),
            (4, 'v1.5.1', 'PowerPointReviewer.zip', True),
        ]
        for index, tag, name, expected in cases:
            with self.subTest(tag=tag, name=name):
                self.assertEqual(release.keep_attachment(index, tag, name), expected)

    def test_incomplete_upload_does_not_publish_feed(self):
        with patch.object(release, 'gitee_create_release', return_value={'id': 123}), \
             patch.object(release, 'gitee_upload') as upload, \
             patch.object(release, 'gitee_attachments', return_value=[]):
            with self.assertRaisesRegex(SystemExit, '尚未发布更新索引'):
                release.publish_gitee('1.6.3', 'v1.6.3', 'notes', 'master', False)
        self.assertNotIn(release.FEED_NAME, [call.args[1].name for call in upload.call_args_list])


class MirroredManagerTests(unittest.TestCase):
    def test_too_many_or_large_deltas_choose_full_and_report_full_size(self):
        from app import updater
        import velopack
        for sizes in ([1] * 11, [60, 60]):
            with self.subTest(sizes=sizes):
                info = SimpleNamespace(TargetFullRelease=SimpleNamespace(Size=100),
                                       BaseRelease=object(), DeltasToTarget=[SimpleNamespace(Size=x) for x in sizes])
                with patch.object(velopack, 'UpdateManager') as native:
                    native.return_value.check_for_updates.return_value = info
                    manager = MirroredUpdateManager('https://example.com/latest', ['https://example.com/download'])
                    result = manager.check_for_updates()
                self.assertIsNone(result.BaseRelease)
                self.assertEqual(updater.download_size(result), 100)

    def test_both_sources_use_mirrors_and_custom_feed_stays_local(self):
        from app import updater
        import velopack
        with patch.object(updater.paths, 'IS_VELOPACK_INSTALL', True), \
             patch.object(updater, 'custom_feed', return_value=''), \
             patch('app.update_download.MirroredUpdateManager') as mirrors:
            self.assertIs(updater.create_manager(0), mirrors.return_value)
            self.assertIs(updater.create_manager(1), mirrors.return_value)
            roots = [updater.GITEE_FEED.rsplit('/', 1)[0], f'{updater.GITHUB_REPO}/releases/download']
            self.assertEqual(mirrors.call_args_list[0].args[1], roots)
            self.assertEqual(mirrors.call_args_list[1].args[1], roots[::-1])
        with patch.object(updater.paths, 'IS_VELOPACK_INSTALL', True), \
             patch.object(updater, 'custom_feed', return_value='C:/local-feed'), \
             patch('app.update_download.MirroredUpdateManager') as mirrors, \
             patch.object(velopack, 'UpdateManager') as native:
            self.assertIs(updater.create_manager(0), native.return_value)
            mirrors.assert_not_called()


@contextmanager
def update_server(feed, files, corrupt=None, missing=None):
    """各版本分别存放各自的包，latest 只提供最新索引及最新版本附件。"""
    paths = {f'v{asset["Version"]}/{asset["FileName"]}': release.RELEASES_DIR / asset['FileName']
             for asset in json.loads(feed)['Assets']}
    for path in files:
        paths[f'latest/{path.name}'] = path
    requests = []
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split('?', 1)[0].lstrip('/')
            requests.append(name)
            if name == f'latest/{release.FEED_NAME}':
                data = feed
            elif name == missing:
                self.send_error(404)
                return
            elif name == corrupt:
                data = bytearray(paths[name].read_bytes())
                data[0] ^= 1  # 大小不变，验证运行时校验值检查。
            elif name in paths and paths[name].is_file():
                self.directory = str(paths[name].parent)
                self.path = '/' + paths[name].name
                return super().do_GET()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@unittest.skipUnless(sys.platform == 'win32' and all(
    (release.RELEASES_DIR / f'{release.PACK_ID}-{version}-{kind}.nupkg').is_file()
    for version, kind in [('1.6.1', 'full'), ('1.6.2', 'delta'), ('1.6.3', 'delta'), ('1.6.3', 'full')]),
    '真实更新验证需要 Windows 和本地 1.6.1—1.6.3 发布包')
class VelopackHistoryTests(unittest.TestCase):
    def setUp(self):
        import velopack
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.packages = self.root / 'packages'
        self.packages.mkdir()
        base = release.RELEASES_DIR / f'{release.PACK_ID}-1.6.1-full.nupkg'
        shutil.copy2(base, self.packages / base.name)
        with zipfile.ZipFile(base) as archive:
            manifest = next(name for name in archive.namelist() if name.endswith('.nuspec'))
            updater = next(name for name in archive.namelist() if name.endswith('Squirrel.exe'))
            (self.root / 'sq.version').write_bytes(archive.read(manifest))
            (self.root / 'Update.exe').write_bytes(archive.read(updater))
        self.locator = velopack.VelopackLocatorConfig(
            RootAppDir=self.root, UpdateExePath=self.root / 'Update.exe', PackagesDir=self.packages,
            ManifestPath=self.root / 'sq.version', CurrentBinaryDir=self.root / 'current', IsPortable=False)
        self.files, self.feed = release.gitee_payload('1.6.3')
        factory = tempfile.TemporaryDirectory
        patcher = patch('app.update_download.tempfile.TemporaryDirectory',
                        side_effect=lambda **kwargs: factory(dir=self.root, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def manager(self, url, mirror=None):
        import velopack
        return MirroredUpdateManager(velopack.HttpSource(f'{url}/latest'),
                                     [url] + ([mirror] if mirror else []), locator=self.locator)

    def assert_clean_packages(self, info):
        self.assertEqual([p.name for p in self.packages.glob('*.nupkg')], [info.TargetFullRelease.FileName])
        self.assertEqual(list(self.packages.glob('*.partial')), [])
        self.assertEqual(list(self.root.glob('pptreviewer-update-*')), [])

    def test_two_historical_deltas_reconstruct_latest_and_clean_old_packages(self):
        with update_server(self.feed, self.files) as (url, requests):
            manager = self.manager(url)
            info = manager.check_for_updates()
            self.assertEqual([x.Version for x in info.DeltasToTarget], ['1.6.2', '1.6.3'])
            progress = []
            manager.download_updates(info, progress.append)
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(progress[-1], 100)
            downloaded = [name for name in requests if name.endswith('.nupkg')]
            self.assertEqual(downloaded, [f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg',
                                          f'v1.6.3/{release.PACK_ID}-1.6.3-delta.nupkg'])
            before = list(requests)
            manager.download_updates(info)
            self.assertEqual(requests, before)  # 已准备好的包不再次下载。
        self.assert_clean_packages(info)
        result = self.packages / info.TargetFullRelease.FileName
        with zipfile.ZipFile(result) as actual, zipfile.ZipFile(release.RELEASES_DIR / result.name) as expected:
            self.assertEqual(set(actual.namelist()), set(expected.namelist()))
            for name in expected.namelist():
                self.assertEqual(hashlib.sha256(actual.read(name)).digest(),
                                 hashlib.sha256(expected.read(name)).digest(), name)

    def test_corrupt_historical_delta_falls_back_to_full_and_cleans_temporary_files(self):
        route = f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg'
        with update_server(self.feed, self.files, corrupt=route) as (url, requests):
            manager = self.manager(url)
            info = manager.check_for_updates()
            manager.download_updates(info)
            self.assertIn(f'v1.6.3/{info.TargetFullRelease.FileName}', requests)
        self.assert_clean_packages(info)
        release.check_asset(self.packages / info.TargetFullRelease.FileName,
                            next(asset for asset in json.loads(self.feed)['Assets']
                                 if asset['Version'] == '1.6.3' and asset['Type'] == 'Full'))

    def test_missing_historical_delta_falls_back_to_full(self):
        route = f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg'
        with update_server(self.feed, self.files, missing=route) as (url, requests):
            manager = self.manager(url)
            info = manager.check_for_updates()
            manager.download_updates(info)
            self.assertIn(f'v1.6.3/{info.TargetFullRelease.FileName}', requests)
        self.assert_clean_packages(info)

    def test_patch_failure_falls_back_to_full_and_cleans_downloaded_deltas(self):
        base = self.packages / f'{release.PACK_ID}-1.6.1-full.nupkg'
        damaged = self.root / 'damaged.nupkg'
        with zipfile.ZipFile(base) as original, zipfile.ZipFile(damaged, 'w') as output:
            for entry in original.infolist():
                if entry.filename != 'lib/app/PowerPointReviewer.exe':
                    output.writestr(entry, original.read(entry.filename))
        damaged.replace(base)
        with update_server(self.feed, self.files) as (url, requests):
            manager = self.manager(url)
            info = manager.check_for_updates()
            self.assertIsNotNone(info.BaseRelease)
            manager.download_updates(info)
            self.assertIn(f'v1.6.3/{info.TargetFullRelease.FileName}', requests)
        self.assert_clean_packages(info)

    def test_portable_without_base_downloads_only_full(self):
        (self.packages / f'{release.PACK_ID}-1.6.1-full.nupkg').unlink()
        with update_server(self.feed, self.files) as (url, requests):
            manager = self.manager(url)
            info = manager.check_for_updates()
            self.assertIsNone(info.BaseRelease)
            self.assertEqual(info.DeltasToTarget, [])
            manager.download_updates(info)
            self.assertEqual([name for name in requests if name.endswith('.nupkg')],
                             [f'v1.6.3/{info.TargetFullRelease.FileName}'])
        self.assert_clean_packages(info)

    def test_mixed_delta_sources_finish_without_full_download(self):
        older = f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg'
        latest = f'v1.6.3/{release.PACK_ID}-1.6.3-delta.nupkg'
        with update_server(self.feed, self.files, missing=latest) as (backup, backup_requests), \
             update_server(self.feed, self.files, missing=older) as (primary, primary_requests):
            manager = self.manager(primary, backup)
            info = manager.check_for_updates()
            progress = []
            manager.download_updates(info, progress.append)
            self.assertEqual([name for name in primary_requests if name.endswith('.nupkg')], [older, latest])
            self.assertEqual(backup_requests, [older])
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(progress[-1], 100)
        self.assert_clean_packages(info)

    def test_corrupt_primary_delta_uses_backup_delta(self):
        older = f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg'
        with update_server(self.feed, self.files) as (backup, backup_requests), \
             update_server(self.feed, self.files, corrupt=older) as (primary, primary_requests):
            manager = self.manager(primary, backup)
            info = manager.check_for_updates()
            manager.download_updates(info)
            self.assertEqual(backup_requests, [older])
            self.assertFalse(any(name.endswith('-full.nupkg') for name in primary_requests + backup_requests))
        self.assert_clean_packages(info)

    def test_full_package_uses_backup_when_primary_is_missing_or_corrupt(self):
        full = f'v1.6.3/{release.PACK_ID}-1.6.3-full.nupkg'
        for problem in ('missing', 'corrupt'):
            with self.subTest(problem=problem), \
                 update_server(self.feed, self.files) as (backup, backup_requests), \
                 update_server(self.feed, self.files, **{problem: full}) as (primary, primary_requests):
                target_path = self.packages / f'{release.PACK_ID}-1.6.3-full.nupkg'
                target_path.unlink(missing_ok=True)
                manager = self.manager(primary, backup)
                info = manager.check_for_updates()
                info.BaseRelease = None
                info.DeltasToTarget = []
                manager.download_updates(info)
                self.assertEqual(backup_requests, [full])
                self.assertIn(full, primary_requests)
                release.check_asset(target_path, next(asset for asset in json.loads(self.feed)['Assets']
                                    if asset['Version'] == '1.6.3' and asset['Type'] == 'Full'))
                self.assert_clean_packages(info)

    def test_both_delta_mirrors_fail_then_full_uses_backup(self):
        older = f'v1.6.2/{release.PACK_ID}-1.6.2-delta.nupkg'
        full = f'v1.6.3/{release.PACK_ID}-1.6.3-full.nupkg'
        with update_server(self.feed, self.files, missing=older) as (backup, backup_requests), \
             update_server(self.feed, self.files, missing=older, corrupt=full) as (primary, primary_requests):
            manager = self.manager(primary, backup)
            info = manager.check_for_updates()
            manager.download_updates(info)
            self.assertEqual(backup_requests, [older, full])
            self.assertIn(full, primary_requests)
        self.assert_clean_packages(info)

    def test_all_full_mirrors_fail_preserves_base_and_cleans_staging(self):
        full = f'v1.6.3/{release.PACK_ID}-1.6.3-full.nupkg'
        with update_server(self.feed, self.files, missing=full) as (backup, backup_requests), \
             update_server(self.feed, self.files, missing=full) as (primary, primary_requests):
            manager = self.manager(primary, backup)
            info = manager.check_for_updates()
            info.BaseRelease = None
            info.DeltasToTarget = []
            with self.assertRaisesRegex(RuntimeError, '无法取得有效更新包'):
                manager.download_updates(info)
            self.assertEqual(backup_requests, [full])
            self.assertIn(full, primary_requests)
        self.assertTrue((self.packages / f'{release.PACK_ID}-1.6.1-full.nupkg').is_file())
        self.assertEqual(list(self.root.glob('pptreviewer-update-*')), [])
        self.assertEqual(list(self.packages.glob('*.partial')), [])


if __name__ == '__main__':
    unittest.main()
