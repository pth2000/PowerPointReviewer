"""视频方式的独立设置、旧配置迁移与 OBS 原生画质选择。"""

from dataclasses import replace
import unittest

from app.settings_store import AppSettings
from app.video.settings import VideoRecordingOptions, load_video_options, save_video_options


class VideoOptionsTests(unittest.TestCase):
    def test_defaults_use_native_high_quality_preset_and_1080p30(self):
        options = VideoRecordingOptions()
        self.assertEqual((options.width, options.height, options.fps), (1920, 1080, 30))
        self.assertEqual(options.obs_quality, 'Small')
        self.assertTrue(options.windowed)

    def test_legacy_settings_seed_both_modes_without_reenabling_stream_quality(self):
        settings = AppSettings()
        legacy = {'width': 1280, 'height': 720, 'fps': 25, 'container': 'mkv',
                  'video_bit_rate': 12000000, 'obs_path': 'D:/OBS/obs64.exe'}
        settings.set('video_recording', legacy)
        for mode in ('native', 'obs'):
            options = load_video_options(settings, mode)
            self.assertEqual((options.width, options.height, options.fps), (1280, 720, 25))
            self.assertEqual(options.container, 'mkv')
            self.assertEqual(options.obs_path, legacy['obs_path'])
            self.assertEqual(options.video_bit_rate, 12000000)
            self.assertEqual(options.obs_quality, 'Small')

    def test_preferences_survive_reload_and_changes_never_overwrite_other_mode_or_legacy(self):
        settings = AppSettings()
        settings.set('video_recording', {'container': 'mkv'})
        native = VideoRecordingOptions(1280, 720, fps=25, container='mkv')
        obs = VideoRecordingOptions(obs_quality='HQ')
        save_video_options(settings, 'native', native)
        save_video_options(settings, 'obs', obs)
        settings.set('video_recording', {'width': 3840, 'height': 2160})
        restored = AppSettings()
        restored.load_from(settings.to_dict())
        self.assertEqual(load_video_options(restored, 'native'), native)
        self.assertEqual(load_video_options(restored, 'obs'), obs)
        save_video_options(restored, 'obs', replace(obs, fps=60))
        self.assertEqual(load_video_options(restored, 'native'), native)
        self.assertEqual(restored.get('video_recording'), {'width': 3840, 'height': 2160})

    def test_empty_mode_settings_override_legacy_and_invalid_values_fall_back_safely(self):
        settings = AppSettings()
        settings.set('video_recording', {'width': 3840, 'height': 2160})
        settings.set('video_recording_by_mode', {'obs': {}})
        self.assertEqual(load_video_options(settings, 'obs'), VideoRecordingOptions())
        for invalid in ('Lossless', 'unknown', None, [], {}):
            options = VideoRecordingOptions.from_dict({'obs_quality': invalid, 'wait_enabled': 'yes',
                                                       'wait_ms': -20})
            self.assertEqual(options, VideoRecordingOptions())

    def test_saving_one_settings_instance_does_not_mutate_shared_defaults(self):
        first, second = AppSettings(), AppSettings()
        save_video_options(first, 'obs', VideoRecordingOptions(obs_quality='HQ'))
        self.assertEqual(load_video_options(second, 'obs').obs_quality, 'Small')

    def test_removed_wait_preferences_are_ignored_and_no_longer_saved(self):
        options = VideoRecordingOptions.from_dict({'wait_enabled': True, 'wait_ms': 5000})
        self.assertEqual(options, VideoRecordingOptions())
        self.assertNotIn('wait_enabled', options.to_dict())
        self.assertNotIn('wait_ms', options.to_dict())

    def test_old_audio_bitrate_does_not_survive_loading_and_saving_either_mode(self):
        settings = AppSettings()
        settings.set('video_recording', {'audio_bit_rate': 256000, 'container': 'mkv'})
        settings.set('video_recording_by_mode', {'obs': {'audio_bit_rate': 96000, 'fps': 60}})
        for mode in ('native', 'obs'):
            options = load_video_options(settings, mode)
            save_video_options(settings, mode, options)
            self.assertNotIn('audio_bit_rate', settings.get('video_recording_by_mode')[mode])
        self.assertEqual(load_video_options(settings, 'native').container, 'mkv')
        self.assertEqual(load_video_options(settings, 'obs').fps, 60)

    def test_lossless_and_unknown_recording_modes_cannot_be_applied_directly(self):
        with self.assertRaises(ValueError):
            VideoRecordingOptions(obs_quality='Lossless')
        with self.assertRaises(ValueError):
            load_video_options(AppSettings(), 'retired')


if __name__ == '__main__':
    unittest.main()
