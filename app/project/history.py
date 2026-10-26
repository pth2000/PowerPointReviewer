"""历史记录编解码与原子保存，不依赖页面或 Qt 控件。"""

import copy
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.audio import segments as segment_audio
from app.project.model import ProjectSession


class MissingAudioError(RuntimeError):
    """记录的讲稿可读，但已有配音不完整。"""


@dataclass
class LoadedHistory:
    project: ProjectSession
    record: dict


def save_session(project, root, engine, *, baseline=None):
    """保存当前工程，沿用记录身份及扩展字段；失败时保留原文件。"""
    if not project.notes_list:
        return

    generation_profile = project.generation_profile if project.generation_profile is not None else engine.get_generation_profile()
    if len(project.note_cache_keys) != len(project.notes_list):
        project.note_cache_keys = [
            engine.build_audio_cache_key(item['text'], generation_profile)
            for item in project.notes_list
        ]

    if len(project.note_cache_exts) != len(project.notes_list):
        output_ext = engine.get_output_extension()
        project.note_cache_exts = [output_ext] * len(project.notes_list)

    durations = project.notes_duration_list[:]
    if len(durations) != len(project.notes_list):
        durations = [0.0] * len(project.notes_list)

    items = []
    for index, note in enumerate(project.notes_list):
        items.append({
            'index': index,
            'page': note['page'],
            'text': note['text'],
            'duration': float(durations[index]),
            'cache_key': project.note_cache_keys[index],
            'cache_ext': project.note_cache_exts[index],
        })
        if index in project.audio_edits:
            items[-1]['audio_edit'] = copy.deepcopy(project.audio_edits[index])

    now = datetime.now()
    previous = {}
    record_path = project.session_record_path
    if record_path and Path(record_path).is_file():
        record_path = Path(record_path)
        with record_path.open('r', encoding='utf-8') as stream:
            previous = json.load(stream)
        if not isinstance(previous, dict):
            raise RuntimeError('无法更新当前历史记录，请检查记录文件是否完整。')
    else:
        record_path = root / f'{now.strftime("%Y%m%d_%H%M%S_%f")}.json'
    session_id = str(previous.get('session_id') or record_path.stem)
    speaker_name = (project.generation_speaker if project.generation_profile is not None
                    else engine.get_selected_voice_name()).strip()
    record = {
        **previous,
        'version': 1,
        'session_id': session_id,
        'created_at': previous.get('created_at') or now.isoformat(timespec='seconds'),
        'updated_at': now.isoformat(timespec='seconds'),
        'source_file': str(project.note_file_path) if project.note_file_path else '',
        'source_name': project.note_file_name,
        'presentation': dict(project.presentation_link),
        'presentation_notes': baseline,
        'mark': project.mark,
        'script_comments_enabled': project.script_comments_enabled,
        'speaker': speaker_name,
        'generation_profile': generation_profile,
        'notes': project.notes,
        'items': items,
    }

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=record_path.parent,
                                         prefix=record_path.stem + '-', suffix='.tmp',
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, indent=2)
        temp_path.replace(record_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    project.session_record_path = record_path


def load_session(record_path, cache_dir, build_cache_key, *, fallback_mark="●"):
    """先完整解析并校验缓存，再返回可替换的工程，失败时不触碰当前工程。"""
    project = ProjectSession(mark=fallback_mark)
    with record_path.open('r', encoding='utf-8') as f:
        record = json.load(f)
    if not isinstance(record, dict):
        raise RuntimeError('历史记录格式不正确，请选择有效的记录文件。')

    items = record.get('items', [])
    if not items:
        raise RuntimeError('历史记录为空，无法加载')

    missing_list = []
    media_list = []
    notes_list = []
    duration_list = []
    cache_keys = []
    cache_exts = []

    for idx, item in enumerate(items):
        page = int(item.get('page', 0))
        text = str(item.get('text', ''))
        cache_key = str(item.get('cache_key', '')).strip()
        if not cache_key:
            profile = record.get('generation_profile', {})
            cache_key = build_cache_key(text, profile)

        cache_ext = str(item.get('cache_ext', '')).strip().lower().lstrip('.')
        ext_candidates = [cache_ext] if cache_ext else []
        for ext in ('wav', 'mp3'):
            if ext not in ext_candidates:
                ext_candidates.append(ext)

        cache_path = None
        for ext in ext_candidates:
            candidate = cache_dir / f'{cache_key}.{ext}'
            if candidate.exists() and candidate.stat().st_size > 0:
                cache_path = candidate
                cache_ext = ext
                break

        if cache_path is None:
            missing_list.append(f'第{page}页-第{idx + 1}条')
            continue

        media_list.append(cache_path)
        notes_list.append({'page': page, 'text': text})
        duration_list.append(float(item.get('duration', 0.0)))
        cache_keys.append(cache_key)
        cache_exts.append(cache_ext)

    if missing_list:
        missing_text = '、'.join(missing_list[:10])
        raise MissingAudioError(f'存在缺失音频缓存：{missing_text}')

    source_file = str(record.get('source_file', '')).strip()
    mark = str(record.get('mark') or project.mark).strip() or '●'

    notes = record.get('notes')
    restored_notes = {}
    if isinstance(notes, dict):
        for key, value in notes.items():
            try:
                restored_notes[int(key)] = value
            except (TypeError, ValueError):
                print(f'[历史记录] 跳过无法解析的页码：{key!r}')

    if restored_notes:
        restored = restored_notes
    else:
        rebuilt_notes = {}
        for item in notes_list:
            rebuilt_notes.setdefault(item['page'], []).append(item['text'])
        restored = {k: mark.join(v) for k, v in rebuilt_notes.items()}

    project.note_file_name = str(record.get('source_name', ''))
    project.note_file_path = Path(source_file) if source_file else None
    project.mark = mark
    project.notes = restored
    project.script_comments_enabled = bool(record.get('script_comments_enabled', False))
    profile = record.get('generation_profile')
    project.generation_profile = profile if isinstance(profile, dict) else {}
    project.generation_speaker = str(record.get('speaker') or '')

    project.notes_list = notes_list
    project.notes_duration_list = duration_list
    project.note_cache_keys = cache_keys
    project.note_cache_exts = cache_exts
    project.audio_edits = {index: edit for index, item in enumerate(items)
                        if (edit := segment_audio.validated_edit(item)) is not None}

    project.media_list = media_list
    project.current_index = 0
    project.session_record_path = Path(record_path)
    project.audio_loaded = True
    return LoadedHistory(project, record)
