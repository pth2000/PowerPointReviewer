"""用已保存文稿的独占副本生成视频，讲稿时间轴负责翻页与顺序点击。"""

from collections import Counter
from contextlib import contextmanager
import logging
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QEventLoop, QThread, QTimer, Signal

from app.presentation.visibility import hidden_shape_ids
from app.presentation.com import application_candidates, application_pid, running_applications
from app.video.export_progress import ExportProgressMonitor, powerpoint_process_id
from app.video.eta import RemainingTimeDisplay, RemainingTimeEstimator
from app.video.mux import MuxCancelled, build_video_captions, mux_video
from app.video.native_media import ensure_native_media_available, join_narration, probe_media
from app.video.narration import NarrationEncoder
from app.video.powerpoint_timing import (
    build_powerpoint_schedule, read_slide_elements, scan_slide_timings,
)


logger = logging.getLogger('pptreviewer.video.powerpoint')

# 媒体页会按自身的帧与音频块边界补齐，误差不会随目标帧率同比缩短。
# 用固定的时间界限容纳小量补齐，长文稿也不按时长比例放大允许误差。
_MEDIA_CLOCK_TOLERANCE_US = 200000


def _video_clock_matches(info, schedule, has_media):
    if info.video_packets <= 0 or info.width <= 0 or info.height <= 0:
        return False
    if has_media:
        return abs(info.duration_us - schedule.total_frames * schedule.tick_us) <= _MEDIA_CLOCK_TOLERANCE_US
    return info.tick_us == schedule.tick_us and info.video_packets == schedule.total_frames


class PowerPointTimingError(RuntimeError):
    """原生画面已完整生成，但时长检查未通过；清理前保留画面供取回。"""

    def __init__(self, picture, expected_us, actual_us):
        self.picture = Path(picture)
        difference = abs(actual_us - expected_us) / 1000000
        super().__init__(f'视频与配音的时长相差 {difference:.2f} 秒，暂未完成合成。')


@contextmanager
def _export_workspace():
    """仅清理本次独占目录；短暂占用可重试，清理失败不应丢失完整成片。"""
    workspace = tempfile.TemporaryDirectory(prefix='pptreviewer-export-')
    try:
        yield Path(workspace.name)
    finally:
        for attempt in range(5):
            try:
                workspace.cleanup()
                break
            except OSError:
                if attempt == 4:
                    logger.warning('临时导出目录仍被占用，暂未清理：%s', workspace.name, exc_info=True)
                else:
                    time.sleep(.1 * (2 ** attempt))


class VideoExportUnavailable(RuntimeError):
    """软件无法提供原生视频导出，可以尝试其他软件。"""


def _require_video_export(application):
    try:
        library = application._oleobj_.GetTypeInfo().GetContainingTypeLib()[0]
        identifier = str(library.GetLibAttr()[0]).upper()
    except Exception as exc:
        raise VideoExportUnavailable('无法确认演示软件，请检查安装后重试。') from exc
    if identifier not in ('{91493440-5A91-11CF-8700-00AA0060263B}',
                           '{44720440-94BF-4940-926D-4F38FECF2A48}'):
        raise VideoExportUnavailable('当前演示软件不支持动画导出。')
    return 'PowerPoint' if identifier == '{91493440-5A91-11CF-8700-00AA0060263B}' else 'WPS 演示'


def _check_powerpoint_version(application, options):
    """版本号只用于基本门槛，具体输出能力由短片校准确认。"""
    try:
        version = str(application.Version)
        major = int(version.split('.')[0])
    except Exception as exc:
        raise VideoExportUnavailable('无法读取 PowerPoint 版本，请检查安装，或使用“OBS 录制”。') from exc
    if major < 16:
        raise VideoExportUnavailable('PowerPoint 导出需要 2016 或更新版本，请更新 PowerPoint，或使用“OBS 录制”。')
    if options.height >= 2160 and sys.getwindowsversion().major < 10:
        raise ValueError('4K 导出需要 Windows 10 或更新系统，请选择较低的分辨率，或使用“OBS 录制”。')


