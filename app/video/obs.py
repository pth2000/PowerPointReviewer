"""OBS 负责采集和编码，录制帧时钟驱动放映，结束后合入既有配音。"""

from dataclasses import replace
import copy
import logging
from pathlib import Path
import shutil
import tempfile
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from app.presentation import window_target
from app.video.mux import MuxCancelled
from app.video.native_media import ensure_native_media_available, join_narration, probe_media
from app.video.narration import AUDIO_BIT_RATE, NarrationEncoder
from app.video.obs_runtime import ObsRuntime, check_obs_version, find_obs, wait_saved
from app.video.timeline import SAMPLE_RATE


logger = logging.getLogger(__name__)


def check_output_space(timeline, options, directory):
    copies = 3 if options.container == 'mkv' or options.embed_subtitles else 2
    bitrate = options.video_bit_rate
    if options.obs_quality != 'Stream':
        # 质量预设的码率随内容变化，此处仅按画面规模预留空间，不使用已隐藏的自定义码率。
        # 录制期间还会持续检查可用空间，避免将预留值当作实际大小的保证。
        scale = options.width * options.height * options.fps / (1920 * 1080 * 30)
        bitrate = 8000000 * scale * (2 if options.obs_quality == 'HQ' else 1)
    required = round(timeline.duration * (bitrate + AUDIO_BIT_RATE) / 8 * copies * 1.1)
    required += 32 * 1024 * 1024
    if shutil.disk_usage(directory).free < required:
        size = f'{required / 1024 ** 3:.1f} GB' if required >= 1024 ** 3 else f'{required / 1024 ** 2:.0f} MB'
        raise RuntimeError(f'保存位置的可用空间不足，预计需要约 {size}，请更换保存位置后重试。')


def shifted_timeline(timeline, frames):
    result = copy.copy(timeline)
    offset = frames * timeline.frames_per_tick
    result.cues = [replace(cue, action_frame=cue.action_frame + offset,
                           audio_frame=cue.audio_frame + offset, end_frame=cue.end_frame + offset)
                   for cue in timeline.cues]
    result.total_frames = timeline.total_frames + offset
    return result


def capture_transform(source_size, client_size, viewport, slide_size, output_size):
    sw, sh = source_size
    cw, ch = client_size
    x, y, vw, vh = viewport
    slide_width, slide_height = slide_size
    if min(sw, sh, cw, ch, vw, vh, slide_width, slide_height) <= 0:
        raise RuntimeError('无法读取放映画面，请重新选择录制窗口。')
    ratio = slide_width / slide_height
    width, height = min(vw, vh * ratio), min(vh, vw / ratio)
    x, y = x + (vw - width) / 2, y + (vh - height) / 2
    return {'cropLeft': max(0, round(x * sw / cw)), 'cropTop': max(0, round(y * sh / ch)),
            'cropRight': max(0, round((cw - x - width) * sw / cw)),
            'cropBottom': max(0, round((ch - y - height) * sh / ch)),
            'positionX': output_size[0] / 2, 'positionY': output_size[1] / 2,
            'alignment': 0, 'boundsType': 'OBS_BOUNDS_SCALE_INNER',
            'boundsWidth': output_size[0], 'boundsHeight': output_size[1], 'boundsAlignment': 0}


def capture_window_value(items, target):
    """严格匹配标题、窗口类和程序，禁止 OBS 悄悄切到相似窗口。"""
    for item in items:
        parts = str(item.get('itemValue', '')).split(':')
        if len(parts) == 3 and item.get('itemEnabled', True):
            title, class_name, process = (part.replace('#3A', ':').replace('#22', '#') for part in parts)
            if (title == target['title'] and class_name == target['class_name']
                    and process.casefold() == target['process'].casefold()):
                return item['itemValue']
    raise RuntimeError('OBS 未找到所选放映窗口，请重新选择窗口后重试。')


