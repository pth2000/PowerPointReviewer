"""将讲稿采样时间轴与 PowerPoint 的顺序动画、视频帧对齐。"""

from dataclasses import dataclass
import math
from pathlib import Path
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app.video.timeline import SAMPLE_RATE
from app.presentation.media import is_media_control_sequence, media_shape_ids


P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'p': P}
EFFECT_TYPES = {'clickEffect', 'withEffect', 'afterEffect'}


@dataclass(frozen=True)
class EffectTiming:
    index: int
    step: int
    delay: float
    indefinite: bool = False
    stop_on_next: bool = False
    media: bool = False


@dataclass(frozen=True)
class SlideTiming:
    page: int
    clicks: int
    effects: tuple[EffectTiming, ...]
    has_media: bool = False


@dataclass(frozen=True)
class ScheduledEffect:
    index: int
    delay: float
    frame: int
    end_frame: int | None = None
    indefinite: bool = False
    media: bool = False
    instant: bool = False


@dataclass(frozen=True)
class ScheduledSlide:
    page: int
    start_frame: int
    end_frame: int
    entry_frames: int
    advance_time: float
    effects: tuple[ScheduledEffect, ...]


@dataclass(frozen=True)
class PowerPointSchedule:
    tick_us: int
    total_frames: int
    slides: tuple[ScheduledSlide, ...]


def _unsupported(page, detail):
    return ValueError(f'第 {page} 页{detail}，暂不能使用动画导出。')


def _delay(node, page):
    conditions = node.find('p:stCondLst', NS)
    if conditions is None:
        return 0.0
    if len(conditions) != 1:
        raise _unsupported(page, '的动画开始方式无法确认')
    condition = conditions[0]
    if condition.get('evt') or len(condition):
        raise _unsupported(page, '包含特殊动画触发方式')
    try:
        value = float(condition.get('delay', '0')) / 1000
    except ValueError as exc:
        raise _unsupported(page, '的动画延迟无法读取') from exc
    if not math.isfinite(value) or value < 0:
        raise _unsupported(page, '的动画延迟无效')
    return value


def _check_repeats(node, page):
    """读取重复设置；无限循环由导出时已知的点击或换页边界限定。"""
    for child in node.iter(f'{{{P}}}cTn'):
        for key in ('repeatCount', 'repeatDur'):
            value = child.get(key)
            if value is None:
                continue
            if value == 'indefinite':
                continue
            try:
                number = float(value)
            except ValueError as exc:
                raise _unsupported(page, '的动画重复设置无法读取') from exc
            if not math.isfinite(number) or number < 0:
                raise _unsupported(page, '的动画重复设置无效')


def _check_group_repeat(node, page):
    if (node.get('repeatCount') not in (None, '1000') or node.get('repeatDur') not in (None, '0')
            or node.get('autoRev') in ('1', 'true')):
        raise _unsupported(page, '包含整组循环的动画')


