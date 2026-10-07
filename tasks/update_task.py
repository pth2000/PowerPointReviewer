"""在后台检查并下载新版本。"""

from PySide6.QtCore import QThread, Signal

from app import updater


class UpdateCheckTask(QThread):
    """按优先级依次查询各发布源上的最新版本。"""

    # 状态为 unmanaged、latest、available 或 error，附带更新信息或错误详情
    checked = Signal(str, object)

    def run(self):
        errors = []
        for index in range(updater.source_count()):
            manager = updater.create_manager(index)
            if manager is None:
                self.checked.emit('unmanaged', None)
                return
            try:
                info = manager.check_for_updates()
            except Exception as e:
                errors.append(f'{updater.source_name(index)}：{e}')
                continue
            updater.use_source(index)
            self.checked.emit('available' if info else 'latest', info)
            return
        self.checked.emit('error', '；'.join(errors) or '未知错误')


class UpdateDownloadTask(QThread):
    """下载更新包并报告进度。"""

    progress = Signal(int)
    downloaded = Signal(object, str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        try:
            updater.create_manager().download_updates(
                self.info, lambda value: self.progress.emit(int(value)))
        except Exception as e:
            self.downloaded.emit(self.info, str(e))
            return
        self.downloaded.emit(self.info, '')
