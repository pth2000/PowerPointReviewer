"""构建 PyInstaller 产物，并用 Velopack 生成安装器、便携包与更新包。

    python build.py
    python build.py --notes notes.md

版本号取自 main.py。releases 目录中保留的上一版完整包会被用来生成增量包。
发布到 Gitee 与 GitHub 请使用 release.py。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACK_ID = 'PowerPointReviewer'
MAIN_EXE = 'PowerPointReviewer.exe'
DIST_DIR = ROOT / 'dist' / PACK_ID
RELEASES_DIR = ROOT / 'releases'
RUNTIME_DIRS = ('config', 'data', 'temp')
FEED_NAME = 'releases.win.json'
SETUP_NAME = f'{PACK_ID}-win-Setup.exe'
PORTABLE_NAME = f'{PACK_ID}-win-Portable.zip'


def read_version() -> str:
    """读取 main.py 中的版本号。"""
    text = (ROOT / 'main.py').read_text(encoding='utf-8-sig')
    match = re.search(r"^VERSION = '([^']+)'", text, re.MULTILINE)
    if not match:
        sys.exit('无法从 main.py 读取 VERSION')
    return match.group(1)


def find_vpk() -> str:
    """定位 vpk 可执行文件。"""
    found = shutil.which('vpk')
    if found:
        return found
    fallback = Path.home() / '.dotnet' / 'tools' / 'vpk.exe'
    if fallback.is_file():
        return str(fallback)
    sys.exit('未找到 vpk。请安装 .NET 10 SDK 后执行：dotnet tool install -g vpk')


def dotnet_env() -> dict:
    """返回运行 vpk 所需的环境变量。"""
    env = dict(os.environ)
    env.setdefault('DOTNET_ROOT', str(Path.home() / '.dotnet'))
    env.setdefault('DOTNET_CLI_TELEMETRY_OPTOUT', '1')
    return env


def run_pyinstaller():
    """构建 PyInstaller 产物。"""
    subprocess.run([sys.executable, '-m', 'PyInstaller', 'PowerPointReviewer.spec', '--noconfirm'],
                   cwd=ROOT, check=True)


def check_runtime_dirs():
    """确认产物目录中没有本地运行期数据。"""
    leftovers = [name for name in RUNTIME_DIRS if (DIST_DIR / name).exists()]
    if leftovers:
        sys.exit(f'{DIST_DIR} 中存在运行期目录 {leftovers}，删除后再打包，避免本地配置进入安装包')


def pack(version: str, notes: Path = None):
    """用 Velopack 打包当前产物。"""
    command = [
        find_vpk(), 'pack',
        '--packId', PACK_ID,
        '--packVersion', version,
        '--packTitle', PACK_ID,
        '--packDir', str(DIST_DIR),
        '--mainExe', MAIN_EXE,
        '--icon', str(ROOT / 'image' / 'ppt_ico.ico'),
        '--outputDir', str(RELEASES_DIR),
    ]
    if notes:
        command += ['--releaseNotes', str(Path(notes).resolve())]
    subprocess.run(command, cwd=ROOT, check=True, env=dotnet_env())


def build(version: str, notes: Path = None):
    """执行完整的构建与打包。"""
    run_pyinstaller()
    check_runtime_dirs()
    pack(version, notes)


def main():
    parser = argparse.ArgumentParser(description='构建并打包 PowerPointReviewer')
    parser.add_argument('--notes', type=Path, help='更新说明（Markdown 文件）')
    args = parser.parse_args()

    version = read_version()
    build(version, args.notes)
    print(f'\n已生成 {version}：{RELEASES_DIR}')


if __name__ == '__main__':
    main()
