"""只读检查 PPTX 的顺序点击步骤，与实际生成的讲稿分段对照。

按 mainSeq 中的点击组计数，组内的同时、随后动画不增加点击。
对象触发、媒体及无法可靠读取的结构返回待确认项，不当作零次点击。
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile


_P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_NS = {'p': _P}


@dataclass(frozen=True)
class AnimationIssue:
    page: int | None
    segments: int | None
    clicks: int | None
    reason: str


def _clicks(slide):
    if slide.tag != f'{{{_P}}}sld':
        return None, '暂时无法读取此页的动画信息'
    timing = slide.find('p:timing', _NS)
    if timing is None:
        return 0, ''
    nodes = list(timing.iter(f'{{{_P}}}cTn'))
    if any(node.get('nodeType') == 'interactiveSeq' for node in nodes):
        return None, '包含点击指定对象触发的动画，请确认分段是否对应'
    if any(timing.find(f'.//p:{tag}', _NS) is not None for tag in ('audio', 'video', 'cmd')):
        return None, '包含媒体播放步骤，请确认分段是否对应'
    if timing.find('.//p:tmplLst', _NS) is not None:
        return None, '部分文字动画的点击次数需要人工确认'
    if any(cond.get('evt') == 'onClick' and cond.find('p:tgtEl/p:spTgt', _NS) is not None
           for cond in timing.iter(f'{{{_P}}}cond')):
        return None, '包含点击指定对象触发的动画，请确认分段是否对应'
    sequences = [node for node in nodes if node.get('nodeType') == 'mainSeq']
    if len(sequences) != 1:
        return None, '暂时无法确认此页的点击次数'
    groups = sequences[0].find('p:childTnLst', _NS)
    if groups is None:
        return 0, ''
    clicks = 0
    for group in groups:
        root = group.find('p:cTn', _NS)
        if root is None:
            return None, '暂时无法确认此页的点击次数'
        effect_types = {node.get('nodeType') for node in root.iter(f'{{{_P}}}cTn')}
        if not effect_types.intersection({'clickEffect', 'clickPar'}):
            # An automatic entry group contains only with/after effects.
            if not effect_types.intersection({'withEffect', 'afterEffect', 'withGroup', 'afterGroup'}):
                return None, '暂时无法确认此页的点击次数'
            continue
        conditions = root.findall('p:stCondLst/p:cond', _NS)
        if not conditions or any(cond.get('evt') not in (None, 'onClick') for cond in conditions):
            return None, '暂时无法确认此页的点击次数'
        if not any(cond.get('delay') == 'indefinite' or cond.get('evt') == 'onClick'
                   for cond in conditions):
            return None, '暂时无法确认此页的点击次数'
        clicks += 1
    return clicks, ''


def check_alignment(source, segments):
    """检查讲稿涉及的页，报告空讲稿和点击不匹配；页码采用文稿实际顺序。"""
    counts = Counter(int(item['page']) for item in segments)
    spoken_pages = {int(item['page']) for item in segments if item['text'].strip()}
    if not counts or not source:
        return []
    source = Path(source)
    if source.suffix.lower() != '.pptx':
        return [AnimationIssue(None, None, None, '此格式暂不支持预先检查，请确认点击动画与讲稿分段是否对应')]
    with ZipFile(source) as archive:
        document = ET.fromstring(archive.read('ppt/presentation.xml'))
        relationships = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {rel.get('Id'): rel.get('Target') for rel in relationships
                   if rel.get('Type', '').endswith('/slide') and rel.get('TargetMode') != 'External'}
        slides = document.find('p:sldIdLst', _NS)
        if slides is None:
            raise ValueError('无法读取 PPT 页码。')
        issues = []
        for page, count in sorted(counts.items()):
            if not 1 <= page <= len(slides):
                issues.append(AnimationIssue(page, count, None, '讲稿页码超出了 PPT 的页数'))
                continue
            target = targets[slides[page - 1].get(f'{{{_R}}}id')]
            part = (target.lstrip('/') if target.startswith('/') else
                    posixpath.normpath(posixpath.join('ppt', target)))
            clicks, reason = _clicks(ET.fromstring(archive.read(part)))
            reasons = []
            empty = page not in spoken_pages
            if empty:
                reasons.append('此页没有可朗读的讲稿')
            if clicks is None:
                reasons.append(reason)
            elif clicks != count - 1:
                difference = count - 1 - clicks
                reason = (f'讲稿多出 {difference} 个点击步骤' if difference > 0 else
                          f'还有 {-difference} 个点击步骤未对应讲稿')
                reasons.append(reason)
            if reasons:
                issues.append(AnimationIssue(page, 0 if empty else count, clicks, '；'.join(reasons)))
        return issues
