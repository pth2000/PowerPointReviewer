"""历史列表摘要；缓存解析结果，不缓存音频正文或 PPT 内容。"""

from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path

from app.logging_setup import get_logger
from app.presentation import link as presentation_link


@lru_cache(maxsize=2048)
def _summary(filename, modified_ns, size):
    with Path(filename).open('r', encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('历史记录格式不正确')
    items = data.get('items', [])
    if not isinstance(items, list):
        raise ValueError('历史记录中的配音列表格式不正确')
    profile = data.get('generation_profile')
    return {
        'source_name': Path(str(data.get('source_file') or '')).name or str(data.get('source_name') or '-'),
        'created_at': str(data.get('created_at') or ''),
        'updated_at': str(data.get('updated_at') or data.get('created_at') or ''),
        'items_count': len(items),
        'engine_mode': str(profile.get('mode') or '-') if isinstance(profile, dict) else '-',
        'speaker': str(data.get('speaker') or '-'),
        'mark': str(data.get('mark') or '-'),
        'session_id': str(data.get('session_id') or Path(filename).stem),
        'presentation': presentation_link.from_record(data),
    }


def read_record_summary(record_path, stat=None):
    path = Path(record_path)
    row = {'file_path': path, 'file_name': path.name, 'source_name': path.stem,
           'created_at': '-', 'updated_at': '-', 'items_count': 0, 'engine_mode': '-',
           'speaker': '-', 'mark': '-', 'session_id': path.stem,
           'presentation_state': '未关联', 'presentation_tooltip': '', 'read_error': ''}
    try:
        stat = stat or path.stat()
        fallback_time = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        row.update(_summary(str(path.resolve()), stat.st_mtime_ns, stat.st_size))
        for key in ('created_at', 'updated_at'):
            row[key] = (row[key] or fallback_time).replace('T', ' ')
        link = row.pop('presentation')
        associated = presentation_link.resolve(link)
        row['presentation_state'] = '已关联' if associated else '文件缺失' if link.get('path') else '未关联'
        row['presentation_tooltip'] = (str(associated) if associated else
            '关联 PPT 后可使用同步翻页、视频导出和写回 PPT 备注。')
    except Exception as exc:
        get_logger().exception('历史记录摘要读取失败：%s', path)
        row['read_error'] = str(exc)
        row['presentation_state'] = '无法读取'
        row['presentation_tooltip'] = '记录无法读取，可尝试恢复备份或删除此记录。'
    return row
