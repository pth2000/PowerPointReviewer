"""统计音频缓存，并按历史记录引用关系安全清理缓存文件。

缓存键由规范化文本和生成配置共同计算，相同输入会稳定映射到同一文件。历史记录只保存
这些键，因此清理时必须保留仍被任一记录引用的音频。
"""

import json
from pathlib import Path

from app import paths

AUDIO_SUFFIXES = ('.wav', '.mp3')


def format_size(num_bytes: int) -> str:
    """将字节数格式化为最高 GB 的易读字符串。"""
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


def iter_cache_files():
    """迭代缓存目录顶层的 WAV 和 MP3 文件。"""
    cache_dir = paths.AUDIO_CACHE_DIR
    if not cache_dir.exists():
        return
    for path in cache_dir.iterdir():
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
            yield path


def stats() -> tuple[int, int]:
    """返回缓存文件数量和总字节数。"""
    count = 0
    total = 0
    for path in iter_cache_files():
        try:
            total += path.stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


def keys_of_record(record_path) -> set[str]:
    """读取一条历史记录引用的全部非空缓存键；坏记录按空集合处理。"""
    keys: set[str] = set()
    try:
        with Path(record_path).open('r', encoding='utf-8') as f:
            record = json.load(f)
    except Exception as e:
        print(f'[缓存] 无法解析历史记录 {Path(record_path).name}：{e}')
        return keys

    for item in record.get('items', []) or []:
        if isinstance(item, dict):
            key = str(item.get('cache_key', '')).strip()
            if key:
                keys.add(key)
    return keys


def referenced_keys(exclude=None) -> set[str]:
    """汇总全部历史记录引用的缓存键，可排除一条指定记录。"""
    keys: set[str] = set()
    session_dir = paths.SESSION_DIR
    if not session_dir.exists():
        return keys

    skip = Path(exclude).resolve() if exclude else None
    for record_path in session_dir.glob('*.json'):
        if skip is not None and record_path.resolve() == skip:
            continue
        keys |= keys_of_record(record_path)
    return keys


def exclusive_keys(record_paths) -> set[str]:
    """返回目标记录集合独占的缓存键。

    不返回仍被其它记录引用的键，调用方因而可以在删除目标记录时安全清理对应音频。
    """
    if isinstance(record_paths, (str, Path)):
        record_paths = [record_paths]

    targets = {Path(p).resolve() for p in record_paths}
    own: set[str] = set()
    for path in targets:
        own |= keys_of_record(path)
    if not own:
        return set()

    session_dir = paths.SESSION_DIR
    others: set[str] = set()
    if session_dir.exists():
        for path in session_dir.glob('*.json'):
            if path.resolve() not in targets:
                others |= keys_of_record(path)

    return own - others


def measure_keys(keys) -> tuple[int, int]:
    """返回指定缓存键对应的文件数量和总字节数。"""
    wanted = {str(k).strip() for k in keys if str(k).strip()}
    count = 0
    total = 0
    for path in iter_cache_files():
        if path.stem in wanted:
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


def _remove(paths_to_remove) -> tuple[int, int]:
    """尽力删除给定文件，并返回成功删除数和释放字节数。"""
    removed = 0
    freed = 0
    for path in paths_to_remove:
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as e:
            print(f'[缓存] 删除失败 {path.name}：{e}')
            continue
        removed += 1
        freed += size
    return removed, freed


def clear_all() -> tuple[int, int]:
    """删除全部音频缓存，并返回删除统计。"""
    return _remove(list(iter_cache_files()))


def clear_orphans() -> tuple[int, int]:
    """删除未被任何历史记录引用的缓存文件。

    被引用的音频必须保留，否则相应历史记录将无法恢复播放。
    """
    keys = referenced_keys()
    orphans = [path for path in iter_cache_files() if path.stem not in keys]
    return _remove(orphans)


def remove_keys(keys) -> tuple[int, int]:
    """删除指定缓存键对应的文件，并返回删除统计。"""
    wanted = {str(k).strip() for k in keys if str(k).strip()}
    targets = [path for path in iter_cache_files() if path.stem in wanted]
    return _remove(targets)


def summary() -> str:
    """返回适合直接显示在界面上的缓存占用摘要。"""
    count, total = stats()
    return f'{count} 条 / {format_size(total)}'