def parse_slide_timing(slide, page):
    """读取每个顺序动画相对所属点击步骤的偏移，不推测 COM 的分组延迟。"""
    has_media = any(node.tag in (f'{{{P}}}audio', f'{{{P}}}video') for node in slide.iter())
    timing = slide.find('p:timing', NS)
    if timing is None:
        return SlideTiming(page, 0, (), has_media)
    nodes = list(timing.iter(f'{{{P}}}cTn'))
    media_ids = media_shape_ids(timing)
    if any(node.get('nodeType') == 'interactiveSeq' and not is_media_control_sequence(node, media_ids)
           for node in nodes):
        raise _unsupported(page, '包含点击指定对象触发的动画')
    if timing.find('.//p:tmplLst', NS) is not None:
        raise _unsupported(page, '包含暂不能识别的文字动画组合')
    _check_repeats(timing, page)
    sequences = [node for node in nodes if node.get('nodeType') == 'mainSeq']
    if not sequences and has_media:
        return SlideTiming(page, 0, (), True)
    if len(sequences) != 1:
        raise _unsupported(page, '的动画顺序无法确认')
    groups = sequences[0].find('p:childTnLst', NS)
    effects = []
    step = 0

    def visit(container, offset):
        node = container.find('p:cTn', NS)
        if node is None:
            raise _unsupported(page, '的动画组合无法读取')
        offset += _delay(node, page)
        kind = node.get('nodeType')
        if kind in EFFECT_TYPES:
            indefinite = any(child.get(key) == 'indefinite' for child in node.iter(f'{{{P}}}cTn')
                             for key in ('repeatCount', 'repeatDur'))
            stop_on_next = any(condition.get('evt') == 'onNext'
                               for condition in node.findall('p:endCondLst/p:cond', NS))
            media = node.get('presetClass') == 'mediacall'
            effects.append(EffectTiming(len(effects) + 1, step, offset, indefinite, stop_on_next, media))
            return
        _check_group_repeat(node, page)
        if kind in {'clickPar', 'withGroup', 'afterGroup'} or container.tag != f'{{{P}}}par':
            raise _unsupported(page, '包含暂不支持的动画组合')
        children = node.find('p:childTnLst', NS)
        if children is None:
            raise _unsupported(page, '的动画组合不完整')
        for child in children:
            visit(child, offset)

    for group in (() if groups is None else groups):
        root = group.find('p:cTn', NS)
        if root is None:
            raise _unsupported(page, '的动画顺序无法读取')
        _check_group_repeat(root, page)
        kinds = {node.get('nodeType') for node in root.iter(f'{{{P}}}cTn')}
        clicked = bool(kinds.intersection({'clickEffect', 'clickPar'}))
        conditions = root.findall('p:stCondLst/p:cond', NS)
        if clicked:
            if not conditions or not any(c.get('delay') == 'indefinite' or c.get('evt') == 'onClick' for c in conditions):
                raise _unsupported(page, '的点击步骤无法确认')
            if any(c.get('evt') not in (None, 'onClick') or len(c) for c in conditions):
                raise _unsupported(page, '包含特殊点击步骤')
            step += 1
        elif not kinds.intersection(EFFECT_TYPES):
            raise _unsupported(page, '包含暂不支持的动画组合')
        elif step:
            raise _unsupported(page, '包含暂不支持的独立自动动画组')
        automatic_offset = 0.0
        if not clicked and conditions:
            offsets = []
            for condition in conditions:
                if condition.get('delay') == 'indefinite':
                    continue
                event = condition.get('evt')
                valid_reference = (len(condition) == 1 and condition[0].tag == f'{{{P}}}tn'
                                   and condition[0].get('val') == sequences[0].get('id'))
                if not ((event is None and not len(condition)) or (event == 'onBegin' and valid_reference)):
                    raise _unsupported(page, '包含特殊自动动画触发方式')
                try:
                    value = float(condition.get('delay', '0')) / 1000
                except ValueError as exc:
                    raise _unsupported(page, '的自动动画延迟无法读取') from exc
                if not math.isfinite(value) or value < 0:
                    raise _unsupported(page, '的自动动画延迟无效')
                offsets.append(value)
            if not offsets or len(set(offsets)) != 1:
                raise _unsupported(page, '的自动动画开始方式无法确认')
            automatic_offset = offsets[0]
        children = root.find('p:childTnLst', NS)
        if children is None:
            raise _unsupported(page, '的动画组合不完整')
        for child in children:
            visit(child, automatic_offset)
    return SlideTiming(page, step, tuple(effects), has_media)


def read_slide_elements(source, pages):
    """按文稿中的页序读取页面，不将部件文件名当作页码。"""
    with ZipFile(Path(source)) as archive:
        presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
        relationships = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        targets = {node.get('Id'): node.get('Target') for node in relationships
                   if node.get('Type', '').endswith('/slide') and node.get('TargetMode') != 'External'}
        slides = presentation.find('p:sldIdLst', NS)
        if slides is None:
            raise ValueError('无法读取 PPT 页码。')
        result = []
        for page in pages:
            if not 1 <= page <= len(slides):
                raise ValueError(f'讲稿包含第 {page} 页，但 PPT 只有 {len(slides)} 页。')
            target = targets[slides[page - 1].get(f'{{{R}}}id')]
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt', target))
            result.append(ET.fromstring(archive.read(part)))
        return tuple(result)


