"""记录 PPT 的本地位置与内容身份，独立于讲稿和语音配置。"""

import hashlib
import json
import logging
from pathlib import Path
import tempfile


PPT_SUFFIXES = ('.pptx', '.ppt')
_KEEP_BASELINE = object()


def fingerprint(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path) -> dict:
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() not in PPT_SUFFIXES:
        raise ValueError('请选择已保存的 PPT 文稿。')
    return {'path': str(path), 'name': path.name, 'sha256': fingerprint(path)}


def validate(path, pages) -> dict:
    """核验文稿可读且覆盖讲稿页码；不读取或替换用户的讲稿。"""
    info = describe(path)
    required = max((int(page) for page in pages), default=1)
    if required < 1:
        raise ValueError('讲稿页码须从第 1 页开始。')
    if Path(path).suffix.lower() == '.pptx':
        from pptx import Presentation
        try:
            count = len(Presentation(str(path)).slides)
        except Exception as exc:
            raise ValueError('无法读取所选 PPT，请确认文件可以正常打开。') from exc
        if required > count:
            raise ValueError(f'讲稿包含第 {required} 页，但所选 PPT 只有 {count} 页。')
        info['slide_count'] = count
    else:
        # 旧 PPT 的二进制格式由演示软件读取，沿用只读副本与资源清理流程。
        from app.presentation.images import export_slide_images
        with tempfile.TemporaryDirectory(prefix='pptreviewer-link-') as directory:
            export_slide_images(path, [required], directory, 16, 16)
    return info


def from_record(record) -> dict:
    link = record.get('presentation')
    if isinstance(link, dict):
        return dict(link)
    source = record.get('source_file')
    if source and Path(source).suffix.lower() in PPT_SUFFIXES:
        return {'path': str(source), 'name': Path(source).name}
    return {}


def resolve(link, candidates=()):
    """原位置可用时直接使用；其他位置仅接受内容完全相同的文件。"""
    source = link.get('path')
    if source and Path(source).is_file() and Path(source).suffix.lower() in PPT_SUFFIXES:
        return Path(source)
    expected = link.get('sha256')
    if not expected:
        return None
    seen = set()
    for candidate in candidates:
        path = Path(candidate)
        try:
            key = str(path.resolve()).casefold()
            if key in seen or not path.is_file() or path.suffix.lower() not in PPT_SUFFIXES:
                continue
            seen.add(key)
            if fingerprint(path) == expected:
                return path.resolve()
        except OSError:
            continue
    return None


def nearby_files(directory):
    """仅检查指定目录下的文稿，不递归搜索用户磁盘。"""
    try:
        return sorted(path for path in Path(directory).iterdir()
                      if path.is_file() and path.suffix.lower() in PPT_SUFFIXES
                      and not path.name.startswith('~$'))
    except OSError:
        return []


def update_record(record_path, link, *, notes_baseline=_KEEP_BASELINE, notes=_KEEP_BASELINE):
    """原子更新当前记录的关联字段，保留原语音配置、讲稿与音频引用。"""
    target = Path(record_path)
    with target.open('r', encoding='utf-8') as stream:
        record = json.load(stream)
    record['presentation'] = dict(link)
    if notes_baseline is not _KEEP_BASELINE:
        record['presentation_notes'] = notes_baseline
    if notes is not _KEEP_BASELINE:
        record['notes'] = notes
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                     prefix=target.stem + '-', suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        except Exception:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def refresh_identity(link, path):
    """记录存在时补齐旧记录身份；文件读取失败也不阻止语音播放。"""
    try:
        return {**link, **describe(path)}
    except OSError:
        logging.getLogger(__name__).warning('无法读取关联文稿的校验信息', exc_info=True)
        return dict(link)
