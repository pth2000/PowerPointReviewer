"""通过 Velopack 检查、下载并安装新版本。"""

import os

from app import paths

# Velopack 没有 Gitee 适配器，改用通用 HTTP 源读取最新发行版的附件
GITEE_FEED = 'https://gitee.com/pth2000/PowerPointReviewer/releases/download/latest'
GITHUB_REPO = 'https://github.com/pth2000/PowerPointReviewer'
SOURCE_NAMES = ('Gitee', 'GitHub')
# 设置后改用该本地目录或静态地址作为发布源，用于在正式发布前验证更新流程
FEED_ENV = 'POWERPOINTREVIEWER_UPDATE_FEED'

_source_index = 0


def run_startup_hooks() -> str:
    """处理 Velopack 的安装、更新与卸载钩子，返回刚更新到的版本号。"""
    if not paths.IS_VELOPACK_INSTALL:
        return ''
    import velopack

    restarted = []
    velopack.App().on_restarted(lambda version: restarted.append(str(version))).run()
    return restarted[0] if restarted else ''


def custom_feed() -> str:
    """返回环境变量指定的发布源。"""
    return os.environ.get(FEED_ENV, '').strip()


def source_count() -> int:
    """返回可用发布源的数量。"""
    return 1 if custom_feed() else len(SOURCE_NAMES)


def source_name(index: int) -> str:
    """返回发布源的显示名称。"""
    if custom_feed():
        return '本地发布源'
    return SOURCE_NAMES[index]


def use_source(index: int):
    """记住本次检查成功的发布源，供后续下载与安装沿用。"""
    global _source_index
    _source_index = index


def _source(index: int):
    """构造指定发布源。"""
    import velopack

    feed = custom_feed()
    if feed:
        return feed
    if index == 0:
        return velopack.HttpSource(GITEE_FEED)
    return velopack.GithubSource(GITHUB_REPO)


def create_manager(index: int = None):
    """返回更新管理器；源码运行或未经 Velopack 安装时返回 None。"""
    if not paths.IS_VELOPACK_INSTALL:
        return None
    import velopack

    return velopack.UpdateManager(_source(_source_index if index is None else index))


def download_size(info) -> int:
    """返回本次更新实际需要下载的字节数。"""
    deltas = list(info.DeltasToTarget or [])
    if info.BaseRelease is not None and deltas:
        return sum(asset.Size for asset in deltas)
    return info.TargetFullRelease.Size


def release_notes(info, max_lines: int = 15) -> str:
    """把更新说明转为适合对话框显示的纯文本。"""
    lines = []
    for line in (info.TargetFullRelease.NotesMarkdown or '').strip().splitlines():
        stripped = line.strip()
        lines.append(stripped.lstrip('#').strip() if stripped.startswith('#') else line.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ['…']
    return '\n'.join(lines).strip()
