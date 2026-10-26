"""发现已安装的 OBS，启动独立便携实例，不读取或修改用户的 OBS 配置。"""

import configparser
from contextlib import contextmanager
import fnmatch
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

from PySide6.QtCore import QLockFile

from app.paths import DATA_DIR
from app.video.mux import MuxCancelled
from app.video.obs_client import ObsClient, ObsRequestError


logger = logging.getLogger(__name__)
RUNTIME_DIR = DATA_DIR / 'cache/obs-studio'
_IGNORED = ('*.pdb', 'obs-browser*', 'obs-vst*')


def _installation_files(source):
    files = []
    for name in ('bin', 'data', 'obs-plugins'):
        for directory, children, filenames in os.walk(source / name):
            children[:] = sorted(child for child in children
                                 if not any(fnmatch.fnmatch(child, pattern) for pattern in _IGNORED))
            for filename in sorted(filenames):
                if not any(fnmatch.fnmatch(filename, pattern) for pattern in _IGNORED):
                    path = Path(directory) / filename
                    info = path.stat()
                    files.append([path.relative_to(source).as_posix(), info.st_size, info.st_mtime_ns])
    return files


@contextmanager
def _external_libraries():
    """子程序使用自身的 DLL，不继承打包程序的 DLL 搜索目录。"""
    if not getattr(sys, 'frozen', False):
        yield
        return
    import win32api
    original = win32api.GetDllDirectory()
    try:
        win32api.SetDllDirectory(None)
        yield
    finally:
        win32api.SetDllDirectory(original)


def wait_saved(path, cancelled, timeout=30):
    """停止返回后仍可能有复用子进程写入，必须等待独占读取成功。"""
    import win32con
    import win32file
    import pywintypes
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            raise MuxCancelled()
        if path.is_file() and path.stat().st_size:
            try:
                handle = win32file.CreateFile(str(path), win32con.GENERIC_READ, 0, None,
                                               win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)
                handle.Close()
                return
            except (OSError, pywintypes.error):
                pass
        time.sleep(.05)
    raise RuntimeError('OBS 视频尚未保存完整，请检查输出目录和剩余空间。')


def find_obs(configured=''):
    candidates = [Path(configured)] if configured else []
    if not configured:
        found = shutil.which('obs64.exe')
        if found:
            candidates.append(Path(found))
        for variable in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / 'obs-studio/bin/64bit/obs64.exe')
    for executable in candidates:
        root = executable.parent.parent.parent
        if (executable.name.casefold() == 'obs64.exe' and executable.is_file()
                and (root / 'data/obs-studio').is_dir() and (root / 'obs-plugins/64bit').is_dir()):
            return executable.resolve()
    return None


def check_obs_version(executable):
    """只读文件版本，在启动和复制程序前识别过旧的安装。"""
    import win32api
    import pywintypes
    try:
        version = win32api.GetFileVersionInfo(str(executable), '\\')
    except (OSError, pywintypes.error):
        return
    if version['FileVersionMS'] >> 16 < 28:
        raise RuntimeError('请更新至 OBS Studio 28 或更新版本后再录制。')