def _check_export_parameters(info, options, provider='PowerPoint'):
    """复用已有校准短片，防止旧版本静默降低分辨率或帧率。"""
    if info.height != options.height:
        raise ValueError(f'当前 {provider} 未能按所选分辨率导出（{options.height}p）。'
                         '请选择较低的分辨率，或使用“OBS 录制”。')
    # PowerPoint 的画面时间刻度可能按毫秒取整，容纳一个毫秒的量化差。
    if info.tick_us <= 0 or abs(info.tick_us - 1000000 / options.fps) > 1000:
        raise ValueError(f'当前 {provider} 未能按所选帧率导出（{options.fps} fps）。'
                         '请选择其他帧率，或使用“OBS 录制”。')


def _remove_hidden_effects(document, source, pages):
    """仅整理独占副本，让 PowerPoint 重建忽略隐藏对象后的动画分组和索引。"""
    changed = False
    for page, xml in zip(pages, read_slide_elements(source, pages)):
        hidden = hidden_shape_ids(xml)
        if not hidden:
            continue
        sequence = document.Slides.Item(page).TimeLine.MainSequence
        for index in range(int(sequence.Count), 0, -1):
            effect = sequence.Item(index)
            if str(effect.Shape.Id) in hidden:
                effect.Delete()
                changed = True
    if changed:
        document.Save()


def _effect_duration(timing, page):
    """计算完整效果时长；内置行为的往返已计入 Duration，不再重复加倍。"""
    duration = float(timing.Duration)
    count = float(timing.RepeatCount)
    limit = float(timing.RepeatDuration)
    reverse = int(timing.AutoReverse)
    if (not all(math.isfinite(value) and value >= 0 for value in (duration, count, limit))
            or reverse not in (0, -1)):
        raise ValueError(f'第 {page} 页的动画时长无法确认。')
    duration *= count * (2 if reverse == -1 else 1)
    # RepeatDuration 为 0 时未设置上限，否则与完整重复时长取较短者。
    if limit:
        duration = min(duration, limit)
    return duration


def _slide_shapes(shapes):
    for index in range(1, int(shapes.Count) + 1):
        shape = shapes.Item(index)
        yield shape
        if int(shape.Type) == 6:
            yield from _slide_shapes(shape.GroupItems)


def _bound_effect(timing, effect, tick_us, page):
    """将原放映中的点击/翻页中断转换为导出可执行的时长边界，不改变播放速度。"""
    if effect.media and not effect.indefinite:
        # playFrom 是启动媒体的命令，不是播放器的生命周期。
        # 给它追加 RepeatDuration 会反复发出播放命令，让单次视频也循环。
        return
    available = max(0.001, (effect.end_frame - effect.frame - .25) * tick_us / 1000000)
    if effect.indefinite:
        existing = float(timing.RepeatDuration)
        limit = min(existing, available) if existing > 0 else available
        if effect.indefinite:
            duration = float(timing.Duration) * (2 if int(timing.AutoReverse) == -1 else 1)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError(f'第 {page} 页的动画时长无法确认。')
            # 导出区间已经确定，把无界循环编译为足够覆盖区间的有限循环。
            # 仅写 RepeatDuration 而保留无限标记时，PowerPoint 仍可能等待下一次点击。
            timing.RepeatCount = min(2147483647, max(1, math.ceil(limit / duration))) if duration else 1
        timing.RepeatDuration = limit
    elif _effect_duration(timing, page) > available:
        timing.RepeatDuration = available


def _finish_effect(timing, boundary, tick_us, at_page_end=False):
    """在下一次点击前完成跳过部分，保留效果参数及其最终状态。"""
    count = max(1, int(timing.RepeatCount))
    reverse = 2 if int(timing.AutoReverse) == -1 else 1
    timing.RepeatCount = count
    timing.RepeatDuration = 0
    # 点击快进限制在一个输出采样区间内；翻页后的状态不进入最后一帧。
    quantum = .001 if at_page_end else tick_us / 1000000
    duration = max(.001, quantum / (count * reverse))
    timing.Duration = duration
    timing.TriggerType = 2
    timing.TriggerDelayTime = max(0, boundary - count * reverse * duration - .001)


