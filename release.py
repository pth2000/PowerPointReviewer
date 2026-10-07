"""发布新版本：构建产物，并同步发布到 Gitee 与 GitHub。

    python release.py --notes notes.md
    python release.py --notes notes.md --dry-run
    python release.py --prune

发布前需要设置环境变量 GITEE_TOKEN 与 GITHUB_TOKEN，并已提交、打好 v<版本号> 标签并推送。
版本号取自 main.py，Gitee 与 GitHub 使用同一份更新说明。
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import build
from build import FEED_NAME, PACK_ID, PORTABLE_NAME, RELEASES_DIR, ROOT, SETUP_NAME

GITEE_OWNER = 'pth2000'
GITEE_REPO = 'PowerPointReviewer'
GITEE_API = f'https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}'
GITEE_DOWNLOAD = f'https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases/download/latest'
GITHUB_REPO_URL = f'https://github.com/{GITEE_OWNER}/{GITEE_REPO}'

# Gitee 免费仓库的附件配额
FILE_LIMIT = 100 * 1024 * 1024
TOTAL_LIMIT = 1024 * 1024 * 1024
# 客户端只会读取最新发行版的更新包，旧发行版的完整包与索引可以清理
# 经典版本：Velopack 之前发布的绿色包，下载附件始终保留
CLASSIC_UNTIL = 'v1.5.1'
# 里程碑版本卸任后是否继续保留便携包；Gitee 配额紧张时只留安装器
MILESTONE_KEEP_PORTABLE = False


def version_tuple(tag: str) -> tuple:
    """把标签转为可比较的版本号。"""
    parts = [int(x) for x in re.findall(r'\d+', tag or '')[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def is_classic(tag: str) -> bool:
    """判断是否为需要长期保留下载附件的经典版本。"""
    return version_tuple(tag) <= version_tuple(CLASSIC_UNTIL)


def is_milestone(tag: str) -> bool:
    """判断是否为里程碑版本，即修订号为 0 的版本。"""
    return version_tuple(tag)[2] == 0


def github_tag_commit(tag: str) -> str:
    """返回 GitHub 上该标签指向的提交，不存在时返回空串。"""
    import requests

    headers = {}
    token = os.environ.get('GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'token {token}'
    url = f'https://api.github.com/repos/{GITEE_OWNER}/{GITEE_REPO}/commits/{tag}'
    response = requests.get(url, headers=headers, timeout=30)
    return response.json().get('sha', '') if response.status_code == 200 else ''


def human(size: int) -> str:
    """把字节数转为易读的文本。"""
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}'
        value /= 1024


def git(*args) -> str:
    """执行 git 命令并返回标准输出。"""
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} 失败：{result.stderr.strip()}')
    return result.stdout.strip()


def gitee(method: str, path: str, token: str = '', params: dict = None,
          data: dict = None, files: dict = None, timeout: int = 60):
    """调用 Gitee 开放接口。"""
    import requests

    params = dict(params or {})
    data = dict(data or {})
    if token:
        if method in ('GET', 'DELETE'):
            params['access_token'] = token
        else:
            data['access_token'] = token
    response = requests.request(method, GITEE_API + path, params=params,
                                data=data or None, files=files, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f'Gitee {method} {path} 返回 {response.status_code}：{response.text[:200]}')
    return response.json() if response.content else None


def gitee_releases(token: str = '') -> list:
    """按时间倒序返回全部发行版。"""
    return gitee('GET', '/releases', token, params={'per_page': 100, 'direction': 'desc'}) or []


def gitee_attachments(release_id: int, token: str = '') -> list:
    """返回发行版的附件列表。"""
    return gitee('GET', f'/releases/{release_id}/attach_files', token, params={'per_page': 100}) or []


def gitee_create_release(tag: str, name: str, body: str, branch: str, token: str) -> dict:
    """创建发行版。"""
    return gitee('POST', '/releases', token,
                 data={'tag_name': tag, 'name': name, 'body': body, 'target_commitish': branch})


def gitee_upload(release_id: int, path: Path, token: str) -> dict:
    """上传单个附件。"""
    with path.open('rb') as handle:
        return gitee('POST', f'/releases/{release_id}/attach_files', token,
                     files={'file': (path.name, handle)}, timeout=1800)


def gitee_delete_attachment(release_id: int, attachment_id: int, token: str):
    """删除单个附件。"""
    gitee('DELETE', f'/releases/{release_id}/attach_files/{attachment_id}', token)


def artifacts(version: str) -> list:
    """返回需要发布的产物，更新索引排在最后。"""
    names = [SETUP_NAME, PORTABLE_NAME, f'{PACK_ID}-{version}-full.nupkg',
             f'{PACK_ID}-{version}-delta.nupkg', FEED_NAME]
    files = []
    for name in names:
        path = RELEASES_DIR / name
        if path.is_file():
            files.append(path)
        elif 'delta' not in name:
            sys.exit(f'缺少产物：{path}')
    return files


def collect_problems(version: str, tag: str, notes: Path,
                     allow_dirty: bool = False, need_tokens: bool = True) -> list:
    """返回发布前检查发现的问题，空列表表示可以发布。"""
    problems = []
    branch = git('rev-parse', '--abbrev-ref', 'HEAD')
    args = argparse.Namespace(allow_dirty=allow_dirty, dry_run=not need_tokens)

    if not args.allow_dirty and git('status', '--porcelain'):
        problems.append('工作区有未提交的改动，提交后再发布，或使用 --allow-dirty')

    try:
        tagged = git('rev-parse', f'{tag}^{{commit}}')
    except RuntimeError:
        tagged = ''
    head = git('rev-parse', 'HEAD')
    if not tagged:
        problems.append(f'本地缺少标签 {tag}，请执行：git tag {tag}')
    elif tagged != head:
        problems.append(f'标签 {tag} 未指向当前提交')

    if not git('ls-remote', 'origin', f'refs/tags/{tag}'):
        problems.append(f'标签 {tag} 未推送到 origin，请执行：git push origin {branch} --tags')

    try:
        github_commit = github_tag_commit(tag)
    except Exception as e:
        github_commit = ''
        print(f'[检查] GitHub 标签查询失败：{e}')
    if not github_commit:
        problems.append(f'GitHub 上还没有标签 {tag}。等待镜像同步完成，或直接推送到 GitHub')
    elif github_commit != head:
        problems.append(f'GitHub 上的标签 {tag} 指向其它提交，发行版会挂在错误的版本上')

    if notes is None or not Path(notes).is_file():
        problems.append('缺少更新说明文件，使用 --notes 指定')

    if not args.dry_run:
        if not os.environ.get('GITEE_TOKEN'):
            problems.append('未设置环境变量 GITEE_TOKEN')
        if not os.environ.get('GITHUB_TOKEN'):
            problems.append('未设置环境变量 GITHUB_TOKEN')

    try:
        planned = sum(path.stat().st_size for path in artifacts(version))
        usage = gitee_usage(os.environ.get('GITEE_TOKEN', ''))
        if usage + planned > TOTAL_LIMIT:
            problems.append(
                f'Gitee 附件已占用 {human(usage)}，本次还需 {human(planned)}，将超过 {human(TOTAL_LIMIT)} 上限。'
                f'先执行：python release.py --prune')
        elif usage + planned > TOTAL_LIMIT * 0.9:
            print(f'[配额] 发布后 Gitee 附件约占 {human(usage + planned)} / {human(TOTAL_LIMIT)}，'
                  f'建议随后执行：python release.py --prune')
    except SystemExit:
        raise
    except Exception as e:
        print(f'[配额] 预估失败，跳过检查：{e}')

    setup = RELEASES_DIR / SETUP_NAME
    if setup.is_file() and setup.stat().st_size > FILE_LIMIT:
        problems.append(f'{SETUP_NAME} 为 {human(setup.stat().st_size)}，超过 Gitee 单文件上限')

    token = os.environ.get('GITEE_TOKEN', '')
    if token or args.dry_run:
        existing = [r for r in gitee_releases(token) if r.get('tag_name') == tag]
        if existing:
            problems.append(f'Gitee 上已存在 {tag} 的发行版，请先在网页删除')

    return problems


def preflight(version: str, tag: str, notes: Path, args) -> str:
    """检查发布前提，未通过时终止，返回当前分支名。"""
    problems = collect_problems(version, tag, notes, args.allow_dirty, not args.dry_run)
    if problems:
        print('发布前检查未通过：')
        for item in problems:
            print('  -', item)
        sys.exit(1)
    return git('rev-parse', '--abbrev-ref', 'HEAD')


def publish_gitee(version: str, tag: str, notes_text: str, branch: str, dry_run: bool):
    """在 Gitee 创建发行版并上传全部产物。"""
    token = os.environ.get('GITEE_TOKEN', '')
    files = artifacts(version)
    total = sum(path.stat().st_size for path in files)
    print(f'[Gitee] 发行版 {tag}，共 {len(files)} 个附件，合计 {human(total)}')
    if dry_run:
        for path in files:
            print(f'        将上传 {path.name}（{human(path.stat().st_size)}）')
        return

    release = gitee_create_release(tag, f'{PACK_ID} {tag}', notes_text, branch, token)
    for path in files:
        print(f'        上传 {path.name}（{human(path.stat().st_size)}）…', flush=True)
        gitee_upload(release['id'], path, token)
    uploaded = {item['name'] for item in gitee_attachments(release['id'], token)}
    missing = [path.name for path in files if path.name not in uploaded]
    if missing:
        sys.exit(f'[Gitee] 以下附件未上传成功：{missing}')
    print(f'[Gitee] 完成：{release.get("html_url") or tag}')


def publish_github(version: str, tag: str, notes: Path, dry_run: bool):
    """用 vpk 在 GitHub 创建发行版并上传全部产物。"""
    command = [
        build.find_vpk(), 'upload', 'github',
        '--repoUrl', GITHUB_REPO_URL,
        '--token', os.environ.get('GITHUB_TOKEN', ''),
        '--outputDir', str(RELEASES_DIR),
        '--tag', tag,
        '--releaseName', f'{PACK_ID} {tag}',
        '--publish',
    ]
    if notes:
        command += ['--releaseNotes', str(Path(notes).resolve())]
    print(f'[GitHub] 发行版 {tag}')
    if dry_run:
        printable = list(command)
        printable[printable.index('--token') + 1] = '***'
        print('        将执行：', ' '.join(printable))
        return
    subprocess.run(command, cwd=ROOT, check=True, env=build.dotnet_env())
    print('[GitHub] 完成')


def verify(version: str, tag: str):
    """确认客户端能读到刚发布的更新索引。"""
    import requests
    import time

    local = hashlib.sha256((RELEASES_DIR / FEED_NAME).read_bytes()).hexdigest()
    status = '未获取到'
    for attempt in range(3):
        try:
            response = requests.get(f'{GITEE_DOWNLOAD}/{FEED_NAME}', timeout=60)
            if response.status_code == 200:
                same = hashlib.sha256(response.content).hexdigest() == local
                status = '内容一致' if same else '内容不一致'
                break
            status = f'状态 {response.status_code}'
        except Exception as e:
            status = str(e)[:60]
        time.sleep(5 * (attempt + 1))
    print(f'[校验] Gitee 更新索引：{status}')

    names = []
    try:
        api = f'https://api.github.com/repos/{GITEE_OWNER}/{GITEE_REPO}/releases/tags/{tag}'
        response = requests.get(api, timeout=60)
        if response.status_code == 200:
            names = [item['name'] for item in response.json().get('assets', [])]
    except Exception as e:
        print(f'[校验] GitHub 查询失败：{e}')
    expected = [FEED_NAME, f'{PACK_ID}-{version}-full.nupkg', SETUP_NAME, PORTABLE_NAME]
    missing = [name for name in expected if name not in names]
    print(f'[校验] GitHub 附件：{"完整" if names and not missing else f"缺少 {missing}"}')


def is_update_file(name: str) -> bool:
    """判断附件是否属于更新链路。"""
    return name.endswith('.nupkg') or name == FEED_NAME


def gitee_usage(token: str = '') -> int:
    """返回 Gitee 附件占用的总字节数。"""
    total = 0
    for release in gitee_releases(token):
        for item in gitee_attachments(release['id'], token):
            total += item.get('size', 0)
    return total


def keep_attachment(index: int, tag: str, name: str) -> bool:
    """按保留策略判断附件是否保留。

    最新发行版保持完整；经典版本与里程碑版本保留安装包与便携包；
    中间版本只保留增量包；完整包与更新索引只在最新发行版中有用。
    """
    if index == 0:
        return True
    if name.endswith('-delta.nupkg'):
        return True
    if is_update_file(name):
        return False
    if is_classic(tag):
        return True
    if not is_milestone(tag):
        return False
    return MILESTONE_KEEP_PORTABLE or name != PORTABLE_NAME


def drop_tags(tags: list, dry_run: bool):
    """删除指定版本的全部附件，用于一次性清理历史版本。"""
    token = os.environ.get('GITEE_TOKEN', '')
    if not token:
        print('[清理] 未设置 GITEE_TOKEN，跳过')
        return
    wanted = {tag.strip() for tag in tags if tag.strip()}
    freed = 0
    for release in gitee_releases(token):
        tag = release.get('tag_name', '')
        if tag not in wanted:
            continue
        for item in gitee_attachments(release['id'], token):
            size = item.get('size', 0)
            freed += size
            print(f'[清理] {tag} / {item["name"]}（{human(size)}）')
            if not dry_run:
                gitee_delete_attachment(release['id'], item['id'], token)
    print(f'[清理] {"可释放" if dry_run else "已释放"} {human(freed)}')


def prune_gitee(dry_run: bool):
    """按保留策略清理 Gitee 附件。"""
    token = os.environ.get('GITEE_TOKEN', '')
    if not token:
        print('[清理] 未设置 GITEE_TOKEN，跳过')
        return
    freed = 0
    for index, release in enumerate(gitee_releases(token)):
        tag = release.get('tag_name', '')
        for item in gitee_attachments(release['id'], token):
            name = item['name']
            if keep_attachment(index, tag, name):
                continue
            size = item.get('size', 0)
            freed += size
            print(f'[清理] {tag} / {name}（{human(size)}）')
            if not dry_run:
                gitee_delete_attachment(release['id'], item['id'], token)
    print(f'[清理] {"可释放" if dry_run else "已释放"} {human(freed)}')


def quota_report():
    """统计 Gitee 附件占用。"""
    token = os.environ.get('GITEE_TOKEN', '')
    try:
        total = gitee_usage(token)
    except Exception as e:
        print(f'[配额] 统计失败：{e}')
        return
    print(f'[配额] Gitee 附件合计 {human(total)} / {human(TOTAL_LIMIT)}')
    if total > TOTAL_LIMIT * 0.8:
        print('[配额] 占用已超过八成，建议执行：python release.py --prune')


def main():
    parser = argparse.ArgumentParser(description='构建并发布 PowerPointReviewer')
    parser.add_argument('--notes', type=Path, help='更新说明（Markdown 文件）')
    parser.add_argument('--dry-run', action='store_true', help='只检查与打印，不做任何写入')
    parser.add_argument('--skip-build', action='store_true', help='跳过构建，直接发布现有产物')
    parser.add_argument('--prune', action='store_true', help='按保留策略清理 Gitee 附件')
    parser.add_argument('--drop-tags', default='', metavar='TAGS',
                        help='删除这些版本的全部附件，逗号分隔，用于一次性清理历史版本')
    parser.add_argument('--allow-dirty', action='store_true', help='允许工作区有未提交改动')
    args = parser.parse_args()

    version = build.read_version()
    tag = f'v{version}'

    if args.drop_tags:
        drop_tags(args.drop_tags.split(','), args.dry_run)

    if (args.prune or args.drop_tags) and not args.notes:
        if args.prune:
            prune_gitee(args.dry_run)
        quota_report()
        return

    print(f'准备发布 {tag}')
    branch = preflight(version, tag, args.notes, args)
    if not args.skip_build:
        build.build(version, args.notes)

    notes_text = Path(args.notes).read_text(encoding='utf-8-sig')
    publish_gitee(version, tag, notes_text, branch, args.dry_run)
    publish_github(version, tag, args.notes, args.dry_run)
    if not args.dry_run:
        verify(version, tag)
    if args.prune:
        prune_gitee(args.dry_run)
    quota_report()
    print(f'\n{tag} 发布完成')


if __name__ == '__main__':
    main()
