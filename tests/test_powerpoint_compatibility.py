"""导出前识别旧版 PowerPoint，以及被静默降低的输出参数。"""

from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from app.video.native_media import MediaInfo
from app.video.powerpoint import (
    VideoExportUnavailable, _check_export_parameters, _check_powerpoint_version,
    _export_presentation_video,
)
from app.video.powerpoint_timing import SlideTiming
from app.video.settings import VideoRecordingOptions
from app.video.timeline import VideoTimeline


class PowerPointCompatibilityTests(unittest.TestCase):
    def test_older_and_unreadable_versions_receive_an_actionable_message(self):
        for version in ('12.0', '14.0', '15.0', '', 'unknown', None):
            with self.subTest(version=version), self.assertRaises(VideoExportUnavailable) as error:
                _check_powerpoint_version(NS(Version=version), VideoRecordingOptions())
            self.assertIn('OBS 录制', str(error.exception))
        for version in ('16.0', '16.0.19426', '17.0'):
            _check_powerpoint_version(NS(Version=version), VideoRecordingOptions())

    def test_4k_requires_windows_10_but_1080p_does_not(self):
        with patch('app.video.powerpoint.sys.getwindowsversion', return_value=NS(major=6)):
            with self.assertRaisesRegex(ValueError, 'Windows 10'):
                _check_powerpoint_version(NS(Version='16.0'), VideoRecordingOptions(3840, 2160))
            _check_powerpoint_version(NS(Version='16.0'), VideoRecordingOptions())

    def test_actual_resolution_is_checked_even_when_version_number_is_16(self):
        with self.assertRaisesRegex(ValueError, '2160p'):
            _check_export_parameters(MediaInfo(1000000, 30, 33000, 1920, 1080, 0),
                                     VideoRecordingOptions(3840, 2160))

    def test_frame_rate_check_allows_millisecond_rounding_and_rejects_fallback(self):
        for fps in (24, 25, 30, 50, 60):
            info = MediaInfo(1000000, fps, int(1000 / fps) * 1000, 1920, 1080, 0)
            _check_export_parameters(info, VideoRecordingOptions(fps=fps))
        for tick in (0, 33000):
            with self.subTest(tick=tick), self.assertRaisesRegex(ValueError, '60 fps'):
                _check_export_parameters(MediaInfo(1000000, 30, tick, 1920, 1080, 0),
                                         VideoRecordingOptions(fps=60))

    def test_unsupported_version_or_parameters_stop_before_full_render_and_close_copies(self):
        for version, options in (('15.0', VideoRecordingOptions()),
                                  ('16.0', VideoRecordingOptions(3840, 2160))):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, audio = root / 'source.pptx', root / 'audio.wav'
                source.write_bytes(b'original presentation')
                with wave.open(str(audio), 'wb') as writer:
                    writer.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                    writer.writeframes(b'\0\0' * 12000)
                timeline = VideoTimeline([1], [audio], lead_ms=0, tail_ms=0)
                app, document, probe = Mock(), Mock(), Mock()
                app.Version = version
                app.Presentations.Count = 0
                app.Presentations.Open.return_value = document
                app.Presentations.Add.return_value = probe
                document.Slides.Count = 1
                document.Slides.Item.return_value.TimeLine.MainSequence.Count = 0
                probe.Slides.Count = 1
                probe.Slides.Item.return_value.TimeLine.MainSequence.Count = 0
                probe.Slides.Item.return_value.Shapes.Count = 0
                with patch('app.video.powerpoint.ensure_native_media_available'), \
                     patch('win32com.client.DispatchEx', return_value=app), \
                     patch('win32com.client.dynamic.Dispatch', return_value=app), \
                     patch('win32process.EnumProcesses', return_value=[1]), \
                     patch('app.video.powerpoint._require_video_export', return_value='PowerPoint'), \
                     patch('app.video.powerpoint.powerpoint_process_id', return_value=1001), \
                     patch('app.video.powerpoint.application_pid', return_value=1001), \
                     patch('app.video.powerpoint.sys.getwindowsversion', return_value=NS(major=10)), \
                     patch('app.video.powerpoint._remove_hidden_effects'), \
                     patch('app.video.powerpoint.scan_slide_timings', return_value=((SlideTiming(1, 0, ()),), [])), \
                     patch('app.video.powerpoint._render_video',
                           return_value=MediaInfo(264000, 8, 33000, 1920, 1080, 0)) as render:
                    with self.assertRaisesRegex((VideoExportUnavailable, ValueError),
                                                '2016' if version == '15.0' else '2160p'):
                        _export_presentation_video(source, timeline, root / 'export', options)
                app.Quit.assert_called_once()
                self.assertEqual(source.read_bytes(), b'original presentation')
                if version == '15.0':
                    app.Presentations.Open.assert_not_called()
                    render.assert_not_called()
                else:
                    render.assert_called_once()
                    self.assertEqual(render.call_args.args[1].name, 'calibration-one.mp4')
                    probe.Close.assert_called_once()
                    document.Close.assert_called_once()