def _configure_effect(sequence, item, effect, tick_us, page, page_end):
    boundary = effect.delay + (effect.end_frame - effect.frame) * tick_us / 1000000
    if effect.instant:
        _finish_effect(item.Timing, boundary, tick_us, effect.end_frame == page_end)
        return
    if not effect.indefinite and not effect.media:
        available = (effect.end_frame - effect.frame) * tick_us / 1000000
        if effect.end_frame < page_end and _effect_duration(item.Timing, page) > available:
            # Clone 完整保留路径、强调和退出等内部行为。原条目按原速播放到点击边界，
            # 快进副本在该点击前补齐最终状态，复现放映 View.Next 的 seek 行为。
            final = sequence.Clone(item, -1)
            _finish_effect(final.Timing, boundary, tick_us)
            # 释放被截断的中间属性值，否则它的 hold 状态会覆盖快进副本的最终状态。
            item.Timing.RewindAtEnd = -1
    _bound_effect(item.Timing, effect, tick_us, page)
    item.Timing.TriggerType = 2
    item.Timing.TriggerDelayTime = effect.delay


def _bound_media(slide, plan, tick_us, originals):
    starts = {}
    for effect in plan.effects:
        if effect.media:
            identifier = int(originals[effect.index].Shape.Id)
            starts[identifier] = min(starts.get(identifier, effect.frame), effect.frame)
    for shape in _slide_shapes(slide.Shapes):
        if int(shape.Type) != 16:
            continue
        # PlaySettings 的旧接口会重建 MainSequence、清除静音并改写播放触发。
        # 媒体沿用原播放设置，由页面时间和原生媒体节点结束播放。
        start = starts.get(int(shape.Id), plan.start_frame + plan.entry_frames)
        available_ms = max(1, round((plan.end_frame - start - .25) * tick_us / 1000))
        media = shape.MediaFormat
        begin = int(media.StartPoint)
        end = int(media.EndPoint) or int(media.Length)
        # 视频长于页面剩余时间时，在副本内裁掉本来就不会展示的尾部。
        # 短视频的完整片段和原循环设置均保留，继续循环至换页。
        if end > begin + available_ms:
            media.EndPoint = begin + available_ms


def _configure_slide(slide, plan, timing, tick_us, check_cancelled=None):
    sequence = slide.TimeLine.MainSequence
    # 修改触发方式或 Clone 会重排集合，必须在修改前保存所有原条目的稳定引用。
    originals = {}
    for effect in plan.effects:
        if check_cancelled:
            check_cancelled()
        originals[effect.index] = sequence.Item(effect.index)
    for effect in plan.effects:
        if check_cancelled:
            check_cancelled()
        _configure_effect(sequence, originals[effect.index], effect, tick_us, plan.page, plan.end_frame)
    if timing.has_media:
        _bound_media(slide, plan, tick_us, originals)
    transition = slide.SlideShowTransition
    transition.Hidden = 0
    transition.AdvanceOnClick = 0
    transition.AdvanceOnTime = -1
    transition.AdvanceTime = plan.advance_time


