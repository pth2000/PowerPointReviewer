"""OBS 接入的帧时钟、窗口匹配、独立配置和取消边界。"""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace as NS
import wave
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtTest import QSignalSpy

from qt_test_case import QtTestCase
from app.video.mux import MuxCancelled, build_video_captions
from app.video.obs import (ObsVideoRecording, _CaptureWorker, capture_transform, capture_window_value,
                           check_output_space, shifted_timeline)
from app.video.obs_client import ObsClient
from app.video.obs_runtime import ObsRuntime, check_obs_version, find_obs, wait_saved, _external_libraries
from app.video.settings import VideoRecordingOptions
from app.video.timeline import SAMPLE_RATE, TimelineAudio, VideoTimeline
import test_powerpoint_export_ui as native_ui


class FakeSocket(QObject):
    textMessageReceived = Signal(str)
    errorOccurred = Signal(object)
    disconnected = Signal()

    def __init__(self):
        super().__init__()
        self.sent, self.closed = [], False

    def send(self, op, data):
        QTimer.singleShot(0, lambda: self.textMessageReceived.emit(json.dumps({'op': op, 'd': data})))

    def open(self, _url):
        self.send(0, {'rpcVersion': 1, 'authentication': {'salt': 'salt', 'challenge': 'challenge'}})

    def sendTextMessage(self, message):
        payload = json.loads(message)
        self.sent.append(payload)
        if payload['op'] == 1:
            self.send(2, {'negotiatedRpcVersion': 1})
        else:
            self.send(5, {'eventType': 'unrelated'})
            self.send(7, {'requestId': payload['d']['requestId'],
                          'requestStatus': {'result': True}, 'responseData': {'outputDuration': 123}})

    def abort(self):
        self.closed = True