def scan_slide_timings(source, pages):
    pages = tuple(pages)
    result, issues = [], []
    for page, slide in zip(pages, read_slide_elements(source, pages)):
        try:
            result.append(parse_slide_timing(slide, page))
        except ValueError as exc:
            result.append(None)
            issues.append(str(exc))
    return tuple(result), issues


def build_powerpoint_schedule(timeline, timings, entry_frames, tick_us):
    """按全局时刻取整，避免逐页取整误差累积；声音始终使用原采样时刻。"""
    if not isinstance(tick_us, int) or not 1 <= tick_us <= 100000:
        raise ValueError('无法确认 PowerPoint 的视频时间刻度。')
    tick = tick_us / 1000000
    cues_by_page = {}
    for cue in timeline.cues:
        cues_by_page.setdefault(cue.page, []).append(cue)
    if [timing.page for timing in timings] != list(cues_by_page):
        raise ValueError('PPT 页面与配音时间轴不一致。')
    if len(timings) != len(entry_frames) or any(not isinstance(n, int) or n < 0 for n in entry_frames):
        raise ValueError('无法确认 PPT 的转场时长。')
    starts = [round(cues[0].action_frame / SAMPLE_RATE / tick) for cues in cues_by_page.values()]
    end = math.ceil(timeline.total_frames / SAMPLE_RATE / tick)
    boundaries = starts + [end]
    def schedule_slide(index, timing):
        cues = cues_by_page[timing.page]
        if len(cues) != timing.clicks + 1:
            raise ValueError(f'第 {timing.page} 页有 {timing.clicks} 个点击步骤，需要 {timing.clicks + 1} 段讲稿，当前有 {len(cues)} 段。')
        content_start = starts[index] + entry_frames[index]
        content_frames = boundaries[index + 1] - content_start
        if content_frames < 1:
            raise _unsupported(timing.page, '的配音时间短于转场时间')
        effects = []
        for effect in timing.effects:
            target = cues[effect.step].action_frame / SAMPLE_RATE + effect.delay
            if effect.step == 0:
                # 初始自动动画由页面入场触发；后续点击则跟随配音。
                frame = content_start + round(effect.delay / tick)
            else:
                frame = round(target / tick)
                if frame < content_start:
                    raise _unsupported(timing.page, '在转场结束前就需要执行下一次点击')
            end_frame = boundaries[index + 1]
            if not effect.media and (not effect.indefinite or effect.stop_on_next) and effect.step + 1 < len(cues):
                end_frame = round(cues[effect.step + 1].action_frame / SAMPLE_RATE / tick)
            # PowerPoint 的下一次点击会快进尚未完成的有限动画，包括尚在等待延迟的动画。
            # 到翻页才发生的快进置于最后一个采样点之后，避免改变最后一帧。
            instant = frame >= end_frame
            delay = ((end_frame - content_start) * tick if instant else (frame - content_start) * tick)
            effects.append(ScheduledEffect(effect.index, delay, min(frame, end_frame),
                                           end_frame, effect.indefinite, effect.media, instant))
        return ScheduledSlide(timing.page, starts[index], boundaries[index + 1],
                              entry_frames[index], (content_frames - .25) * tick, tuple(effects))

    slides, issues = [], []
    for index, timing in enumerate(timings):
        try:
            slides.append(schedule_slide(index, timing))
        except ValueError as exc:
            issues.append(str(exc))
    if issues:
        raise ValueError('以下页面的动画时间需要确认：\n' + '\n'.join(issues))
    return PowerPointSchedule(tick_us, end, tuple(slides))