def _render_video(presentation, path, options, timeout, *, check_cancelled,
                  progress=None, pid=None, duration_us=None, estimate=None, provider='PowerPoint', stage=None):
    """仅正式画面导出启用进度监听；所有出口都先停监听再清理文稿。"""
    import pythoncom
    monitor = None
    estimator = RemainingTimeEstimator()
    estimate_display = RemainingTimeDisplay()
    last_estimate = None

    def report_estimate(text):
        nonlocal last_estimate
        if estimate and text != last_estimate:
            last_estimate = text
            try:
                estimate(text)
            except Exception:
                logger.debug('无法更新导出时间预估', exc_info=True)
    try:
        check_cancelled()
        settings = presentation.SlideShowSettings
        settings.RangeType = 1
        settings.AdvanceMode = 2
        settings.ShowWithAnimation = -1
        settings.ShowWithNarration = 0
        if duration_us is not None:
            if stage:
                stage('rendering')
            monitor = ExportProgressMonitor(pid, duration_us)
            monitor.start()
        try:
            presentation.CreateVideo(str(path), True, 4, options.height, options.fps, 85)
        except Exception as exc:
            if provider == 'WPS 演示':
                raise VideoExportUnavailable('当前版本的 WPS 演示不支持动画导出。') from exc
            raise
        started = time.monotonic()
        deadline = started + timeout
        next_report = 0
        while time.monotonic() < deadline:
            check_cancelled()
            try:
                status = int(presentation.CreateVideoStatus)
            except Exception as exc:
                if provider == 'WPS 演示':
                    raise VideoExportUnavailable('当前版本的 WPS 演示无法查询视频导出状态。') from exc
                raise
            if status == 3:
                return probe_media(path, cancelled=lambda: _is_cancelled(check_cancelled))
            if status == 4:
                raise RuntimeError(f'{provider}未能生成视频，请检查文稿是否可以正常导出。')
            now = time.monotonic()
            if provider == 'WPS 演示' and status == 0 and now - started >= 3:
                raise VideoExportUnavailable('当前版本的 WPS 演示未能启动动画导出。')
            if monitor is not None and progress and now >= next_report:
                next_report = now + .25
                signal = monitor.read()
                if signal.saving:
                    if stage:
                        stage('picture_save')
                    estimator.reset()
                    estimate_display.reset()
                    report_estimate('')
                    progress('正在保存视频画面…', -1)
                elif signal.fraction is not None:
                    if stage:
                        stage('rendering')
                    remaining = estimator.observe(signal.fraction, now)
                    report_estimate(estimate_display.update(remaining, now) if signal.fraction < 1 else '')
                    progress('正在生成视频画面…', min(.99, signal.fraction) * 100)
                else:
                    estimator.reset()
                    estimate_display.reset()
                    report_estimate('')
                    progress('正在生成视频画面…', -1)
            pythoncom.PumpWaitingMessages()
            time.sleep(.1)
        raise RuntimeError(f'{provider}导出长时间未完成，请检查演示软件后重试。')
    finally:
        if monitor is not None:
            monitor.close()
        report_estimate('')


def _is_cancelled(check_cancelled):
    try:
        check_cancelled()
        return False
    except MuxCancelled:
        return True


def export_powerpoint_video(source, timeline, directory, options, *, progress=None, cancelled=None,
                           estimate=None, preference='auto', stage=None):
    """按软件偏好选择原生导出，仅在能力不可用时回退，不掩盖文稿错误。"""
    if cancelled and cancelled():
        raise MuxCancelled()
    if sys.platform != 'win32':
        raise RuntimeError('动画导出目前需要 Windows 和支持视频导出的演示软件。')
    import pythoncom
    pythoncom.CoInitialize()
    try:
        # 每款软件只试一次；多个运行实例不能重复使用同一试导出目录。
        candidates = list(dict.fromkeys((prog, name) for prog, name, _app in
                                       application_candidates(running_applications(), preference)))
    finally:
        pythoncom.CoUninitialize()
    failures = []
    for index, (prog_id, provider) in enumerate(candidates):
        try:
            return _export_presentation_video(source, timeline, Path(directory) / prog_id, options,
                progress=progress, cancelled=cancelled, estimate=estimate, prog_id=prog_id, provider=provider,
                stage=stage)
        except VideoExportUnavailable as exc:
            failures.append(f'{provider}：{exc}')
            logger.info('%s 原生导出不可用：%s', provider, exc)
            if cancelled and cancelled():
                raise MuxCancelled() from exc
            if progress and index + 1 < len(candidates):
                progress(f'{provider}无法完成动画导出，正在尝试 {candidates[index + 1][1]}…', -1)
    raise RuntimeError('无法完成视频导出，请检查演示软件，或选择“OBS 录制”。\n' + '\n'.join(failures))


