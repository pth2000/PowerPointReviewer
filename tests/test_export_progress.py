"""实时进度降级、会话释放以及 PowerPoint 导出状态的边界。"""

import ctypes
import itertools
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, PropertyMock, patch

from app.video.export_progress import ExportProgress, ExportProgressMonitor, _Snapshot, powerpoint_process_id
from app.video.mux import MuxCancelled
from app.video.powerpoint import _render_video


class ProgressMonitorTests(unittest.TestCase):
    def setUp(self):
        self.library = Mock()
        def start(_pid, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(123)
            return 0
        self.library.mm_export_progress_start.side_effect = start
        self.library.mm_export_progress_stop.return_value = 0
        self.snapshot = _Snapshot(1000000, 1, 0, 0, 1)
        def poll(_handle, output):
            ctypes.memmove(output, ctypes.byref(self.snapshot), ctypes.sizeof(self.snapshot))
            return 0
        self.library.mm_export_progress_poll.side_effect = poll
        self.patch = patch('app.video.export_progress._library', return_value=self.library)
        self.patch.start()
        self.monitor = ExportProgressMonitor(1234, 10000000)
        self.monitor.start()

    def tearDown(self):
        self.monitor.close()
        self.patch.stop()

    def test_video_fraction_and_duplicate_close(self):
        self.assertEqual(self.monitor.read(), ExportProgress(.1))
        self.snapshot.position_us = 10000000
        self.assertEqual(self.monitor.read().fraction, 1.)
        self.monitor.close()
        self.monitor.close()
        self.library.mm_export_progress_stop.assert_called_once()

    def test_completed_picture_is_saving_not_export_completion(self):
        self.snapshot.phase = 2
        self.assertEqual(self.monitor.read(), ExportProgress(saving=True))

    def test_stale_and_unknown_signals_use_waiting_animation(self):
        self.snapshot.idle_ms = 5001
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.snapshot.idle_ms = 0
        self.assertEqual(self.monitor.read().fraction, .1)
        self.snapshot.video_jobs = 2
        self.assertEqual(self.monitor.read(), ExportProgress())

    def test_ambiguous_or_failed_observer_stops_without_failing_export(self):
        self.snapshot.phase = 3
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.library.mm_export_progress_stop.assert_called_once()
        self.assertEqual(self.monitor.read(), ExportProgress())

    def test_no_events_and_out_of_range_time_stop_observer(self):
        self.snapshot.position_us = 10200001
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.library.mm_export_progress_stop.assert_called_once()
        self.monitor.start()
        self.snapshot.phase, self.snapshot.idle_ms = 0, 10001
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.assertEqual(self.library.mm_export_progress_stop.call_count, 2)

    def test_native_api_errors_and_missing_symbols_are_optional(self):
        self.library.mm_export_progress_poll.side_effect = OSError('unavailable')
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.library.mm_export_progress_stop.assert_called_once()
        with patch('app.video.export_progress._library', side_effect=AttributeError('old DLL')):
            monitor = ExportProgressMonitor(1234, 10)
            monitor.start()
            self.assertEqual(monitor.read(), ExportProgress())
            monitor.close()

    def test_access_denied_does_not_create_a_fake_handle(self):
        self.monitor.close()
        self.library.mm_export_progress_start.side_effect = None
        self.library.mm_export_progress_start.return_value = 5
        self.monitor.start()
        self.assertEqual(self.monitor.read(), ExportProgress())
        self.library.mm_export_progress_stop.assert_called_once()

    def test_hidden_powerpoint_process_is_associated_only_when_unambiguous(self):
        with patch('app.video.export_progress.application_pid', return_value=None), \
                patch('win32process.EnumProcesses', return_value=[1,2]), \
                patch('app.presentation.window_target._process_name',
                      side_effect=lambda pid: 'POWERPNT.EXE' if pid==2 else 'other.exe'):
            self.assertEqual(powerpoint_process_id(Mock()), 2)
        with patch('app.video.export_progress.application_pid', return_value=None), \
                patch('win32process.EnumProcesses', return_value=[1,2]), \
                patch('app.presentation.window_target._process_name', return_value='POWERPNT.EXE'):
            self.assertIsNone(powerpoint_process_id(Mock()))


class RenderProgressTests(unittest.TestCase):
    def setUp(self):
        self.presentation = Mock()
        type(self.presentation).CreateVideoStatus = PropertyMock(side_effect=[1,1,3])
        self.options = NS(height=1080, fps=30)
        self.patch = patch('app.video.powerpoint.ExportProgressMonitor')
        self.monitor = self.patch.start().return_value
        self.monitor.read.side_effect = [ExportProgress(.3),ExportProgress(saving=True)]
        self.clock = patch('app.video.powerpoint.time.monotonic', side_effect=itertools.count(0,.3).__next__)
        self.clock.start()
        self.sleep = patch('app.video.powerpoint.time.sleep')
        self.sleep.start()
        self.probe = patch('app.video.powerpoint.probe_media', return_value='picture-info')
        self.probe.start()

    def tearDown(self):
        self.probe.stop()
        self.sleep.stop()
        self.clock.stop()
        self.patch.stop()

    def render(self, **kwargs):
        return _render_video(self.presentation,Path('picture.mp4'),self.options,100,
                             check_cancelled=kwargs.pop('check_cancelled',Mock()),**kwargs)

    def test_picture_progress_then_wait_for_powerpoint_status(self):
        progress = Mock()
        result = self.render(progress=progress,pid=1234,duration_us=10000000)
        self.assertEqual(result,'picture-info')
        self.assertEqual(progress.call_args_list[0].args,('正在生成视频画面…',30.0))
        self.assertEqual(progress.call_args_list[1].args,('正在保存视频画面…',-1))
        self.monitor.start.assert_called_once()
        self.monitor.close.assert_called_once()

    def test_calibration_does_not_start_observer(self):
        self.render()
        self.monitor.start.assert_not_called()
        self.monitor.close.assert_not_called()

    def test_estimate_uses_current_raw_picture_progress_and_clears_when_saving(self):
        from app.video.eta import RemainingTime
        estimate = Mock()
        with patch('app.video.powerpoint.RemainingTimeEstimator') as factory:
            factory.return_value.observe.return_value = RemainingTime(100, 170)
            self.render(progress=Mock(), pid=1234, duration_us=10000000, estimate=estimate)
            self.assertEqual(factory.return_value.observe.call_args.args[0], .3)
            factory.return_value.reset.assert_called_once()
        self.assertEqual([call.args[0] for call in estimate.call_args_list],
                         ['预计剩余约 3 分钟', ''])

    def test_lost_progress_clears_estimate_and_restarts_measurement(self):
        type(self.presentation).CreateVideoStatus = PropertyMock(side_effect=[1, 1, 1, 3])
        self.monitor.read.side_effect = [ExportProgress(.3), ExportProgress(), ExportProgress(.4)]
        estimate = Mock()
        with patch('app.video.powerpoint.RemainingTimeEstimator') as factory:
            factory.return_value.observe.return_value = None
            self.render(progress=Mock(), pid=1234, duration_us=10000000, estimate=estimate)
            factory.return_value.reset.assert_called_once()
        self.assertEqual([call.args[0] for call in estimate.call_args_list],
                         ['正在估算时间…', '', '正在估算时间…', ''])

    def test_estimate_callback_failure_cannot_fail_video_export(self):
        estimate = Mock(side_effect=ValueError('widget closed'))
        self.assertEqual(self.render(progress=Mock(), pid=1234, duration_us=10000000,
                                     estimate=estimate), 'picture-info')
        self.monitor.close.assert_called_once()

    def test_picture_completion_does_not_display_zero_seconds(self):
        type(self.presentation).CreateVideoStatus = PropertyMock(side_effect=[1, 3])
        self.monitor.read.side_effect = [ExportProgress(1.)]
        estimate, progress = Mock(), Mock()
        self.render(progress=progress, pid=1234, duration_us=10000000, estimate=estimate)
        estimate.assert_called_once_with('')
        self.assertLess(progress.call_args.args[1], 100)

    def test_unavailable_progress_still_completes_normal_export(self):
        self.monitor.read.side_effect = None
        self.monitor.read.return_value = ExportProgress()
        progress = Mock()
        self.assertEqual(self.render(progress=progress,pid=None,duration_us=10000000),'picture-info')
        self.assertTrue(all(call.args[1]==-1 for call in progress.call_args_list))
        self.monitor.close.assert_called_once()

    def test_cancel_failure_and_callback_error_all_release_observer(self):
        for behavior in ('cancel','failure','create-failed','callback-failed'):
            with self.subTest(behavior=behavior):
                self.monitor.reset_mock()
                self.monitor.read.side_effect = None
                self.monitor.read.return_value = ExportProgress(.5)
                self.presentation.CreateVideo.side_effect = None
                type(self.presentation).CreateVideoStatus = PropertyMock(return_value=1)
                check, progress = Mock(), Mock()
                if behavior=='cancel':
                    check.side_effect = [None,None,MuxCancelled()]
                elif behavior=='failure':
                    type(self.presentation).CreateVideoStatus = PropertyMock(return_value=4)
                elif behavior=='create-failed':
                    self.presentation.CreateVideo.side_effect = RuntimeError('CreateVideo failed')
                else:
                    progress.side_effect = ValueError('callback failed')
                with self.assertRaises((MuxCancelled,RuntimeError,ValueError)):
                    self.render(progress=progress,pid=1234,duration_us=10000000,check_cancelled=check)
                self.monitor.close.assert_called_once()

    def test_timeout_releases_observer(self):
        type(self.presentation).CreateVideoStatus = PropertyMock(return_value=1)
        self.monitor.read.side_effect = None
        self.monitor.read.return_value = ExportProgress()
        with self.assertRaisesRegex(RuntimeError,'长时间'):
            _render_video(self.presentation,Path('picture.mp4'),self.options,1,
                          check_cancelled=Mock(),pid=1234,duration_us=10000000)
        self.monitor.close.assert_called_once()


class NativeSessionTests(unittest.TestCase):
    def test_native_sessions_repeat_and_duplicate_start_preserves_live_observer(self):
        for _ in range(3):
            monitor = ExportProgressMonitor(os.getpid(),10000000)
            other = ExportProgressMonitor(os.getpid(),10000000)
            try:
                monitor.start()
                if not monitor._handle:
                    self.skipTest('Current Windows permissions do not allow ETW')
                other.start()
                self.assertFalse(other._handle)
                self.assertEqual(monitor.read(),ExportProgress())
            finally:
                other.close()
                monitor.close()
            self.assertFalse(monitor._handle)


if __name__=='__main__':
    unittest.main()