def _encoder(executable, options):
    """使用 OBS 自带的探测器确认 NVENC；其他设备使用快速软件编码。"""
    probe = executable.parent / 'obs-nvenc-test.exe'
    if probe.is_file():
        try:
            with _external_libraries():
                result = subprocess.run([str(probe)], cwd=str(executable.parent), capture_output=True,
                                        timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            data = configparser.ConfigParser()
            data.read_string(result.stdout.decode('utf-8', errors='replace'))
            if (result.returncode == 0 and data.getboolean('general', 'nvenc_supported', fallback=False)
                    and data.getint('h264', 'codec_supported', fallback=0)
                    and options.width <= data.getint('h264', 'max_width', fallback=0)
                    and options.height <= data.getint('h264', 'max_height', fallback=0)):
                return 'nvenc'
        except (OSError, subprocess.SubprocessError, configparser.Error, ValueError):
            logger.info('OBS 硬件编码探测未通过，使用软件编码')
    return 'x264'


class ObsRuntime:
    def __init__(self, executable, workspace, options, cancelled, phase=lambda _text: None):
        self.executable, self.workspace = Path(executable), Path(workspace)
        self.options, self.cancelled = options, cancelled
        self.phase = phase
        self.process = None
        self.client = None
        self.runtime = RUNTIME_DIR.resolve()
        self.lock = None
        self.job = None
        self.exit_code = None
        self.forced_shutdown = False
        self._diagnostics_saved = False

    @property
    def log_directory(self):
        return self.runtime / 'config/obs-studio/logs'

    def _remove_directory(self, name):
        directory = self.runtime / name
        if not directory.exists():
            return
        # 只清理本功能固定缓存中的已知子目录，不跟随指向缓存外的联接。
        if name not in ('bin', 'data', 'obs-plugins', 'config') or directory.resolve().parent != self.runtime:
            raise RuntimeError('OBS 缓存位置异常，请检查程序目录后重试。')
        shutil.rmtree(directory)

    def _prepare_runtime(self):
        if self.cancelled():
            raise MuxCancelled()
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.runtime.with_suffix('.lock')))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise RuntimeError('OBS 正在用于另一次视频导出，请等待完成后重试。')
        self.lock = lock
        self.runtime.mkdir(parents=True, exist_ok=True)
        source = self.executable.parent.parent.parent
        if self.runtime.is_relative_to(source.resolve()) or source.resolve().is_relative_to(self.runtime):
            raise RuntimeError('请选择本机安装的 OBS Studio 程序。')
        manifest = self.runtime / 'installation.json'
        expected = {'source': str(source.resolve()), 'files': _installation_files(source)}
        try:
            current = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            current = None
        if current == expected and all((self.runtime / name).is_file() for name, _, _ in expected['files']):
            return
        manifest.unlink(missing_ok=True)
        for name in ('bin', 'data', 'obs-plugins'):
            self._remove_directory(name)
            (self.runtime / name).mkdir()

        for name, _, _ in expected['files']:
            if self.cancelled():
                raise MuxCancelled()
            original, target = source / name, self.runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # OBS 可更新 data 中的服务目录；仅程序文件适合同盘硬链接。
            if name.startswith('data/'):
                shutil.copy2(original, target)
            else:
                try:
                    os.link(original, target)
                except OSError:
                    shutil.copy2(original, target)
        manifest.write_text(json.dumps(expected), encoding='utf-8')

    def start(self):
        self.phase('正在准备录制程序…')
        self._prepare_runtime()
        runtime = self.runtime
        self._remove_directory('config')
        (runtime / 'portable_mode.txt').touch()
        config = runtime / 'config/obs-studio'
        profile = config / 'basic/profiles/PowerPointReviewer'
        scenes = config / 'basic/scenes'
        plugin = config / 'plugin_config/obs-websocket'
        output = self.workspace / 'capture'
        for directory in (profile, scenes, plugin, output):
            directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as endpoint:
            endpoint.bind(('127.0.0.1', 0))
            port = endpoint.getsockname()[1]
        password = secrets.token_urlsafe(32)
        (plugin / 'config.json').write_text(json.dumps({
            'server_enabled': True, 'server_port': port, 'auth_required': True,
            'server_password': password, 'alerts_enabled': False}), encoding='utf-8')
        global_ini = ('[General]\nFirstRun=true\nEnableAutoUpdates=false\nMaxLogs=2\nConfirmOnExit=false\n'
                      '[Basic]\nProfile=PowerPointReviewer\nProfileDir=PowerPointReviewer\n'
                      'SceneCollection=PowerPointReviewer\nSceneCollectionFile=PowerPointReviewer\n'
                      '[BasicWindow]\nPreviewEnabled=false\nSysTrayEnabled=true\n'
                      'WarnBeforeStartingRecord=false\nWarnBeforeStoppingRecord=false\n')
        for name in ('global.ini', 'user.ini'):
            (config / name).write_text(global_ini, encoding='utf-8')
        encoder = _encoder(self.executable, self.options)
        (profile / 'basic.ini').write_text(
            '[General]\nName=PowerPointReviewer\n[Video]\n'
            f'BaseCX={self.options.width}\nBaseCY={self.options.height}\n'
            f'OutputCX={self.options.width}\nOutputCY={self.options.height}\n'
            f'FPSType=0\nFPSCommon={self.options.fps}\n'
            '[Output]\nMode=Simple\nFilenameFormatting=recording\n'
            '[SimpleOutput]\n' + f'FilePath={output.as_posix()}\nRecFormat=mp4\nRecFormat2=fragmented_mp4\n'
            'MuxerCustom=movflags=frag_keyframe+empty_moov+delay_moov\n'
            f'RecQuality={self.options.obs_quality}\nStreamEncoder={encoder}\nRecEncoder={encoder}\n'
            'RecAudioEncoder=aac\nRecTracks=1\n'
            f'VBitrate={self.options.video_bit_rate // 1000}\nABitrate=128\n'
            'Preset=veryfast\nNVENCPreset2=p4\nRecRB=false\n'
            '[Audio]\nSampleRate=48000\nChannelSetup=Mono\n', encoding='utf-8')
        (scenes / 'PowerPointReviewer.json').write_text(json.dumps({
            'name': 'PowerPointReviewer', 'current_scene': 'Slides', 'current_program_scene': 'Slides',
            'scene_order': [{'name': 'Slides'}],
            'sources': [{'name': 'Slides', 'id': 'scene', 'settings': {'items': []}}],
            'DesktopAudioDevice1': None, 'DesktopAudioDevice2': None,
            'AuxAudioDevice1': None, 'AuxAudioDevice2': None,
            'AuxAudioDevice3': None, 'AuxAudioDevice4': None,
        }), encoding='utf-8')
        binary = runtime / 'bin/64bit/obs64.exe'
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        environment = dict(os.environ)
        # PyInstaller/PySide6 的 Qt 插件路径不能传给 OBS 自带的 Qt。
        for name in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORM',
                     'OBS_PLUGINS_PATH', 'OBS_PLUGINS_DATA_PATH', 'OBS_DATA_PATH'):
            environment.pop(name, None)
        with _external_libraries():
            self.process = subprocess.Popen(
                [str(binary), '--portable', '--multi', '--minimize-to-tray', '--disable-updater', '--only-bundled-plugins',
                 '--disable-missing-files-check', '--profile', 'PowerPointReviewer',
                 '--collection', 'PowerPointReviewer'], cwd=str(binary.parent),
                env=environment, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._own_process()
        self.phase('正在连接 OBS Studio…')
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if self.cancelled():
                raise MuxCancelled()
            if self.process.poll() is not None:
                raise RuntimeError('OBS Studio 未能启动，请确认安装完整后重试。')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.15):
                    pass
                self.client = ObsClient(port, password, self.cancelled)
                while True:
                    try:
                        version = self.client.call('GetVersion')
                        break
                    except ObsRequestError as exc:
                        if exc.code != 207 or time.monotonic() >= deadline:
                            raise
                        if self.cancelled():
                            raise MuxCancelled()
                        time.sleep(.1)
                required = {'GetVersion', 'GetStats', 'GetRecordStatus', 'StartRecord', 'StopRecord',
                            'GetInputList', 'RemoveInput', 'CreateInput', 'SetInputSettings',
                            'GetInputPropertiesListPropertyItems', 'GetSceneItemTransform', 'SetSceneItemTransform'}
                if not required.issubset(version.get('availableRequests', ())):
                    raise RuntimeError('请更新 OBS Studio 后重试。')
                logger.info('OBS 独立录制实例已启动：版本=%s；编码=%s', version.get('obsVersion'), encoder)
                return self.client
            except OSError:
                time.sleep(.1)
        raise RuntimeError('OBS Studio 启动超时，请确认使用 OBS Studio 28 或更新版本。')

    def _own_process(self):
        # 应用意外退出时由 Windows 回收本次 OBS 及其编码子进程，避免后台持续录制。
        if self.process.poll() is not None:
            return
        import win32api
        import win32con
        import win32job
        job = win32job.CreateJobObject(None, 'PowerPointReviewer-OBS-' + secrets.token_hex(16))
        try:
            limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
            process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                          False, self.process.pid)
            try:
                win32job.AssignProcessToJobObject(job, process)
            finally:
                process.Close()
        except Exception:
            job.Close()
            raise RuntimeError('无法准备 OBS 录制进程，请关闭本次导出后重试。')
        self.job = job

    def _stop_process(self):
        if self.client:
            self.client.close()
            self.client = None
        if self.process and self.process.poll() is None:
            # 只关闭本次创建的 PID，不影响用户正在运行的 OBS。
            import win32gui
            import win32process
            import pywintypes
            windows = []
            def close_window(hwnd, _):
                try:
                    if win32process.GetWindowThreadProcessId(hwnd)[1] != self.process.pid:
                        return
                    window_class = win32gui.GetClassName(hwnd)
                    if (window_class.startswith('Qt') and window_class.endswith('QWindowIcon')
                            and win32gui.GetWindowText(hwnd).startswith('OBS ')):
                        windows.append(hwnd)
                except (OSError, pywintypes.error):
                    pass  # 关闭时窗口列表可能已改变。
            win32gui.EnumWindows(close_window, None)
            # Qt 的托盘、屏幕监听与输入法也有顶层窗口；关闭它们会破坏 OBS 的退出流程。
            # 仅让主窗口处理退出，其他窗口与采集资源由 OBS 自行释放。
            if windows and self.process.poll() is None:
                try:
                    win32gui.PostMessage(windows[0], 0x0010, 0, 0)
                except (OSError, pywintypes.error):
                    pass
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.forced_shutdown = True
                logger.warning('OBS 主窗口退出超时，结束本次录制进程：PID=%s', self.process.pid)
                self.process.terminate()
                self.process.wait(timeout=5)

    def _save_diagnostics(self):
        if self._diagnostics_saved or self.process is None:
            return
        self._diagnostics_saved = True
        # 临时配置含随机连接凭据，仅留诊断文本，最多保存最近三次运行。
        directory = self.runtime / 'diagnostics'
        directory.mkdir(exist_ok=True)
        if directory.resolve().parent != self.runtime:
            raise OSError('OBS diagnostics directory is outside its cache')
        target = directory / f'run-{time.time_ns()}-{self.process.pid}'
        target.mkdir()
        for kind in ('logs', 'crashes'):
            for source in (self.runtime / 'config/obs-studio' / kind).glob('*.txt'):
                if source.is_file() and not source.is_symlink():
                    shutil.copy2(source, target / (kind + '-' + source.name))
        (target / 'exit.json').write_text(json.dumps({
            'pid': self.process.pid, 'exit_code': self.exit_code,
            'forced_shutdown': self.forced_shutdown,
        }), encoding='utf-8')
        runs = sorted(path for path in directory.glob('run-*') if path.is_dir())
        for old in runs[:-3]:
            if old.resolve().parent == directory.resolve() and not old.is_symlink():
                shutil.rmtree(old)
        logger.info('OBS 退出：PID=%s；退出码=%s；强制结束=%s；诊断=%s',
                    self.process.pid, self.exit_code, self.forced_shutdown, target)

    def close(self):
        try:
            self._stop_process()
        finally:
            # 即使窗口关闭失败，也须释放 Job；Windows 会结束本次创建的进程树。
            if self.job:
                if self.process and self.process.poll() is None:
                    self.forced_shutdown = True
                self.job.Close()
                self.job = None
                if self.process and self.process.poll() is None:
                    self.process.wait(timeout=5)
            if self.lock and (self.process is None or self.process.poll() is not None):
                try:
                    self.exit_code = self.process.poll() if self.process else None
                    try:
                        self._save_diagnostics()
                    except OSError:
                        logger.warning('无法保存 OBS 退出诊断', exc_info=True)
                    self._remove_directory('config')
                finally:
                    self.lock.unlock()
                    self.lock = None
        if self.process:
            self.exit_code = self.process.poll()
            if self.exit_code not in (None, 0) or self.forced_shutdown:
                raise RuntimeError('OBS Studio 未正常退出，请重新尝试导出。')