def _export_presentation_video(source, timeline, directory, options, *, progress=None, cancelled=None,
                               estimate=None, prog_id='PowerPoint.Application', provider='PowerPoint', stage=None):
    """生成并校验画面，返回视频路径；分辨率按高度设置，保留页面比例。"""
    if sys.platform != 'win32':
        raise RuntimeError('动画导出目前需要 Windows 和支持视频导出的演示软件。')
    source, directory = Path(source).resolve(), Path(directory).resolve()
    if not source.is_file() or source.suffix.lower() not in ('.ppt', '.pptx'):
        raise ValueError('请选择已保存的 PPT 文稿。')
    pages = list(dict.fromkeys(cue.page for cue in timeline.cues))
    if not pages:
        raise ValueError('请先生成讲稿配音。')
    if pages != sorted(pages):
        raise ValueError('讲稿页码须按顺序排列。')
    logger.info('开始动画导出：软件=%s；文稿=%s；页数=%d；片段=%d；配音时长=%.3f 秒；输出=%sp@%s fps',
                provider, source.name, len(pages), len(timeline.cues), timeline.duration, options.height, options.fps)
    ensure_native_media_available()
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / ('source' + source.suffix.lower())
    target = directory / 'picture.mp4'
    if source == copy or copy.exists() or target.exists():
        raise ValueError('导出准备目录已被使用，请重新导出。')

    def check_cancelled():
        if cancelled and cancelled():
            raise MuxCancelled()

    def report(text, value):
        check_cancelled()
        if progress:
            progress(text, value)

    check_cancelled()
    if stage:
        stage('preparing')
    shutil.copyfile(source, copy)
    import pythoncom
    import win32com.client
    import win32com.client.dynamic
    import win32process

    pythoncom.CoInitialize()
    application = document = None
    security = alerts = None
    owned_pid = None

    def render(presentation, path, timeout, duration_us=None):
        return _render_video(presentation, path, options, timeout, check_cancelled=check_cancelled,
                             progress=report, pid=pid, duration_us=duration_us,
                             estimate=estimate if duration_us is not None else None, provider=provider, stage=stage)

    try:
        original_pids = set(win32process.EnumProcesses())
        try:
            application = win32com.client.dynamic.Dispatch(win32com.client.DispatchEx(prog_id))
        except Exception as exc:
            raise VideoExportUnavailable(f'无法启动{provider}，请确认已安装并能正常打开文稿。') from exc
        provider = _require_video_export(application)
        pid = powerpoint_process_id(application) if provider == 'PowerPoint' else None
        app_pid = application_pid(application)
        if provider == 'PowerPoint':
            app_pid = pid
        owned_pid = app_pid if app_pid and app_pid not in original_pids else None
        if provider == 'PowerPoint':
            _check_powerpoint_version(application, options)
        security, alerts = application.AutomationSecurity, application.DisplayAlerts
        application.AutomationSecurity = 3
        application.DisplayAlerts = 1
        if provider == 'WPS 演示':
            # 先独立试导出，避免空实现进入整份文稿的处理与反复校准。
            report('正在检查 WPS 演示的视频导出…', -1)
            capability = application.Presentations.Add(0)
            try:
                slide = capability.Slides.Add(1, 12)
                slide.SlideShowTransition.AdvanceOnClick = 0
                slide.SlideShowTransition.AdvanceOnTime = -1
                slide.SlideShowTransition.AdvanceTime = .24
                capability.SaveAs(str(directory / 'capability.pptx'), 24)
                render(capability, directory / 'capability.mp4', 30)
            finally:
                slide = None
                try:
                    capability.Saved = -1
                    capability.Close()
                except Exception:
                    logger.warning('无法关闭 WPS 视频试导出文稿', exc_info=True)
                capability = None
        report('正在读取 PPT…', 0)
        document = application.Presentations.Open(str(copy), 0, 0, 0)
        if copy.suffix == '.ppt':
            copy = directory / 'source.pptx'
            if copy.exists():
                raise ValueError('导出准备目录已被使用，请重新导出。')
            document.SaveAs(str(copy), 24)
        if pages[-1] > int(document.Slides.Count):
            raise ValueError('讲稿页码超出了 PPT 的页数。')
        _remove_hidden_effects(document, copy, pages)
        timings, issues = scan_slide_timings(copy, pages)
        segment_counts = Counter(cue.page for cue in timeline.cues)
        for page, timing in zip(pages, timings):
            if timing is None:
                continue
            if segment_counts[page] != timing.clicks + 1:
                issues.append(f'第 {page} 页有 {timing.clicks} 个点击步骤，需要 {timing.clicks + 1} 段讲稿，当前有 {segment_counts[page]} 段。')
            slide = document.Slides.Item(page)
            if int(slide.TimeLine.MainSequence.Count) != len(timing.effects):
                issues.append(f'第 {page} 页的动画组合暂不能使用动画导出。')
        if issues:
            raise ValueError('以下页面需要处理后才能导出：\n' + '\n'.join(issues))

        def calibrate(indices, keep_entry, name):
            """只插入目标页及其前一页，避免校准长文稿时反复导出全部页面。"""
            probe = application.Presentations.Add(0)
            try:
                probe.PageSetup.SlideWidth = document.PageSetup.SlideWidth
                probe.PageSetup.SlideHeight = document.PageSetup.SlideHeight
                for page in indices:
                    probe.Slides.InsertFromFile(str(copy), int(probe.Slides.Count), page, page)
                for index in range(1, len(indices) + 1):
                    slide = probe.Slides.Item(index)
                    sequence = slide.TimeLine.MainSequence
                    while sequence.Count:
                        sequence.Item(sequence.Count).Delete()
                    media = [shape for shape in _slide_shapes(slide.Shapes) if int(shape.Type) == 16]
                    for shape in reversed(media):
                        shape.Delete()
                    transition = slide.SlideShowTransition
                    if not keep_entry or index != len(indices):
                        transition.EntryEffect = 0
                    transition.Hidden = 0
                    transition.AdvanceOnClick = 0
                    transition.AdvanceOnTime = -1
                    transition.AdvanceTime = .24
                return render(probe, directory / (name + '.mp4'), 60)
            finally:
                probe.Saved = -1
                probe.Close()

        report('正在准备文稿…', -1)
        baseline_one = calibrate(pages[:1], False, 'calibration-one')
        _check_export_parameters(baseline_one, options, provider)
        tick_us = baseline_one.tick_us
        if not 1 <= tick_us <= 100000:
            raise RuntimeError('无法确认视频时间刻度，请尝试其他帧率。')
        baseline_two = calibrate(pages[:2], False, 'calibration-two') if len(pages) > 1 else None
        entry_frames = []
        for index, page in enumerate(pages):
            report(f'正在准备文稿：{index + 1} / {len(pages)}', round(index * 80 / len(pages)))
            indices = pages[max(0, index - 1):index + 1]
            transition = document.Slides.Item(page).SlideShowTransition
            baseline = baseline_one if index == 0 else baseline_two
            if int(transition.EntryEffect) == 0:
                entry_frames.append(0)
                continue
            measured = calibrate(indices, True, f'calibration-{index + 1}')
            added = measured.video_packets - baseline.video_packets
            if measured.tick_us != tick_us or added < 0:
                raise RuntimeError('无法确认 PPT 转场时间，请尝试其他导出方式。')
            entry_frames.append(added)
        schedule = build_powerpoint_schedule(timeline, timings, entry_frames, tick_us)
        for index, (slide_plan, timing) in enumerate(zip(schedule.slides, timings)):
            report(f'正在准备视频：{index + 1} / {len(schedule.slides)}',
                   80 + round(index * 20 / len(schedule.slides)))
            slide = document.Slides.Item(slide_plan.page)
            _configure_slide(slide, slide_plan, timing, tick_us, check_cancelled)
        selected = set(pages)
        for page in range(int(document.Slides.Count), 0, -1):
            if page not in selected:
                document.Slides.Item(page).Delete()
        document.Save()
        report('文稿已准备', 100)
        if stage:
            stage('rendering')
        report('正在生成视频画面…', -1)
        info = render(document, target, max(120, timeline.duration * 4 + 30),
                      duration_us=schedule.total_frames * schedule.tick_us)
        has_media = any(timing.has_media for timing in timings)
        if not _video_clock_matches(info, schedule, has_media):
            logger.error('原生导出时长不符：计划=%d us；实际=%d us；帧数=%d；时间刻度=%d；媒体=%s；目标帧率=%d',
                         schedule.total_frames * tick_us, info.duration_us, info.video_packets, info.tick_us,
                         has_media, options.fps)
            raise PowerPointTimingError(target, schedule.total_frames * tick_us, info.duration_us)
        logger.info('原生画面已生成：计划=%d us；实际=%d us；差值=%d us；帧数=%d；目标帧率=%d；媒体=%s',
                    schedule.total_frames * tick_us, info.duration_us,
                    info.duration_us - schedule.total_frames * tick_us, info.video_packets, options.fps, has_media)
        report('视频画面已生成', 100)
        return target
    except (MuxCancelled, ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.exception('%s 原生导出失败', provider)
        raise RuntimeError(f'{provider}无法完成导出，请确认文稿能正常打开后重试。') from exc
    finally:
        slide = transition = None
        if document is not None:
            try:
                document.Saved = -1
            except Exception:
                logger.debug('无法标记原生导出的文稿副本为已保存', exc_info=True)
            try:
                document.Close()
            except Exception:
                logger.warning('无法关闭原生导出的文稿副本', exc_info=True)
        if application is not None:
            for name, value in (('AutomationSecurity', security), ('DisplayAlerts', alerts)):
                if value is not None:
                    try:
                        setattr(application, name, value)
                    except Exception:
                        logger.warning('无法恢复 PowerPoint 的 %s', name, exc_info=True)
            try:
                if owned_pid and int(application.Presentations.Count) == 0:
                    application.Quit()
            except Exception:
                logger.warning('无法恢复原生导出的 PowerPoint 状态', exc_info=True)
        document = application = None
        pythoncom.CoUninitialize()


class PowerPointExportTask(QThread):
    """完整导出在工作线程完成；target 须为调用方独占的空暂存文件。"""

    progress = Signal(str, float)
    estimated_time = Signal(str)
    stage_changed = Signal(str)

    def __init__(self, source, timeline, notes, target, options, parent=None, *, preference='auto'):
        super().__init__(parent)
        self.source, self.timeline, self.target, self.options = source, timeline, Path(target), options
        self.preference = preference
        self.notes = tuple(dict(note) for note in notes)
        self.result = None
        self.error = ''
        self.was_cancelled = False
        self._stage = None
        self._last_media_progress = 0.

    def _set_stage(self, name):
        if name != self._stage:
            self._stage = name
            self._last_media_progress = 0.
            self.estimated_time.emit('')
            self.stage_changed.emit(name)

    def _saving_progress(self, position, duration):
        now = time.monotonic()
        if duration > 0 and now - self._last_media_progress >= .1:
            self._last_media_progress = now
            self.progress.emit('正在保存视频…', min(99., max(0., position * 100 / duration)))

    def cancel(self):
        self.requestInterruption()

    def _preserve_failed_picture(self, picture):
        saved = None
        try:
            with tempfile.NamedTemporaryFile(
                    prefix=Path(self.source).stem[:80] + '-画面待检查-', suffix='.mp4',
                    dir=self.target.parent, delete=False) as output:
                saved = Path(output.name)
                with Path(picture).open('rb') as source:
                    shutil.copyfileobj(source, output)
            return saved
        except Exception:
            if saved is not None:
                saved.unlink(missing_ok=True)
            raise

    def run(self):
        owned_target = False
        try:
            if self.options.fps != self.timeline.fps:
                raise ValueError('视频设置不一致，请重新选择帧率后再导出。')
            if self.target.exists() and self.target.stat().st_size:
                raise ValueError('目标文件已存在，请更换保存位置或文件名。')
            # 调用方通常已创建独占的空暂存文件，失败时也须移除不完整的写入。
            owned_target = True
            captions = build_video_captions(self.notes, self.timeline) if self.options.embed_subtitles else ()
            self._set_stage('preparing')
            with _export_workspace() as directory:
                try:
                    picture = export_powerpoint_video(self.source, self.timeline, directory / 'picture', self.options,
                        progress=self.progress.emit, stage=self._set_stage,
                        cancelled=self.isInterruptionRequested, estimate=self.estimated_time.emit,
                        preference=self.preference)
                except PowerPointTimingError as exc:
                    if self.isInterruptionRequested():
                        raise MuxCancelled() from exc
                    try:
                        saved = self._preserve_failed_picture(exc.picture)
                    except OSError:
                        logger.exception('无法保留时长校验失败的原生画面')
                        raise RuntimeError(str(exc) + '\n无法保留画面，请检查输出目录的访问权限和剩余空间。') from exc
                    raise RuntimeError(str(exc) + f'\n未合并讲稿配音的画面已保留：{saved}') from exc
                audio = directory / 'narration.m4a'
                self._set_stage('narration')
                encoder = NarrationEncoder(self.timeline, audio)
                loop = QEventLoop()
                outcome, errors = [], []
                encoder.finished.connect(lambda _path: (outcome.append('done'), loop.quit()))
                encoder.failed.connect(lambda message: (errors.append(message), loop.quit()))
                encoder.cancelled.connect(lambda: (outcome.append('cancelled'), loop.quit()))
                encoder.progress.connect(lambda value: self.progress.emit('正在合成讲稿配音…', value * 100))
                cancel_timer = QTimer()
                cancel_timer.setInterval(50)
                cancel_timer.timeout.connect(lambda: encoder.cancel() if self.isInterruptionRequested() else None)
                cancel_timer.start()
                self.progress.emit('正在合成讲稿配音…', 0)
                QTimer.singleShot(0, encoder.start)
                loop.exec()
                cancel_timer.stop()
                encoder.session.setRecorder(None)
                encoder.session.setAudioBufferInput(None)
                encoder.deleteLater()
                # recorder 已停止，释放文件句柄后才能清理独占目录。
                from PySide6.QtCore import QCoreApplication, QEvent
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                if self.isInterruptionRequested() or outcome == ['cancelled']:
                    raise MuxCancelled()
                if errors or outcome != ['done']:
                    raise RuntimeError(errors[0] if errors else '配音未能保存完整，请重新导出。')
                self.progress.emit('讲稿配音已合成', 100)
                self._set_stage('joining')
                self.progress.emit('正在合并画面和配音…', -1)
                joined = directory / 'joined.mp4'
                join_narration(picture, audio, joined, cancelled=self.isInterruptionRequested)
                self._set_stage('saving')
                self.progress.emit('正在保存视频…', 0)
                mux_video(joined, self.target, self.options.container, captions,
                          cancelled=self.isInterruptionRequested, progress=self._saving_progress)
                self.result = str(self.target)
                self.progress.emit('视频已导出', 100)
        except MuxCancelled:
            self.was_cancelled = True
        except Exception as exc:
            logger.exception('PowerPoint 视频导出失败')
            self.error = str(exc)
        finally:
            self.estimated_time.emit('')
            if self.result is None and owned_target:
                try:
                    self.target.unlink(missing_ok=True)
                except OSError:
                    logger.warning('无法移除未完成的视频暂存文件：%s', self.target, exc_info=True)