class _CaptureWorker(QThread):
    action = Signal(int)
    timeline_ready = Signal(object)
    progress = Signal(float, float, int, int)
    phase = Signal(str)

    def __init__(self, timeline, options, executable, workspace, target, viewport, slide_size, parent):
        super().__init__(parent)
        self.timeline, self.options = timeline, options
        self.executable, self.workspace = executable, workspace
        self.target, self.viewport, self.slide_size = target, viewport, slide_size
        self.stop_event, self.ack = threading.Event(), threading.Event()
        self.action_error = ''
        self.error, self.raw = '', None
        self.was_cancelled = False
        self.capture_item = None
        self.capture_geometry = None
        self.geometry_pending_since = None

    def cancel(self):
        self.stop_event.set()

    def _action(self, index):
        self.ack.clear()
        self.action.emit(index)
        deadline = time.monotonic() + 15
        while not self.ack.wait(.02):
            self._check_cancelled()
            if time.monotonic() > deadline:
                raise RuntimeError('演示软件响应超时，请确认放映可正常操作后重试。')
        if self.action_error:
            raise RuntimeError(self.action_error)

    def _check_cancelled(self):
        if self.stop_event.is_set():
            raise MuxCancelled()

    def _bind(self, client):
        # 该实例只属于本次导出，移除所有预置音频输入以免采入设备声音。
        for item in client.call('GetInputList')['inputs']:
            client.call('RemoveInput', inputName=item['inputName'])
        identity = window_target.window_info(self.target['hwnd'])
        if not identity or any(identity.get(key) != self.target.get(key) for key in ('pid', 'class_name', 'title')):
            raise RuntimeError('放映窗口已改变，请重新选择录制窗口。')
        created = client.call('CreateInput', sceneName='Slides', inputName='Presentation',
                              inputKind='window_capture', sceneItemEnabled=True,
                              inputSettings={'method': 2, 'cursor': False, 'client_area': True,
                                             'capture_audio': False, 'priority': 0})
        items = client.call('GetInputPropertiesListPropertyItems', inputName='Presentation',
                            propertyName='window')['propertyItems']
        value = capture_window_value(items, self.target)
        client.call('SetInputSettings', inputName='Presentation', inputSettings={'window': value}, overlay=True)
        self.capture_item = created['sceneItemId']
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self._check_cancelled()
            transform = client.call('GetSceneItemTransform', sceneName='Slides',
                                    sceneItemId=created['sceneItemId'])['sceneItemTransform']
            size = transform['sourceWidth'], transform['sourceHeight']
            if min(size) > 0:
                break
            self.stop_event.wait(.05)
        else:
            raise RuntimeError('OBS 未能采集放映画面，请确认窗口未最小化后重试。')
        self._fit_capture(client, size)

    def _fit_capture(self, client, size=None, *, pending=False):
        if size is None:
            transform = client.call('GetSceneItemTransform', sceneName='Slides',
                                    sceneItemId=self.capture_item)['sceneItemTransform']
            size = transform['sourceWidth'], transform['sourceHeight']
        root = window_target.client_rect(self.target['hwnd'])
        view = window_target.client_rect(self.viewport)
        invalid = min(size) <= 0 or root.isEmpty() or view.isEmpty() or not root.contains(view)
        if invalid and pending:
            now = time.monotonic()
            if self.geometry_pending_since is None:
                self.geometry_pending_since = now
            if now - self.geometry_pending_since < 3:
                return
        if invalid:
            raise RuntimeError('放映画面区域已改变，请重新选择录制窗口。')
        self.geometry_pending_since = None
        geometry = (size, root.width(), root.height(), view.x() - root.x(), view.y() - root.y(),
                    view.width(), view.height())
        if geometry == self.capture_geometry:
            return
        crop = capture_transform(size, (root.width(), root.height()),
                                 (view.x() - root.x(), view.y() - root.y(), view.width(), view.height()),
                                 self.slide_size, (self.options.width, self.options.height))
        client.call('SetSceneItemTransform', sceneName='Slides', sceneItemId=self.capture_item,
                    sceneItemTransform=crop)
        self.capture_geometry = geometry
        logger.info('OBS 放映窗口：进程=%s；采集=%s；裁切=%s', self.target['process'], size, crop)

    def _record(self, client):
        client.call('StartRecord')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self._check_cancelled()
            status = client.call('GetRecordStatus')
            if status['outputActive']:
                if status['outputPaused']:
                    raise RuntimeError('OBS 录制已中断，请重新导出视频。')
                break
            self.stop_event.wait(.02)
        else:
            raise RuntimeError('OBS 无法开始录制，请检查显卡驱动和输出目录。')
        # 已编码帧计数落后于采集，延迟随编码器变化。使用 OBS 视频渲染时钟，
        # 在确认采集已启动后建立原点，下一帧开始放映，配音及字幕使用同一原点。
        initial_stats = client.call('GetStats')
        render_origin = initial_stats['renderTotalFrames']
        self.timeline = shifted_timeline(self.timeline, 1)
        self.timeline_ready.emit(self.timeline)
        cue_index = -1
        last_frame, last_progress, last_verify, last_fit, last_space = -1, 0.0, 0.0, 0.0, 0.0
        last_change = time.monotonic()
        while True:
            self._check_cancelled()
            stats = client.call('GetStats')
            frame = stats['renderTotalFrames'] - render_origin
            now = time.monotonic()
            if self.capture_item is not None and now - last_fit >= 1:
                self._fit_capture(client, pending=True)
                last_fit = now
            if now - last_space >= 5:
                if shutil.disk_usage(self.workspace).free < 32 * 1024 * 1024:
                    raise RuntimeError('保存位置的剩余空间不足，录制已停止。')
                last_space = now
            if frame != last_frame:
                last_frame, last_change = frame, now
            elif now - last_change > 8:
                raise RuntimeError('OBS 已停止生成画面，请检查电脑负载和剩余空间。')
            sample = frame * self.timeline.frames_per_tick
            while cue_index + 1 < len(self.timeline.cues) and self.timeline.cues[cue_index + 1].action_frame <= sample:
                cue_index += 1
                self._action(cue_index)
            if cue_index >= 0:
                cue = self.timeline.cues[cue_index]
                if now - last_verify >= .25:
                    status = client.call('GetRecordStatus')
                    if not status['outputActive'] or status['outputPaused']:
                        raise RuntimeError('OBS 录制已中断，请重新导出视频。')
                    self._action(-1)
                    last_verify = now
                if now - last_progress >= .2:
                    self.progress.emit(min(sample, self.timeline.total_frames) / SAMPLE_RATE,
                                       self.timeline.duration, cue.page, cue_index + 1)
                    last_progress = now
            if sample >= self.timeline.total_frames:
                break
            self.stop_event.wait(.015)
        logger.info('OBS 录制完成：时长=%.3f 秒；渲染丢帧=%s；编码丢帧=%s', self.timeline.duration,
                     stats.get('renderSkippedFrames', 0) - initial_stats.get('renderSkippedFrames', 0),
                     stats.get('outputSkippedFrames', 0) - initial_stats.get('outputSkippedFrames', 0))
        raw = Path(client.call('StopRecord')['outputPath']).resolve()
        if not raw.is_relative_to(self.workspace.resolve()):
            raise RuntimeError('OBS 输出位置不正确，请重新启动录制。')
        self.raw = raw

    def run(self):
        runtime = ObsRuntime(self.executable, self.workspace, self.options, self.stop_event.is_set, self.phase.emit)
        client = None
        try:
            client = runtime.start()
            self.phase.emit('正在确认录制画面…')
            self._bind(client)
            self._record(client)
        except MuxCancelled:
            self.was_cancelled = True
        except Exception as exc:
            logger.exception('OBS 放映录制失败')
            self.error = str(exc)
            try:
                logs = sorted(runtime.log_directory.glob('*.txt'))
                if logs:
                    logger.error('OBS 日志末尾：\n%s', '\n'.join(
                        logs[-1].read_text(encoding='utf-8', errors='replace').splitlines()[-45:]))
            except OSError:
                logger.warning('无法读取 OBS 日志', exc_info=True)
        finally:
            if client:
                try:
                    # 取消之后仍须等停止命令完成，不能直接删除正在写入的文件。
                    client.cancelled = lambda: False
                    if client.call('GetRecordStatus')['outputActive'] and self.raw is None:
                        path = Path(client.call('StopRecord')['outputPath']).resolve()
                        if path.is_relative_to(self.workspace.resolve()):
                            self.raw = path
                    # StopRecord 只提交停止请求；等编码和文件收尾结束后再关闭主窗口。
                    deadline = time.monotonic() + 30
                    while client.call('GetRecordStatus')['outputActive']:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('OBS 停止录制超时。')
                        time.sleep(.05)
                    if self.raw:
                        wait_saved(self.raw, lambda: False)
                except Exception:
                    logger.warning('OBS 停止请求失败，关闭本次录制实例', exc_info=True)
            try:
                runtime.close()
            except Exception:
                logger.exception('OBS 独立实例关闭失败')
                self.error = self.error or '无法关闭本次 OBS 录制，请关闭后重试。'
            if self.error and self.raw is None:
                # OBS 异常退出时仍可取回已写入的分片，不能随临时目录一起丢弃。
                for path in (self.workspace / 'capture').glob('*.mp4'):
                    if path.resolve().is_relative_to(self.workspace.resolve()) and path.stat().st_size > 2048:
                        self.raw = path
                        break


