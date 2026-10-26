"""软件优先级、原生导出能力回退以及用户设置的边界。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, PropertyMock, patch

from app.presentation.com import PROG_IDS, application_candidates, ordered_prog_ids
from app.video.powerpoint import VideoExportUnavailable, export_powerpoint_video
import test_export_progress as progress_fixture


class ProviderSelectionTests(unittest.TestCase):
    def test_explicit_preference_can_start_wps_before_reusing_powerpoint(self):
        app = object()
        candidates = application_candidates([(*PROG_IDS[0], app)], 'wps')
        self.assertEqual(candidates, [(*PROG_IDS[1], None), (*PROG_IDS[0], app)])
        self.assertEqual(application_candidates([(*PROG_IDS[1], app)], 'powerpoint'),
                         [(*PROG_IDS[0], None), (*PROG_IDS[1], app)])

    def test_auto_keeps_running_instances_and_multi_instance_order(self):
        first, second = object(), object()
        running = [(*PROG_IDS[1], first), (*PROG_IDS[1], second)]
        self.assertEqual(application_candidates(running), running + [(*PROG_IDS[0], None)])
        self.assertEqual(ordered_prog_ids('unknown'), PROG_IDS)

    def test_native_falls_back_only_for_unavailable_software_in_separate_workspaces(self):
        progress = Mock()
        with patch('app.video.powerpoint.running_applications', return_value=[]), \
                patch('app.video.powerpoint._export_presentation_video',
                      side_effect=[VideoExportUnavailable('unsupported'), 'video']) as render:
            self.assertEqual(export_powerpoint_video('source.pptx', NS(), Path('work'), NS(),
                                                   preference='wps', progress=progress), 'video')
        self.assertEqual([call.kwargs['prog_id'] for call in render.call_args_list],
                         ['Kwpp.Application', 'PowerPoint.Application'])
        self.assertNotEqual(render.call_args_list[0].args[2], render.call_args_list[1].args[2])
        self.assertIn('PowerPoint', progress.call_args.args[0])

    def test_document_errors_never_fall_back_and_hide_the_real_error(self):
        for error in (ValueError('page mismatch'), RuntimeError('timing mismatch')):
            with patch('app.video.powerpoint.running_applications', return_value=[]), \
                    patch('app.video.powerpoint._export_presentation_video', side_effect=error) as render:
                with self.assertRaisesRegex(type(error), str(error)):
                    export_powerpoint_video('source.pptx', NS(), Path('work'), NS(), preference='wps')
                render.assert_called_once()

    def test_multiple_instances_do_not_retry_the_same_native_provider(self):
        running = [(*PROG_IDS[1], object()), (*PROG_IDS[1], object())]
        with patch('app.video.powerpoint.running_applications', return_value=running), \
                patch('app.video.powerpoint._export_presentation_video',
                      side_effect=[VideoExportUnavailable('unsupported'), 'video']) as render:
            self.assertEqual(export_powerpoint_video('source.pptx', NS(), Path('work'), NS()), 'video')
        self.assertEqual([call.kwargs['prog_id'] for call in render.call_args_list],
                         ['Kwpp.Application', 'PowerPoint.Application'])

    def test_unavailable_export_reports_both_software_results(self):
        with patch('app.video.powerpoint.running_applications', return_value=[]), \
                patch('app.video.powerpoint._export_presentation_video',
                      side_effect=VideoExportUnavailable('not supported')):
            with self.assertRaisesRegex(RuntimeError, 'OBS 录制') as error:
                export_powerpoint_video('source.pptx', NS(), Path('work'), NS(), preference='wps')
        self.assertIn('WPS', str(error.exception))
        self.assertIn('PowerPoint', str(error.exception))

    def test_wps_capability_failure_closes_probe_before_opening_the_source_copy(self):
        from app.video.powerpoint import _export_presentation_video
        app, capability = Mock(), Mock()
        app.AutomationSecurity, app.DisplayAlerts = 2, 2
        app.Presentations.Add.return_value = capability
        app.Presentations.Count = 0
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pptx'
            source.write_bytes(b'original presentation')
            with patch('win32com.client.DispatchEx', return_value=app), \
                    patch('win32com.client.dynamic.Dispatch', side_effect=lambda value: value), \
                    patch('win32process.EnumProcesses', return_value=[123]), \
                    patch('app.video.powerpoint.application_pid', return_value=123), \
                    patch('app.video.powerpoint._require_video_export', return_value='WPS 演示'), \
                    patch('app.video.powerpoint.ensure_native_media_available'), \
                    patch('app.video.powerpoint._render_video', side_effect=VideoExportUnavailable('unsupported')):
                with self.assertRaises(VideoExportUnavailable):
                    _export_presentation_video(source, NS(cues=[NS(page=1)], duration=2),
                                               Path(folder) / 'work', NS(height=720, fps=30),
                                               prog_id='Kwpp.Application', provider='WPS 演示')
            self.assertEqual(source.read_bytes(), b'original presentation')
        capability.Close.assert_called_once()
        app.Presentations.Open.assert_not_called()
        app.Quit.assert_not_called()
        self.assertEqual((app.AutomationSecurity, app.DisplayAlerts), (2, 2))


class WpsRenderTests(unittest.TestCase):
    setUp = progress_fixture.RenderProgressTests.setUp
    tearDown = progress_fixture.RenderProgressTests.tearDown
    render = progress_fixture.RenderProgressTests.render

    def test_wps_noop_interface_fails_quickly_instead_of_waiting_the_full_export_timeout(self):
        type(self.presentation).CreateVideoStatus = PropertyMock(return_value=0)
        with self.assertRaises(VideoExportUnavailable):
            self.render(provider='WPS 演示')
        self.monitor.start.assert_not_called()

    def test_wps_with_a_working_interface_uses_the_normal_picture_pipeline(self):
        self.assertEqual(self.render(provider='WPS 演示'), 'picture-info')


if __name__ == '__main__':
    unittest.main()
