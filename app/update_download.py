"""从镜像发行版下载更新包，由 Velopack 还原、清理和安装更新。"""

import hashlib
import logging
from pathlib import Path
import tempfile
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)
MAX_DELTAS = 10


class MirroredUpdateManager:
    """按包在发布镜像间回退，保留 Velopack 的检查和安装行为。"""

    def __init__(self, source, download_roots, locator=None):
        import velopack

        self.download_roots = [root.rstrip('/') for root in download_roots]
        if not self.download_roots:
            raise ValueError('暂时无法获取更新包的下载地址，请稍后重试。')
        self.locator = locator
        self.manager = velopack.UpdateManager(source, locator=locator)

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def check_for_updates(self):
        info = self.manager.check_for_updates()
        if info and (len(info.DeltasToTarget) > MAX_DELTAS or
                     sum(asset.Size for asset in info.DeltasToTarget) >= info.TargetFullRelease.Size):
            info.BaseRelease = None
            info.DeltasToTarget = []
        return info

    def download_updates(self, info, progress_callback=None):
        import velopack

        last_progress = -1

        def progress(value):
            nonlocal last_progress
            value = max(last_progress, min(100, int(value)))
            if progress_callback and value != last_progress:
                progress_callback(value)
            last_progress = value

        pending = self.manager.get_update_pending_restart()
        if pending and pending.FileName == info.TargetFullRelease.FileName:
            self.manager.download_updates(info, progress)
            progress(100)
            return

        deltas = list(info.DeltasToTarget or [])
        total = sum(asset.Size for asset in deltas)
        # Python 绑定不提供自定义 UpdateSource；从镜像取得经过校验的包后，
        # 交给 Velopack 本地源处理补丁、包锁、Update.exe 与旧包清理。
        with tempfile.TemporaryDirectory(prefix='pptreviewer-update-') as folder:
            local = velopack.UpdateManager(folder, locator=self.locator)
            if info.BaseRelease is not None and 0 < len(deltas) <= MAX_DELTAS and total < info.TargetFullRelease.Size:
                try:
                    downloaded = 0
                    for asset in deltas:
                        self._download_asset(asset, Path(folder),
                                             lambda size: progress(70 * (downloaded + size) / max(1, total)))
                        downloaded += asset.Size
                    local.download_updates(info, lambda value: progress(70 + 0.3 * value))
                    progress(100)
                    return
                except Exception as error:
                    logger.warning('增量更新失败，改用完整包：%s', error)

            target = info.TargetFullRelease
            self._download_asset(target, Path(folder), lambda size: progress(90 * size / max(1, target.Size)))
            full = velopack.UpdateInfo(target, [], info.IsDowngrade)
            local.download_updates(full, lambda value: progress(90 + 0.1 * value))
        progress(100)

    def _download_asset(self, asset, folder: Path, progress):
        kind = asset.Type.lower()
        if kind not in ('full', 'delta'):
            raise ValueError('更新包类型不正确')
        expected = f'{asset.PackageId}-{asset.Version}-{kind}.nupkg'
        if asset.FileName != expected or any(char in asset.FileName for char in '/\\:'):
            raise ValueError('更新包文件名不正确')
        path = folder / asset.FileName
        for root in self.download_roots:
            url = f'{root}/v{quote(asset.Version, safe="")}/{quote(asset.FileName, safe="")}'
            try:
                self._download_url(url, path, asset, progress)
                return
            except (requests.RequestException, ValueError) as error:
                path.unlink(missing_ok=True)
                logger.warning('更新包下载或校验失败（%s）：%s', url, error)
        raise RuntimeError('暂时无法取得有效更新包，请稍后重试。')

    @staticmethod
    def _download_url(url, path, asset, progress):
        sha1, sha256 = hashlib.sha1(), hashlib.sha256()
        size = 0
        with requests.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with path.open('wb') as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > asset.Size:
                        raise ValueError('更新包下载不完整，请重新下载。')
                    handle.write(chunk)
                    sha1.update(chunk)
                    sha256.update(chunk)
                    progress(size)
        if size != asset.Size:
            raise ValueError('更新包下载不完整，请重新下载。')
        if not asset.SHA1 or sha1.hexdigest().lower() != asset.SHA1.lower():
            raise ValueError('更新包校验未通过，请重新下载。')
        if asset.SHA256 and sha256.hexdigest().lower() != asset.SHA256.lower():
            raise ValueError('更新包校验未通过，请重新下载。')
