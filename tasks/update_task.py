"""检查更新线程模块"""

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
        """HTTP 从 Gitee 获取更新信息"""
        try:
            response = requests.get("https://gitee.com/api/v5/repos/pth2000/PowerPointReviewer/releases/latest")
        except Exception as e:
            print(e)
            self.signal_finish.emit([2, '获取更新失败', f'详情：{e}'])
            return False

        try:
            res = response.json()
            latest_version = res['tag_name'][1:]
            latest_version_name = res['name']
            latest_version_time = res['created_at']
            latest_version_download_url = res['assets'][0]['browser_download_url']

            if self.compare_versions(self.version, latest_version):
                info = f'当前版本为最新版\n服务器版本：{latest_version}\n更新时间：{latest_version_time}'
                self.signal_finish.emit([0, '获取更新成功', info])
            else:
                info = (
                    f"发现新版本！{self.version} --> {latest_version}\n"
                    f"更新内容：{latest_version_name}\n更新时间：{latest_version_time}"
                )
                self.signal_finish.emit([1, '获取更新成功', info, latest_version_download_url])
        except Exception as e:
            print(e)
            self.signal_finish.emit([2, 'HTTP 解析失败', f'详情：{e}'])
            return False

    @staticmethod
    def compare_versions(version1, version2):
        """版本号比较，返回 version1 是否大于等于 version2"""
        parts1 = version1.split('.')
        parts2 = version2.split('.')
        min_length = min(len(parts1), len(parts2))

        for i in range(min_length):
            if int(parts1[i]) < int(parts2[i]):
                return False
            if int(parts1[i]) > int(parts2[i]):
                return True

        if len(parts1) < len(parts2):
            return False
        if len(parts1) > len(parts2):
            return True

        return True
