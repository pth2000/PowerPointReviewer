"""检查更新线程模块"""

import re
import requests
from PySide6.QtCore import QThread, Signal


class UpdateTask(QThread):
    """检查更新线程"""

    signal_finish = Signal(list)

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = version

    def run(self):
        """线程入口"""
        self.get_update()

    def get_update(self):
        """HTTP 获取更新信息：Gitee 主源，GitHub 备选"""
        latest = None
        errors = []

        try:
            latest = self._fetch_latest_from_gitee()
        except Exception as e:
            print(e)
            errors.append(f'Gitee: {e}')

        if latest is None:
            try:
                latest = self._fetch_latest_from_github()
            except Exception as e:
                print(e)
                errors.append(f'GitHub: {e}')

        if latest is None:
            error_msg = '；'.join(errors) if errors else '未知错误'
            self.signal_finish.emit([2, '获取更新失败', f'详情：{error_msg}'])
            return False

        latest_version = latest['version']
        latest_version_name = latest['name']
        latest_version_time = latest['time']
        latest_version_download_url = latest['download_url']
        latest_source = latest['source']

        if self.compare_versions(self.version, latest_version):
            info = (
                f'当前版本为最新版\n'
                f'服务器版本：{latest_version}\n'
                f'更新时间：{latest_version_time}\n'
                f'来源：{latest_source}'
            )
            self.signal_finish.emit([0, '获取更新成功', info])
        else:
            info = (
                f"发现新版本！{self.version} --> {latest_version}\n"
                f"更新内容：{latest_version_name}\n"
                f"更新时间：{latest_version_time}\n"
                f"来源：{latest_source}"
            )
            self.signal_finish.emit([1, '获取更新成功', info, latest_version_download_url])

        return True

    def _fetch_latest_from_gitee(self) -> dict:
        """从 Gitee 获取最新 release"""
        url = 'https://gitee.com/api/v5/repos/pth2000/PowerPointReviewer/releases/latest'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        assets = data.get('assets') or []
        download_url = ''
        if assets and isinstance(assets[0], dict):
            download_url = str(assets[0].get('browser_download_url', '')).strip()
        if not download_url:
            download_url = str(data.get('html_url', '')).strip()

        return {
            'source': 'Gitee',
            'version': self.normalize_version(str(data.get('tag_name', ''))),
            'name': str(data.get('name', '')).strip() or '无更新说明',
            'time': str(data.get('created_at', '')).strip() or '-',
            'download_url': download_url,
        }

    def _fetch_latest_from_github(self) -> dict:
        """从 GitHub 获取最新 release"""
        url = 'https://api.github.com/repos/pth2000/PowerPointReviewer/releases/latest'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        assets = data.get('assets') or []
        download_url = ''
        if assets and isinstance(assets[0], dict):
            download_url = str(assets[0].get('browser_download_url', '')).strip()
        if not download_url:
            download_url = str(data.get('html_url', '')).strip()

        return {
            'source': 'GitHub',
            'version': self.normalize_version(str(data.get('tag_name', ''))),
            'name': str(data.get('name', '')).strip() or '无更新说明',
            'time': str(data.get('published_at', '')).strip() or str(data.get('created_at', '')).strip() or '-',
            'download_url': download_url,
        }

    @staticmethod
    def normalize_version(version: str) -> str:
        """将 tag 归一化为 x.y.z 形式可比对版本串"""
        text = version.strip()
        text = text.lstrip('vV')
        match = re.search(r'\d+(?:\.\d+)*', text)
        if match:
            return match.group(0)
        return '0.0.0'

    @staticmethod
    def compare_versions(version1, version2):
        """版本号比较，返回 version1 是否大于等于 version2"""
        parts1 = [int(p) for p in str(version1).split('.') if p.isdigit()]
        parts2 = [int(p) for p in str(version2).split('.') if p.isdigit()]

        if not parts1:
            parts1 = [0]
        if not parts2:
            parts2 = [0]

        min_length = min(len(parts1), len(parts2))

        for i in range(min_length):
            if parts1[i] < parts2[i]:
                return False
            if parts1[i] > parts2[i]:
                return True

        if len(parts1) < len(parts2):
            return False
        if len(parts1) > len(parts2):
            return True

        return True
