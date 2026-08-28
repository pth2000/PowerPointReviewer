"""导出和导入可跨设备播放的 PowerPointReviewer 工程包。

普通会话记录只含缓存键，离开原设备后无法定位音频。工程包将清单和音频写入同一 ZIP；
导入后按原缓存键恢复文件并生成本地会话记录，因而无需重新合成或配置远端服务。
"""

import json
import zipfile
from datetime import datetime
from pathlib import Path

from app import paths

PACKAGE_SUFFIX = '.pprpkg'
PACKAGE_FILTER = f'讲稿工程包 (*{PACKAGE_SUFFIX})'
MANIFEST_NAME = 'manifest.json'
AUDIO_DIR = 'audio'

PACKAGE_FORMAT = 'powerpointreviewer-package'
PACKAGE_VERSION = 1


def _audio_source(cache_key: str, cache_ext: str, fallback):
    """解析可打包的音频源，优先缓存文件，其次使用本次生成副本。"""
    if cache_key:
        candidate = paths.AUDIO_CACHE_DIR / f'{cache_key}.{cache_ext or "wav"}'
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate

    if fallback:
        path = Path(fallback)
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def build_items(notes_list, durations, cache_keys, cache_exts, media_paths):
    """将同索引的讲稿、时长和缓存元数据合并为清单条目。"""
    items = []
    for index, note in enumerate(notes_list):
        items.append({
            'index': index,
            'page': int(note.get('page', 0)),
            'text': str(note.get('text', '')),
            'duration': float(durations[index]) if index < len(durations) else 0.0,
            'cache_key': str(cache_keys[index]) if index < len(cache_keys) else '',
            'cache_ext': str(cache_exts[index]) if index < len(cache_exts) else 'wav',
            'media': str(media_paths[index]) if index < len(media_paths) else '',
        })
    return items


def estimate_size(items) -> int:
    """估算去重后的音频总字节数，供导出前确认。"""
    total = 0
    seen = set()
    for item in items:
        key = item.get('cache_key', '')
        if key in seen:
            continue
        seen.add(key)
        source = _audio_source(key, item.get('cache_ext', ''), item.get('media'))
        if source is not None:
            total += source.stat().st_size
    return total


def export_package(target_path, *, items, notes, mark, source_name,
                   generation_profile, speaker, app_version='',
                   include_audio: bool = True) -> dict:
    """写出工程包，并返回音频数量、缺失项和最终文件大小。"""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        'format': PACKAGE_FORMAT,
        'version': PACKAGE_VERSION,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'app_version': app_version,
        'source_name': source_name,
        'mark': mark,
        'speaker': speaker,
        'generation_profile': generation_profile or {},
        'has_audio': bool(include_audio),
        'notes': {str(k): v for k, v in dict(notes or {}).items()},
        'items': [{k: v for k, v in item.items() if k != 'media'} for item in items],
    }

    packed = 0
    missing = []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        if include_audio:
            written = set()
            for item in items:
                key = item.get('cache_key', '')
                ext = item.get('cache_ext', 'wav') or 'wav'
                name = f'{key}.{ext}'
                if not key or name in written:
                    continue

                source = _audio_source(key, ext, item.get('media'))
                if source is None:
                    missing.append(f'第{item.get("page")}页-第{item.get("index", 0) + 1}条')
                    continue

                archive.write(source, f'{AUDIO_DIR}/{name}')
                written.add(name)
                packed += 1

        manifest['missing_audio'] = missing
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    return {'audio_count': packed, 'missing': missing, 'size': target.stat().st_size}


def read_manifest(package_path) -> dict:
    """读取并校验工程包清单，拒绝损坏或更高版本的包。"""
    try:
        with zipfile.ZipFile(package_path) as archive:
            raw = archive.read(MANIFEST_NAME).decode('utf-8')
    except KeyError as e:
        raise RuntimeError('工程包缺少清单文件，可能已损坏') from e
    except zipfile.BadZipFile as e:
        raise RuntimeError('文件不是有效的工程包') from e

    try:
        manifest = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f'工程包清单解析失败：{e}') from e

    if manifest.get('format') != PACKAGE_FORMAT:
        raise RuntimeError('该文件不是本软件导出的工程包')
    if int(manifest.get('version', 0)) > PACKAGE_VERSION:
        raise RuntimeError('工程包由更高版本导出，请升级本软件后再试')
    if not manifest.get('items'):
        raise RuntimeError('工程包中没有讲稿内容')

    return manifest


def import_package(package_path) -> dict:
    """恢复工程包中的音频，并创建可由现有流程加载的历史记录。

    音频按原缓存键落盘；已存在且非空的同名文件直接复用，不重复写入。
    """
    manifest = read_manifest(package_path)
    paths.ensure_runtime_dirs()

    restored = 0
    skipped = 0
    with zipfile.ZipFile(package_path) as archive:
        for name in archive.namelist():
            if not name.startswith(f'{AUDIO_DIR}/') or name.endswith('/'):
                continue

            target = paths.AUDIO_CACHE_DIR / Path(name).name
            if target.exists() and target.stat().st_size > 0:
                skipped += 1
                continue

            data = archive.read(name)
            if not data:
                continue
            # 先写旁路临时文件，再原子替换，避免中断时留下可被误判为有效的缓存。
            temp = target.with_suffix(target.suffix + '.part')
            temp.write_bytes(data)
            temp.replace(target)
            restored += 1

    record = {
        'version': 1,
        'session_id': datetime.now().strftime('%Y%m%d_%H%M%S'),
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'source_file': '',
        'source_name': str(manifest.get('source_name', '') or '导入的工程包'),
        'mark': str(manifest.get('mark', '') or '●'),
        'speaker': str(manifest.get('speaker', '')),
        'generation_profile': manifest.get('generation_profile', {}),
        'notes': manifest.get('notes', {}),
        'items': manifest.get('items', []),
        'imported_from_package': True,
    }

    record_path = paths.SESSION_DIR / f'{record["session_id"]}.json'
    with record_path.open('w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    return {
        'record_path': record_path,
        'manifest': manifest,
        'restored': restored,
        'skipped': skipped,
        'has_audio': bool(manifest.get('has_audio', True)),
    }
