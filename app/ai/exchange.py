"""导出包含提示词的优化任务，并校验外部 AI 返回的逐页结果。"""

import hashlib
import json

from app.ai import rewrite


RESULT_FORMAT = 'powerpointreviewer-ai-result'
RESULT_VERSION = 1


def source_hash(page: int, text: str, mark: str) -> str:
    source = json.dumps([page, text, mark], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def build_task(pages, *, mark: str, instruction: str, templates=None) -> str:
    """只导出所选正文与处理要求，不接收接口配置或密钥。"""
    config = rewrite.normalize_templates(templates)
    input_pages = []
    for page, text in pages:
        input_pages.append({'page': page, 'source_hash': source_hash(page, text, mark),
                            'segments': text.split(mark) if mark else [text]})
    if not input_pages:
        raise ValueError('请先勾选需要优化的页面。')
    example = {'format': RESULT_FORMAT, 'version': RESULT_VERSION, 'pages': [{
        'page': input_pages[0]['page'], 'source_hash': input_pages[0]['source_hash'],
        'segments': ['对应片段处理后的完整正文'],
    }]}
    return (
        '请按以下要求处理所有输入页面的讲稿。\n\n'
        f'处理原则：\n{config["system_prompt"]}\n\n'
        + rewrite.build_batch_rules(mark=mark, instruction=instruction, templates=config)
        + '\n\n输入讲稿：\n' + json.dumps(input_pages, ensure_ascii=False, indent=2)
        + '\n\n返回格式（优先于上文有关正文输出格式的要求）：\n'
          '只返回一个 JSON 对象，不附带说明或代码围栏。逐页保留 page 和 source_hash，'
          '不得修改、重排或重复页码。segments 的数量及顺序与各输入页保持一致，'
          '每项填写对应片段处理后的完整正文，不含分隔符。没有改动的片段也要原样填写。'
          '保留片段内部的换行，不使用省略号或占位文字代替正文。\n'
          '下方仅示意结构。实际返回的 pages 必须包含全部输入页，每页的片段数量以输入为准。\n'
        + json.dumps(example, ensure_ascii=False, indent=2)
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'结果中重复出现“{key}”，请检查完整结果。')
        result[key] = value
    return result


def parse_result(content: str, notes: dict, mark: str) -> dict[int, str]:
    """先校验全部页面再返回，失败时调用方不应修改任何已有候选结果。"""
    try:
        payload = json.loads(rewrite.clean_response(content.lstrip('\ufeff')), object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError('无法读取优化结果，请粘贴完整的 JSON 内容，或选择 AI 返回的结果文件。') from exc
    if (not isinstance(payload, dict) or payload.get('format') != RESULT_FORMAT
            or type(payload.get('version')) is not int or payload['version'] != RESULT_VERSION):
        raise ValueError('结果格式不匹配，请使用“导出优化任务”生成的任务，并导入对应结果。')
    pages = payload.get('pages')
    if not isinstance(pages, list) or not pages:
        raise ValueError('优化结果中没有可导入的页面。')
    results = {}
    for entry in pages:
        if not isinstance(entry, dict) or type(entry.get('page')) is not int:
            raise ValueError('优化结果中存在无效页码。')
        page = entry['page']
        if page in results:
            raise ValueError(f'第 {page} 页出现多次，请检查结果。')
        if page not in notes:
            raise ValueError(f'当前讲稿中没有第 {page} 页，请确认导入了对应任务的结果。')
        if entry.get('source_hash') != source_hash(page, str(notes[page]), mark):
            raise ValueError(f'第 {page} 页与当前讲稿不一致，请重新导出优化任务。')
        segments = entry.get('segments')
        if not isinstance(segments, list) or not segments or any(not isinstance(s, str) for s in segments):
            raise ValueError(f'第 {page} 页的讲稿片段格式不正确。')
        text = mark.join(segments) if mark else ''.join(segments)
        if not any(segment.strip() for segment in segments):
            raise ValueError(f'第 {page} 页的优化结果为空，请补全正文。')
        if not mark and len(segments) != 1:
            raise ValueError(f'第 {page} 页应保留为一个完整片段。')
        results[page] = text
    return results