class _JoinTask(QThread):
    def __init__(self, video, audio, target, parent):
        super().__init__(parent)
        self.video, self.audio, self.target = video, audio, target
        self.stop_event = threading.Event()
        self.error, self.was_cancelled = '', False

    def cancel(self):
        self.stop_event.set()

    def run(self):
        try:
            # OBS 进程已退出；检查文件之后才交给复用器读取。
            wait_saved(self.video, self.stop_event.is_set)
            info = probe_media(self.video, cancelled=self.stop_event.is_set)
            if not info.video_packets:
                raise RuntimeError('OBS 未保存有效画面，请重新录制。')
            join_narration(self.video, self.audio, self.target, cancelled=self.stop_event.is_set)
        except MuxCancelled:
            self.was_cancelled = True
        except Exception as exc:
            logger.exception('OBS 画面与配音合并失败')
            self.error = str(exc)


class ObsVideoRecording(QObject):
    progress = Signal(float, float, int, int)
    phase = Signal(str)
    timeline_ready = Signal(object)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, timeline, target, presentation, parent=None, options=None):
        super().__init__(parent)
        self.timeline, self.target, self.presentation = timeline, Path(target), presentation
        self.options = options
        self.state, self.cue_index = 'idle', -1
        self.worker = self.encoder = self.joining = None
        self.directory = None
        self.stopping = False

    def start(self):
        if self.state != 'idle':
            return
        self.state = 'starting'
        try:
            executable = find_obs(self.options.obs_path)
            if executable is None:
                raise RuntimeError('未找到 OBS Studio，请先安装或重新选择程序位置。')
            check_obs_version(executable)
            ensure_native_media_available()
            check_output_space(self.timeline, self.options, self.target.parent)
            self.presentation._verify_window()
            self.presentation.begin(self.timeline.cues[0].page)
            target = window_target.window_info(self.presentation.hwnd)
            if not target:
                raise RuntimeError('放映窗口已关闭，请重新开始放映后重试。')
            viewport = self.presentation.viewport_hwnd or self.presentation.hwnd
            self.directory = Path(tempfile.mkdtemp(prefix='.ppt-obs-', dir=self.target.parent))
            self.workspace = self.directory
            self.worker = _CaptureWorker(self.timeline, self.options, executable, self.workspace,
                                         target, viewport, self.presentation.slide_size.toTuple(), self)
            self.worker.action.connect(self._action)
            self.worker.timeline_ready.connect(self._timeline_ready)
            self.worker.progress.connect(self.progress)
            self.worker.phase.connect(self.phase)
            self.worker.finished.connect(self._captured)
            self.phase.emit('正在准备 OBS 录制…')
            self.state = 'recording'
            self.worker.start()
        except Exception as exc:
            logger.exception('OBS 录制启动失败')
            try:
                self.presentation.restore()
            except Exception:
                logger.warning('无法恢复放映设置', exc_info=True)
            self._finish(str(exc))

    def _timeline_ready(self, timeline):
        self.timeline = timeline
        self.timeline_ready.emit(timeline)
        self.phase.emit('正在录制放映…')

    def _action(self, index):
        try:
            if self.stopping or self.state != 'recording':
                return
            if index == 0:
                self.presentation.begin(self.timeline.cues[0].page)
            elif index > 0:
                self.presentation.advance(self.timeline.cues[index].page, self.timeline.cues[index - 1].page)
            if index >= 0:
                self.cue_index = index
            if self.cue_index >= 0:
                self.presentation.verify(self.timeline.cues[self.cue_index].page)
        except Exception as exc:
            logger.exception('OBS 录制中的放映控制失败：片段=%s', index)
            self.worker.action_error = str(exc)
        finally:
            self.worker.ack.set()

    def _captured(self):
        self.stopping = self.stopping or self.worker.was_cancelled
        try:
            self.presentation.restore()
        except Exception as exc:
            if self.stopping:
                logger.warning('取消录制后无法恢复放映设置', exc_info=True)
            else:
                logger.exception('OBS 录制后无法恢复放映设置')
            self._finish('' if self.stopping else str(exc))
            return
        if self.stopping or self.worker.was_cancelled:
            self._finish()
        elif self.worker.error:
            self._finish(self.worker.error)
        else:
            try:
                self.state = 'narration'
                self.phase.emit('正在准备视频配音…')
                self.encoder = NarrationEncoder(self.timeline, self.workspace / 'narration.m4a', self)
                self.encoder.finished.connect(self._join)
                self.encoder.failed.connect(self._finish)
                self.encoder.cancelled.connect(self._finish)
                self.encoder.start()
            except Exception as exc:
                logger.exception('OBS 录制后准备配音失败')
                self._finish(str(exc))

    def _join(self, audio):
        if self.stopping:
            self._finish()
            return
        self.state = 'saving'
        self.phase.emit('正在保存画面和配音…')
        self.joining = _JoinTask(self.worker.raw, audio, self.target, self)
        self.joining.finished.connect(self._joined)
        self.joining.start()

    def _joined(self):
        self._finish(self.joining.error)

    def cancel(self):
        self.stopping = True
        if self.state == 'recording' and self.worker:
            self.worker.cancel()
        elif self.state == 'narration' and self.encoder:
            self.encoder.cancel()
        elif self.state == 'saving' and self.joining:
            self.joining.cancel()

    def _finish(self, error=''):
        if self.state == 'done':
            return
        if error and self.worker and self.worker.raw:
            # 已完成的长录像不因后续配音编码/合并失败而丢失。
            try:
                recovery = Path(tempfile.mkdtemp(prefix='PPT-video-recovery-', dir=self.target.parent))
                self.worker.raw.replace(recovery / ('recording' + self.worker.raw.suffix))
                audio = self.workspace / 'narration.m4a'
                if audio.is_file() and audio.stat().st_size:
                    audio.replace(recovery / 'narration.m4a')
                error += f' 已录制画面保留在：{recovery}'
            except OSError:
                logger.warning('无法移动 OBS 录像，保留原目录以便取回', exc_info=True)
                self.directory = None
                error += f' 原始录像保留在：{self.workspace}'
        self.state = 'done'
        if self.directory:
            try:
                directory = self.directory.resolve()
                if directory.parent == self.target.parent.resolve() and directory.name.startswith('.ppt-obs-'):
                    shutil.rmtree(directory)
                else:
                    logger.error('OBS 临时目录不在本次输出目录内，跳过清理：%s', directory)
            except OSError:
                logger.warning('OBS 临时文件暂时无法清理：%s', self.directory, exc_info=True)
            self.directory = None
        if error:
            self.failed.emit(error)
        elif self.stopping:
            self.cancelled.emit()
        else:
            self.finished.emit(str(self.target))