class ObsTests(QtTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cache_patch = patch('app.video.obs_runtime.RUNTIME_DIR', self.root / 'obs-runtime')
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def timeline(self):
        path = self.root / 'voice.wav'
        with wave.open(str(path), 'wb') as writer:
            writer.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            writer.writeframes(b'\x01\x00' * SAMPLE_RATE)
        return VideoTimeline([1, 1, 2], [path] * 3)

    def test_protocol_authenticates_and_matches_responses_without_an_extra_dependency(self):
        socket = FakeSocket()
        with patch('app.video.obs_client.QWebSocket', return_value=socket):
            client = ObsClient(12345, 'private password')
        secret = base64.b64encode(hashlib.sha256(b'private passwordsalt').digest())
        expected = base64.b64encode(hashlib.sha256(secret + b'challenge').digest()).decode()
        self.assertEqual(socket.sent[0]['d']['authentication'], expected)
        self.assertEqual(client.call('GetRecordStatus')['outputDuration'], 123)
        client.close()
        self.assertTrue(socket.closed)

    def test_connection_cancellation_closes_the_socket(self):
        socket = FakeSocket()
        with patch('app.video.obs_client.QWebSocket', return_value=socket), self.assertRaises(MuxCancelled):
            ObsClient(12345, 'password', cancelled=lambda: True)
        self.assertTrue(socket.closed)
        self.qt.processEvents()

    def test_disconnect_is_reported_without_waiting_for_request_timeout(self):
        socket = FakeSocket()
        with patch('app.video.obs_client.QWebSocket', return_value=socket):
            client = ObsClient(12345, 'password')
        socket.disconnected.emit()
        with self.assertRaisesRegex(RuntimeError, '无法连接 OBS'):
            client.call('GetStats')
        client.close()

    def test_invalid_response_is_reported_in_user_language(self):
        socket = FakeSocket()
        with patch('app.video.obs_client.QWebSocket', return_value=socket):
            client = ObsClient(12345, 'password')
        client._message('[]')
        with self.assertRaisesRegex(RuntimeError, '无法识别的响应'):
            client.call('GetStats')
        client.close()

    def test_old_obs_installation_is_identified_without_launching_it(self):
        with patch('win32api.GetFileVersionInfo', return_value={'FileVersionMS': 27 << 16}):
            with self.assertRaisesRegex(RuntimeError, 'OBS Studio 28'):
                check_obs_version(Path('obs64.exe'))
        with patch('win32api.GetFileVersionInfo', return_value={'FileVersionMS': 31 << 16}):
            check_obs_version(Path('obs64.exe'))

    def test_output_space_estimate_includes_intermediate_video_and_subtitles(self):
        timeline = self.timeline()
        with patch('app.video.obs.shutil.disk_usage', return_value=NS(free=1)):
            with self.assertRaisesRegex(RuntimeError, '预计需要约'):
                check_output_space(timeline, VideoRecordingOptions(), self.root)
        with patch('app.video.obs.shutil.disk_usage', return_value=NS(free=44 * 1024 ** 2)):
            check_output_space(timeline, VideoRecordingOptions(), self.root)
            with self.assertRaisesRegex(RuntimeError, '可用空间不足'):
                check_output_space(timeline, VideoRecordingOptions(embed_subtitles=True), self.root)

    def test_window_match_requires_title_class_and_process(self):
        target = {'title': 'Presentation: lesson', 'class_name': 'screenClass', 'process': 'POWERPNT.EXE'}
        value = 'Presentation#3A lesson:screenClass:powerpnt.exe'
        items = [{'itemValue': 'Presentation#3A lesson:Other:powerpnt.exe'},
                 {'itemValue': value, 'itemEnabled': True}]
        self.assertEqual(capture_window_value(items, target), value)
        for bad in ({**target, 'class_name': 'OtherClass'}, {**target, 'title': 'Different'}):
            with self.assertRaises(RuntimeError):
                capture_window_value(items, bad)

    def test_crop_uses_viewport_geometry_and_preserves_slide_ratio(self):
        result = capture_transform((1600, 1000), (1600, 1000), (0, 50, 1600, 900),
                                   (1600, 900), (1920, 1080))
        self.assertEqual((result['cropLeft'], result['cropTop'], result['cropRight'], result['cropBottom']),
                         (0, 50, 0, 50))
        self.assertEqual((result['positionX'], result['positionY']), (960, 540))
        result = capture_transform((1600, 1200), (1600, 1200), (0, 0, 1600, 1200),
                                   (1600, 900), (1920, 1080))
        self.assertEqual((result['cropTop'], result['cropBottom']), (150, 150))

    def test_resizing_refits_capture_and_moving_without_resizing_keeps_the_same_crop(self):
        worker = _CaptureWorker(self.timeline(), VideoRecordingOptions(), Path('obs64.exe'), self.root,
                                 {'hwnd': 1, 'process': 'POWERPNT.EXE'}, 2, (16, 9), None)
        worker.capture_item = 10
        client = Mock()
        with patch('app.video.obs.window_target.client_rect', side_effect=[
                QRect(0, 0, 1600, 1000), QRect(0, 50, 1600, 900),
                QRect(10, 10, 1600, 1000), QRect(10, 60, 1600, 900),
                QRect(10, 10, 1920, 1200), QRect(10, 60, 1920, 1080)]):
            worker._fit_capture(client, (1600, 1000))
            worker._fit_capture(client, (1600, 1000))
            worker._fit_capture(client, (1920, 1200))
        self.assertEqual(client.call.call_count, 2)
        last = client.call.call_args.kwargs['sceneItemTransform']
        self.assertEqual((last['cropTop'], last['cropBottom']), (50, 70))

    def test_capture_tolerates_transient_geometry_during_window_resize(self):
        worker = _CaptureWorker(self.timeline(), VideoRecordingOptions(), Path('obs64.exe'), self.root,
                                 {'hwnd': 1, 'process': 'POWERPNT.EXE'}, 2, (16, 9), None)
        client = Mock()
        with patch('app.video.obs.window_target.client_rect', return_value=QRect()), \
                patch('app.video.obs.time.monotonic', side_effect=[10, 14]):
            worker._fit_capture(client, (0, 0), pending=True)
            with self.assertRaisesRegex(RuntimeError, '画面区域已改变'):
                worker._fit_capture(client, (0, 0), pending=True)

    def test_start_offset_shifts_audio_actions_and_subtitles_by_the_same_frame_count(self):
        original = self.timeline()
        shifted = shifted_timeline(original, 3)
        offset = 3 * original.frames_per_tick
        self.assertEqual(shifted.total_frames, original.total_frames + offset)
        self.assertEqual(original.cues[0].action_frame, 0)
        for before, after in zip(original.cues, shifted.cues):
            self.assertEqual(after.action_frame - before.action_frame, offset)
            self.assertEqual(after.audio_frame - before.audio_frame, offset)
        captions = build_video_captions([{'page': page, 'text': '讲稿'} for page in (1, 1, 2)], shifted)
        self.assertEqual(captions[0].start_ms, 600)
        reader = TimelineAudio(shifted)
        try:
            self.assertEqual(reader.read(0, shifted.cues[0].audio_frame),
                             b'\0\0' * shifted.cues[0].audio_frame)
        finally:
            reader.close()

    def test_capture_actions_follow_recorded_frames_and_preserve_every_click(self):
        timeline = self.timeline()
        worker = _CaptureWorker(timeline, VideoRecordingOptions(), Path('obs64.exe'), self.root,
                                 {}, 0, (16, 9), None)
        frames = iter((100, 101, 116, 146, 176, 206, 221))
        client = Mock()
        def call(kind, **_kwargs):
            if kind == 'GetRecordStatus':
                return {'outputActive': True, 'outputPaused': False, 'outputDuration': 999999}
            if kind == 'GetStats':
                return {'renderTotalFrames': next(frames)}
            if kind == 'StopRecord':
                return {'outputPath': str(self.root / 'capture.mkv')}
            return {}
        client.call.side_effect = call
        with patch.object(worker, '_action') as action:
            worker._record(client)
        self.assertEqual([entry.args[0] for entry in action.call_args_list if entry.args[0] >= 0], [0, 1, 2])
        self.assertEqual(worker.timeline.cues[0].action_frame, timeline.frames_per_tick)

    def test_paused_recording_is_reported_instead_of_producing_unsynchronised_output(self):
        worker = _CaptureWorker(self.timeline(), VideoRecordingOptions(), Path('obs64.exe'), self.root,
                                 {}, 0, (16, 9), None)
        client = Mock()
        client.call.side_effect = lambda kind, **_: (
            {'outputActive': True, 'outputPaused': True, 'outputDuration': 33} if kind == 'GetRecordStatus'
            else {'renderTotalFrames': 100 if client.call.call_count == 3 else 102})
        with self.assertRaisesRegex(RuntimeError, '中断'):
            worker._record(client)

    def fake_install(self):
        source = self.root / 'installed OBS'
        for name in ('bin/64bit', 'data/obs-studio', 'obs-plugins/64bit', 'config'):
            (source / name).mkdir(parents=True, exist_ok=True)
        executable = source / 'bin/64bit/obs64.exe'
        executable.write_bytes(b'program')
        (source / 'config/private.ini').write_text('user configuration')
        (source / 'data/obs-studio/services.json').write_text('installed services')
        return executable

    def test_runtime_uses_its_own_config_and_never_copies_user_profiles(self):
        executable = self.fake_install()
        workspace = self.root / 'work'
        runtime = ObsRuntime(executable, workspace, VideoRecordingOptions(), lambda: False)
        self.addCleanup(lambda: self.assertRaises(RuntimeError, runtime.close))
        process = Mock(pid=123)
        process.poll.return_value = 1
        with patch('app.video.obs_runtime._encoder', return_value='x264'), \
                patch('app.video.obs_runtime.subprocess.Popen', return_value=process) as start, \
                self.assertRaisesRegex(RuntimeError, '未能启动'):
            runtime.start()
        self.assertIn('--portable', start.call_args.args[0])
        self.assertNotIn('QT_QPA_PLATFORM', start.call_args.kwargs['env'])
        config = runtime.runtime / 'config/obs-studio'
        self.assertIn('ConfirmOnExit=false', (config / 'global.ini').read_text())
        self.assertFalse((runtime.runtime / 'config/private.ini').exists())
        self.assertFalse((workspace / 'runtime').exists())
        self.assertEqual((executable.parents[2] / 'config/private.ini').read_text(), 'user configuration')
        (runtime.runtime / 'data/obs-studio/services.json').write_text('updated services')
        self.assertEqual((executable.parents[2] / 'data/obs-studio/services.json').read_text(), 'installed services')
        self.assertIn('RecFormat2=fragmented_mp4', (config / 'basic/profiles/PowerPointReviewer/basic.ini').read_text())
        profile = (config / 'basic/profiles/PowerPointReviewer/basic.ini').read_text()
        self.assertIn('RecQuality=Small', profile)
        self.assertIn('RecTracks=1', profile)
        websocket = json.loads((config / 'plugin_config/obs-websocket/config.json').read_text())
        self.assertTrue(websocket['auth_required'])
        self.assertGreaterEqual(len(websocket['server_password']), 32)
        scenes = json.loads((config / 'basic/scenes/PowerPointReviewer.json').read_text())
        self.assertTrue(all(item is None
                            for name, item in scenes.items() if name.startswith(('DesktopAudioDevice', 'AuxAudioDevice'))))

    def test_cancel_during_preparation_never_launches_obs(self):
        runtime = ObsRuntime(self.fake_install(), self.root / 'work', VideoRecordingOptions(), lambda: True)
        with patch('app.video.obs_runtime.subprocess.Popen') as start, self.assertRaises(MuxCancelled):
            runtime.start()
        start.assert_not_called()

    def test_runtime_path_is_reused_and_only_config_is_removed_after_each_export(self):
        executable = self.fake_install()
        first = ObsRuntime(executable, self.root / 'first-export', VideoRecordingOptions(), lambda: False)
        self.addCleanup(first.close)
        first._prepare_runtime()
        binary = first.runtime / 'bin/64bit/obs64.exe'
        timestamp = binary.stat().st_mtime_ns
        private = first.runtime / 'config/obs-studio/private.json'
        private.parent.mkdir(parents=True)
        private.write_text('temporary recording settings')
        first.close()
        self.assertTrue(binary.is_file())
        self.assertFalse(private.exists())
        second = ObsRuntime(executable, self.root / 'second-export', VideoRecordingOptions(), lambda: False)
        self.addCleanup(second.close)
        with patch('app.video.obs_runtime.os.link', side_effect=AssertionError('unexpected copy')):
            second._prepare_runtime()
        self.assertEqual(first.runtime, second.runtime)
        self.assertEqual(binary.stat().st_mtime_ns, timestamp)

    def test_concurrent_recording_cannot_overwrite_active_obs_config(self):
        executable = self.fake_install()
        first = ObsRuntime(executable, self.root / 'first', VideoRecordingOptions(), lambda: False)
        second = ObsRuntime(executable, self.root / 'second', VideoRecordingOptions(), lambda: False)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first._prepare_runtime()
        private = first.runtime / 'config/in-use.json'
        private.parent.mkdir()
        private.write_text('active recording')
        with self.assertRaisesRegex(RuntimeError, '另一次'):
            second.start()
        second.close()
        self.assertEqual(private.read_text(), 'active recording')

    def test_installation_update_rebuilds_cache_without_changing_program_location(self):
        executable = self.fake_install()
        first = ObsRuntime(executable, self.root / 'first', VideoRecordingOptions(), lambda: False)
        self.addCleanup(first.close)
        first._prepare_runtime()
        old = first.runtime / 'obs-plugins/64bit/obsolete.dll'
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b'obsolete')
        first.close()
        executable.write_bytes(b'updated program')
        second = ObsRuntime(executable, self.root / 'second', VideoRecordingOptions(), lambda: False)
        self.addCleanup(second.close)
        second._prepare_runtime()
        self.assertEqual(first.runtime, second.runtime)
        self.assertEqual((second.runtime / 'bin/64bit/obs64.exe').read_bytes(), b'updated program')
        self.assertFalse(old.exists())

    def test_obs_path_validates_the_complete_installation(self):
        executable = self.fake_install()
        self.assertEqual(find_obs(str(executable)), executable.resolve())
        self.assertIsNone(find_obs(str(self.root / 'missing/obs64.exe')))

    def test_close_only_targets_the_process_started_for_this_export(self):
        runtime = ObsRuntime(Path('obs64.exe'), self.root, VideoRecordingOptions(), lambda: False)
        runtime.process = Mock(pid=123)
        runtime.process.poll.return_value = None
        def windows(callback, argument):
            for hwnd in range(1, 8):
                callback(hwnd, argument)
        with patch('win32gui.EnumWindows', side_effect=windows), \
                patch('win32process.GetWindowThreadProcessId', side_effect=lambda hwnd: (1, 999 if hwnd == 1 else 123)), \
                patch('win32gui.GetClassName', side_effect=lambda hwnd: {
                    2: 'Qt683QWindowIcon', 3: 'Qt683TrayIconMessageWindowClass',
                    4: 'Qt683QWindowIcon', 5: 'Qt683ScreenChangeObserverWindow',
                    6: 'GDI+ Hook Window Class', 7: 'SoPY_Status'}[hwnd]), \
                patch('win32gui.GetWindowText', side_effect=lambda hwnd: {
                    2: 'OBS 31.1.2 - 便携模式', 4: 'WebSocket 服务器设置'}[hwnd]), \
                patch('win32gui.PostMessage') as post:
            runtime.close()
        post.assert_called_once_with(2, 0x0010, 0, 0)
        runtime.process.terminate.assert_not_called()

    def test_abnormal_exit_is_reported_and_logs_survive_private_config_cleanup(self):
        runtime = ObsRuntime(self.fake_install(), self.root / 'work', VideoRecordingOptions(), lambda: False)
        runtime._prepare_runtime()
        config = runtime.runtime / 'config/obs-studio'
        for kind in ('logs', 'crashes'):
            (config / kind).mkdir(parents=True)
            (config / kind / 'sample.txt').write_text(kind)
        (config / 'private.json').write_text('private password')
        runtime.process = Mock(pid=123)
        runtime.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, '未正常退出'):
            runtime.close()
        self.assertFalse((runtime.runtime / 'config').exists())
        self.assertIsNone(runtime.lock)
        self.assertFalse(runtime.runtime.with_suffix('.lock').exists())
        saved = next((runtime.runtime / 'diagnostics').glob('run-*'))
        self.assertEqual((saved / 'logs-sample.txt').read_text(), 'logs')
        self.assertEqual((saved / 'crashes-sample.txt').read_text(), 'crashes')
        self.assertEqual(json.loads((saved / 'exit.json').read_text())['exit_code'], 1)
        self.assertFalse((saved / 'private.json').exists())
        runtime.process.terminate.assert_not_called()

    def test_exit_timeout_terminates_only_owned_process_and_is_not_reported_as_success(self):
        runtime = ObsRuntime(Path('obs64.exe'), self.root, VideoRecordingOptions(), lambda: False)
        runtime.process = Mock(pid=123)
        runtime.process.poll.return_value = None
        runtime.process.wait.side_effect = [subprocess.TimeoutExpired('obs', 15), 1]
        runtime.process.terminate.side_effect = lambda: setattr(runtime.process.poll, 'return_value', 1)
        with patch('win32gui.EnumWindows'), self.assertLogs('app.video.obs_runtime', level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, '未正常退出'):
                runtime.close()
        runtime.process.terminate.assert_called_once()
        self.assertTrue(runtime.forced_shutdown)
        self.assertEqual(runtime.exit_code, 1)

    def test_diagnostic_history_is_bounded_and_normal_exit_is_recorded(self):
        executable = self.fake_install()
        for pid in range(4):
            runtime = ObsRuntime(executable, self.root / 'work', VideoRecordingOptions(), lambda: False)
            runtime._prepare_runtime()
            runtime.process = Mock(pid=pid)
            runtime.process.poll.return_value = 0
            runtime.close()
        saved = list((runtime.runtime / 'diagnostics').glob('run-*'))
        self.assertEqual(len(saved), 3)
        results = [json.loads((path / 'exit.json').read_text()) for path in saved]
        self.assertEqual(sorted(result['pid'] for result in results), [1, 2, 3])
        self.assertTrue(all(result['exit_code'] == 0 and not result['forced_shutdown'] for result in results))

    def test_process_job_owns_only_the_created_obs_and_is_closed_after_shutdown_errors(self):
        import win32job
        runtime = ObsRuntime(Path('obs64.exe'), self.root, VideoRecordingOptions(), lambda: False)
        runtime.process = Mock(pid=123)
        runtime.process.poll.return_value = None
        job, process = Mock(), Mock()
        limits = {'BasicLimitInformation': {'LimitFlags': 0}}
        with patch('win32job.CreateJobObject', return_value=job), \
                patch('win32job.QueryInformationJobObject', return_value=limits), \
                patch('win32job.SetInformationJobObject'), \
                patch('win32api.OpenProcess', return_value=process), \
                patch('win32job.AssignProcessToJobObject') as assign:
            runtime._own_process()
        assign.assert_called_once_with(job, process)
        process.Close.assert_called_once()
        self.assertTrue(limits['BasicLimitInformation']['LimitFlags'] & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        runtime.process.poll.return_value = 0
        with patch.object(runtime, '_stop_process', side_effect=RuntimeError('shutdown failed')):
            with self.assertRaisesRegex(RuntimeError, 'shutdown failed'):
                runtime.close()
        job.Close.assert_called_once()

    def test_recording_error_retains_the_video_returned_by_stop(self):
        path = self.root / 'capture/recording.mp4'
        path.parent.mkdir()
        path.write_bytes(b'partial video')
        client = Mock()
        # 停止请求返回后，编码仍有一个周期未结束。
        active_states = iter((True, True, False))
        client.call.side_effect = lambda kind, **_: (
            {'outputActive': next(active_states)} if kind == 'GetRecordStatus' else {'outputPath': str(path)})
        runtime = Mock(log_directory=self.root / 'logs')
        runtime.start.return_value = client
        stopped = []
        runtime.close.side_effect = lambda: stopped.append(next(active_states, 'stopped'))
        worker = _CaptureWorker(self.timeline(), VideoRecordingOptions(), Path('obs64.exe'), self.root,
                                 {}, 0, (16, 9), None)
        with patch('app.video.obs.ObsRuntime', return_value=runtime), \
                patch.object(worker, '_bind'), \
                patch.object(worker, '_record', side_effect=RuntimeError('window minimised')):
            with self.assertLogs('app.video.obs', level='ERROR'):
                worker.run()
        self.assertEqual(worker.error, 'window minimised')
        self.assertEqual(worker.raw, path)
        runtime.close.assert_called_once()
        self.assertEqual(stopped, ['stopped'])

    def test_disappearing_window_does_not_abort_process_cleanup(self):
        import pywintypes
        runtime = ObsRuntime(Path('obs64.exe'), self.root, VideoRecordingOptions(), lambda: False)
        runtime.process = Mock(pid=123)
        runtime.process.poll.return_value = None
        with patch('win32gui.EnumWindows', side_effect=lambda callback, arg: callback(1, arg)), \
                patch('win32process.GetWindowThreadProcessId', return_value=(1, 123)), \
                patch('win32gui.GetClassName', return_value='Qt683QWindowIcon'), \
                patch('win32gui.GetWindowText', return_value='OBS 31.1.2'), \
                patch('win32gui.PostMessage', side_effect=pywintypes.error(1400, 'PostMessage', 'gone')):
            runtime.close()
        runtime.process.wait.assert_called_once_with(timeout=15)

    def test_packaged_process_restores_its_library_path_after_launch_failure(self):
        with patch('app.video.obs_runtime.sys.frozen', True, create=True), \
                patch('win32api.GetDllDirectory', return_value='our bundle'), \
                patch('win32api.SetDllDirectory') as directory:
            with self.assertRaises(OSError), _external_libraries():
                raise OSError('launch failed')
        self.assertEqual([call.args for call in directory.call_args_list], [(None,), ('our bundle',)])

    def test_output_is_not_read_while_the_mux_process_still_holds_it(self):
        path = self.root / 'recording.mkv'
        path.write_bytes(b'video')
        handle = Mock()
        with patch('win32file.CreateFile', side_effect=[OSError('still writing'), handle]) as read:
            wait_saved(path, lambda: False)
        self.assertEqual(read.call_count, 2)
        handle.Close.assert_called_once()

    def test_cancel_waits_for_worker_to_finish_before_removing_files(self):
        recording = ObsVideoRecording(self.timeline(), self.root / 'out.mp4', Mock(),
                                       options=VideoRecordingOptions())
        recording.directory = recording.workspace = self.root / '.ppt-obs-test'
        recording.workspace.mkdir()
        folder = recording.workspace
        recording.worker = Mock(was_cancelled=True)
        recording.state = 'recording'
        cancelled = QSignalSpy(recording.cancelled)
        recording.cancel()
        recording.worker.cancel.assert_called_once()
        self.assertTrue(folder.is_dir())
        recording._captured()
        self.assertFalse(folder.exists())
        self.assertEqual(cancelled.count(), 1)

    def test_completed_video_and_narration_survive_a_join_failure(self):
        recording = ObsVideoRecording(self.timeline(), self.root / 'out.mp4', Mock(),
                                       options=VideoRecordingOptions())
        recording.directory = recording.workspace = self.root / '.ppt-obs-test'
        recording.workspace.mkdir()
        raw = recording.workspace / 'recording.mkv'
        raw.write_bytes(b'finished recording')
        (recording.workspace / 'narration.m4a').write_bytes(b'finished narration')
        recording.worker = Mock(raw=raw)
        recording.state = 'saving'
        failed = QSignalSpy(recording.failed)
        recording._finish('saving failed')
        recovery = next(self.root.glob('PPT-video-recovery-*'))
        self.assertEqual((recovery / 'recording.mkv').read_bytes(), b'finished recording')
        self.assertEqual((recovery / 'narration.m4a').read_bytes(), b'finished narration')
        self.assertFalse(recording.workspace.exists())
        self.assertEqual(failed.count(), 1)
        self.assertIn(str(recovery), failed.at(0)[0])

    def test_saved_backend_and_custom_location_survive_reset_and_invalid_config(self):
        options = VideoRecordingOptions(obs_path='D:/OBS/bin/64bit/obs64.exe')
        self.assertEqual(VideoRecordingOptions.from_dict(options.to_dict()), options)
        self.assertEqual(VideoRecordingOptions.from_dict({'recording_backend': 'bad', 'obs_path': None}),
                         VideoRecordingOptions())

    def test_complete_recording_is_kept_when_presentation_restore_fails(self):
        recording = ObsVideoRecording(self.timeline(), self.root / 'out.mp4',
                                       Mock(restore=Mock(side_effect=RuntimeError('restore failed'))),
                                       options=VideoRecordingOptions())
        recording.directory = recording.workspace = self.root / '.ppt-obs-test'
        recording.workspace.mkdir()
        raw = recording.workspace / 'recording.mp4'
        raw.write_bytes(b'finished recording')
        recording.worker = Mock(raw=raw, error='', was_cancelled=False)
        recording.state = 'recording'
        failed = QSignalSpy(recording.failed)
        recording._captured()
        recovery = next(self.root.glob('PPT-video-recovery-*'))
        self.assertEqual((recovery / 'recording.mp4').read_bytes(), b'finished recording')
        self.assertEqual(failed.count(), 1)


class ObsDialogTests(QtTestCase):
    setUp = native_ui.NativeExportDialogTests.setUp
    tearDown = native_ui.NativeExportDialogTests.tearDown

    def test_obs_controls_only_appear_for_recording_and_keep_saved_selection(self):
        dialog = self.dialog
        self.assertTrue(dialog.obs_panel.isHidden())
        dialog._change_mode('native')
        self.assertTrue(dialog.obs_panel.isHidden())
        dialog._change_mode('obs')
        self.assertFalse(dialog.obs_panel.isHidden())
        self.assertEqual(dialog.windowTitle(), 'OBS 录制')
        self.assertFalse(hasattr(dialog, 'mode_selector'))
        self.assertFalse(hasattr(dialog, 'recording_backend_combo'))
        self.assertFalse(dialog.settings_panel.isHidden())
        self.assertFalse(hasattr(dialog, 'advanced_button'))
        self.assertIsNot(dialog.obs_panel.parent(), dialog.settings_panel)
        options = VideoRecordingOptions(obs_path='D:/OBS/bin/64bit/obs64.exe')
        with patch('ui.dialogs.video_recording_dialog.find_obs', return_value=Path(options.obs_path)):
            dialog._apply_options(options)
        self.assertEqual(dialog.selected_options(), options)
        dialog._set_busy(True)
        self.assertFalse(dialog.settings_panel.isEnabled())
        self.assertFalse(dialog.obs_path_button.isEnabled())
        dialog._set_busy(False)
        dialog._change_mode('native')
        self.assertTrue(dialog.obs_panel.isHidden())

    def test_missing_saved_obs_path_is_visible_and_prevents_starting(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        with patch('ui.dialogs.video_recording_dialog.find_obs', return_value=None):
            dialog._apply_options(VideoRecordingOptions(obs_path='Z:/removed/obs64.exe'))
        self.assertIn('位置失效', dialog.obs_status_badge.text())
        self.assertEqual(dialog.obs_path_button.text(), '重新选择')
        self.assertFalse(dialog.obs_path_button.isHidden())
        self.assertFalse(dialog.start_button.isEnabled())
        dialog._change_mode('native')
        self.assertTrue(dialog.start_button.isEnabled())

    def test_old_obs_version_is_disabled_with_an_update_explanation(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        with patch('ui.dialogs.video_recording_dialog.find_obs', return_value=Path('obs64.exe')), \
                patch('ui.dialogs.video_recording_dialog.check_obs_version', side_effect=RuntimeError('请更新 OBS Studio')):
            dialog._apply_options(VideoRecordingOptions())
        self.assertIn('需更新', dialog.obs_status_badge.text())
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertIn('请更新', dialog.start_button.toolTip())

    def test_preparation_is_not_displayed_as_already_recording(self):
        dialog = self.dialog
        dialog._set_busy(True)
        dialog._obs_phase('正在确认录制画面…')
        self.assertEqual(dialog.recording_badge.text(), '准备中')
        dialog._obs_phase('正在录制放映…')
        self.assertEqual(dialog.recording_badge.text(), '录制中')

    def test_queued_obs_updates_do_not_overwrite_cancelling_status(self):
        dialog = self.dialog
        dialog.closing = True
        dialog._set_status('正在取消视频导出…', '取消中')
        dialog._obs_phase('正在确认录制画面…')
        dialog._progress(1, 10, 1, 1)
        self.assertEqual(dialog.status.text(), '正在取消视频导出…')
        self.assertEqual(dialog.recording_badge.text(), '取消中')
        dialog.closing = False

    def test_obs_recorder_is_used_and_subtitles_follow_its_actual_start_offset(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        dialog.options = VideoRecordingOptions(embed_subtitles=True)
        dialog._set_busy(True)
        dialog.output = self.root / 'target.mp4'
        dialog.selected_presentation = Mock(slide_count=1)
        with patch('ui.dialogs.video_recording_dialog.ObsVideoRecording') as obs:
            dialog._start_live_recording([self.audio])
        obs.return_value.start.assert_called_once()
        original = obs.call_args.args[0]
        shifted = shifted_timeline(original, 3)
        dialog._obs_timeline_ready(shifted)
        self.assertEqual(dialog.captions[0].start_ms, 600)
        dialog._obs_phase('正在保存画面和配音…')
        self.assertTrue(dialog.busy_progress.isStarted())
        dialog._cleanup()
