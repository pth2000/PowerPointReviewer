"""原生导出入口、模式切换以及线程退出后的文件提交与清理。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import wave
from zipfile import ZipFile

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtTest import QTest

from app.settings_store import AppSettings
from app.video.settings import RESOLUTIONS, VideoRecordingOptions, load_video_options
from qt_test_case import QtTestCase
from ui.dialogs.video_recording_dialog import VideoRecordingDialog


class NativeExportDialogTests(QtTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = self.root / '演示文稿.pptx'
        self.source.write_bytes(b'original presentation')
        self.audio = self.root / 'voice.wav'
        with wave.open(str(self.audio), 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(48000)
            writer.writeframes(b'\x00\x00' * 12000)
        settings = AppSettings()
        settings.set('video_recording', VideoRecordingOptions(
            1280, 720, container='mkv', embed_subtitles=True).to_dict())
        self.context = NS(app_settings=settings, config=Mock())
        self.list_shows = patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[])
        self.shows = self.list_shows.start()
        self.dialog = VideoRecordingDialog([{'page': 1, 'text': '当前采用的配音'}], [self.audio],
                                          self.source, ctx=self.context, initial_mode='native')
        self.loop = QEventLoop()
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.loop.quit)

    def tearDown(self):
        self.timeout.stop()
        task = self.dialog.native_export
        if task is not None and task.isRunning():
            task.cancel()
            task.wait(3000)
        self.qt.processEvents()
        self.dialog.reject()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.list_shows.stop()
        self.folder.cleanup()

    def test_native_mode_uses_saved_file_and_only_shows_applicable_options(self):
        dialog = self.dialog
        self.shows.assert_not_called()
        self.assertEqual(dialog.mode, 'native')
        self.assertEqual(dialog.slide_file_edit.text(), self.source.name)
        self.assertTrue(dialog.start_button.isEnabled())
        for widget in (dialog.live_source_panel, dialog.show_mode_panel,
                       dialog.video_bitrate_spin, dialog.video_bitrate_label):
            self.assertTrue(widget.isHidden())
        self.assertFalse(dialog.file_source_panel.isHidden())
        self.assertEqual(dialog.resolution_combo.currentText(), '720p')
        self.source.unlink()
        dialog._selection_changed()
        self.assertFalse(dialog.start_button.isEnabled())

    def test_media_notice_is_conditional_and_applies_to_both_export_methods(self):
        self.assertTrue(self.dialog.media_notice.isHidden())
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('ppt/media/image1.png', b'fixture')
        self.dialog._selection_changed()
        self.assertTrue(self.dialog.media_notice.isHidden())
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('ppt/media/video1.mp4', b'fixture')
        for mode in ('native', 'obs'):
            self.dialog._change_mode(mode)
            self.dialog._selection_changed()
            self.assertFalse(self.dialog.media_notice.isHidden(), mode)

    def test_video_save_uses_remembered_folder_and_cancel_preserves_preference(self):
        self.context.app_settings.set('export_dir', str(self.root / 'previous'))
        with patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                   return_value=('', '')) as choose, \
             patch('ui.dialogs.video_recording_dialog.ensure_native_media_available'):
            self.dialog.start()
        self.assertEqual(Path(choose.call_args.args[2]).parent, self.root / 'previous')
        self.assertEqual(self.context.app_settings.get('export_dir'), str(self.root / 'previous'))

    def test_success_exposes_video_and_folder_actions(self):
        self.dialog.output = self.root / 'result.mkv'
        staged = self.root / 'staged.mkv'
        staged.write_bytes(b'finished')
        self.dialog._save_completed(staged)
        self.assertFalse(self.dialog.open_button.isHidden())
        self.assertFalse(self.dialog.folder_button.isHidden())
        with patch('ui.dialogs.video_recording_dialog.QDesktopServices.openUrl') as open_url:
            self.dialog.folder_button.click()
        self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), self.root)

    def test_switching_modes_restores_recording_controls_and_retains_settings(self):
        dialog = self.dialog
        options = dialog.selected_options()
        dialog._change_mode('obs')
        self.shows.assert_called_once()
        for widget in (dialog.live_source_panel, dialog.show_mode_panel,
                       dialog.obs_quality_combo, dialog.obs_quality_label):
            self.assertFalse(widget.isHidden())
        self.assertEqual(dialog.obs_quality_combo.currentData(), 'Small')
        self.assertTrue(dialog.video_bitrate_spin.isHidden())
        dialog.obs_quality_combo.setCurrentIndex(dialog.obs_quality_combo.findData('Stream'))
        self.assertFalse(dialog.video_bitrate_spin.isHidden())
        self.assertEqual(dialog.resolution_combo.currentText(), RESOLUTIONS[0][0])
        dialog._change_mode('native')
        self.assertEqual(dialog.selected_options(), options)

    def test_obs_native_quality_presets_control_only_the_manual_bitrate_field(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        self.assertEqual(dialog.obs_quality_combo.count(), 3)
        for quality, custom in [('Small', False), ('HQ', False), ('Stream', True)]:
            dialog.obs_quality_combo.setCurrentIndex(dialog.obs_quality_combo.findData(quality))
            self.assertEqual(dialog.selected_options().obs_quality, quality)
            self.assertEqual(dialog.video_bitrate_spin.isHidden(), not custom)
        dialog._change_mode('native')
        self.assertTrue(dialog.obs_quality_combo.isHidden())
        self.assertTrue(dialog.video_bitrate_spin.isHidden())

    def test_modes_remember_output_settings_independently_on_close(self):
        dialog = self.dialog
        native = dialog.selected_options()
        dialog._change_mode('obs')
        obs = VideoRecordingOptions(1920, 1080, fps=60, obs_quality='HQ',
                                    container='mp4')
        dialog._apply_options(obs)
        dialog.reject()
        reopened = VideoRecordingDialog(dialog.notes, [self.audio], self.source,
                                        ctx=self.context, initial_mode='obs')
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened.selected_options(), obs)
        reopened._change_mode('native')
        self.assertEqual(reopened.selected_options(), native)
        reopened._change_mode('obs')
        self.assertEqual(reopened.selected_options(), obs)
        self.assertEqual(load_video_options(self.context.app_settings, 'obs'), obs)

    def test_reset_restores_only_current_mode_and_keeps_obs_program_location(self):
        dialog = self.dialog
        native = dialog.selected_options()
        dialog._remember_options()
        dialog._change_mode('obs')
        custom = VideoRecordingOptions(1280, 720, fps=60, obs_quality='Stream',
                                       obs_path='D:/OBS/bin/64bit/obs64.exe')
        with patch('ui.dialogs.video_recording_dialog.find_obs', return_value=None):
            dialog._apply_options(custom)
            dialog._reset_options()
        self.assertEqual(dialog.selected_options(), VideoRecordingOptions(obs_path=custom.obs_path))
        dialog._remember_options()
        self.assertEqual(load_video_options(self.context.app_settings, 'native'), native)
        dialog._change_mode('native')
        self.assertEqual(dialog.selected_options(), native)

    def test_indeterminate_progress_moves_and_returns_to_numeric_progress_without_resizing(self):
        dialog = self.dialog
        dialog.show()
        dialog._set_busy(True)
        dialog._native_progress('正在准备文稿…', 20)
        self.qt.processEvents()
        height = dialog.height()
        bar_height = dialog.progress.height()
        dialog._native_progress('正在生成视频画面…', -1)
        self.qt.processEvents()
        self.assertTrue(dialog.progress.isHidden())
        self.assertTrue(dialog.busy_progress.isVisible())
        self.assertTrue(dialog.busy_progress.isStarted())
        position = dialog.busy_progress.shortPos
        QTest.qWait(120)
        self.assertNotEqual(dialog.busy_progress.shortPos, position)
        self.assertEqual(dialog.busy_progress.height(), bar_height)
        self.assertEqual(dialog.height(), height)
        # 后续未提供实际处理比例的阶段继续使用等待动画。
        dialog._native_progress('正在合并画面和配音…', -1)
        self.assertTrue(dialog.busy_progress.isStarted())
        dialog._native_progress('正在准备讲稿配音…', 80)
        QTest.qWait(150)
        self.assertFalse(dialog.busy_progress.isStarted())
        self.assertTrue(dialog.busy_progress.isHidden())
        self.assertTrue(dialog.progress.isVisible())
        self.assertEqual(dialog.progress.value(), 8000)
        self.assertEqual(dialog.height(), height)
        dialog._set_busy(False)
        self.assertTrue(dialog.progress_stack.isHidden())
        self.assertFalse(dialog.busy_progress.isStarted())

    def test_real_picture_progress_switches_back_to_waiting_for_final_save(self):
        dialog = self.dialog
        dialog._set_busy(True)
        dialog._native_progress('正在生成视频画面…',-1)
        self.assertTrue(dialog.busy_progress.isStarted())
        for value in (31,45,60,74):
            dialog._native_progress('正在生成视频画面…',value)
            self.assertEqual(dialog.progress.value(),value * 100)
            self.assertFalse(dialog.busy_progress.isStarted())
        dialog._native_progress('正在保存视频画面…',-1)
        self.assertTrue(dialog.busy_progress.isStarted())
        dialog._native_progress('正在准备讲稿配音…',75)
        self.assertFalse(dialog.busy_progress.isStarted())
        self.assertEqual(dialog.progress.value(),7500)
        dialog._set_busy(False)
        self.assertTrue(dialog.progress_stack.isHidden())
        self.assertFalse(dialog.busy_progress.isStarted())

    def test_source_polling_only_runs_for_visible_idle_recording_dialog(self):
        dialog = self.dialog
        dialog.show()
        self.assertFalse(dialog.source_timer.isActive())
        dialog._change_mode('obs')
        self.assertTrue(dialog.source_timer.isActive())
        dialog._set_busy(True)
        self.assertFalse(dialog.source_timer.isActive())
        dialog._set_busy(False)
        self.assertTrue(dialog.source_timer.isActive())
        dialog.hide()
        self.assertFalse(dialog.source_timer.isActive())
        self.assertFalse(dialog.layout_timer.isActive())
        dialog.show()
        self.assertTrue(dialog.source_timer.isActive())
        dialog._change_mode('obs')
        self.assertTrue(dialog.source_timer.isActive())
        dialog._change_mode('native')
        self.assertFalse(dialog.source_timer.isActive())
        self.assertTrue(dialog.progress_stack.isHidden())
        self.assertFalse(dialog.busy_progress.isStarted())

    def test_explicit_native_entry_ignores_the_previous_obs_mode(self):
        self.context.app_settings.set('video_export_mode', 'obs')
        dialog = VideoRecordingDialog(self.dialog.notes, [self.audio], self.source,
                                      ctx=self.context, initial_mode='native')
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.mode, 'native')
        self.assertTrue(dialog.obs_panel.isHidden())

    def test_legacy_obs_preference_opens_the_independent_obs_window(self):
        self.context.app_settings.set('video_export_mode', 'recording')
        self.context.app_settings.set('video_recording', {'recording_backend': 'obs'})
        dialog = VideoRecordingDialog(self.dialog.notes, [self.audio], self.source, ctx=self.context)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.mode, 'obs')
        self.assertEqual(dialog.windowTitle(), 'OBS 录制')

    def test_retired_saved_modes_fall_back_to_native_and_keep_output_settings(self):
        for mode in ('recording', 'static', 'unknown'):
            with self.subTest(mode=mode):
                self.context.app_settings.set('video_export_mode', mode)
                saved = VideoRecordingOptions(1280, 720, container='mkv').to_dict()
                saved['recording_backend'] = 'qt'
                self.context.app_settings.set('video_recording', saved)
                dialog = VideoRecordingDialog(self.dialog.notes, [self.audio], self.source,
                                              ctx=self.context)
                self.addCleanup(dialog.deleteLater)
                self.assertEqual(dialog.mode, 'native')
                self.assertEqual(dialog.selected_options().width, 1280)
                self.assertEqual(dialog.selected_options().container, 'mkv')

    def test_retired_mode_cannot_be_selected_programmatically(self):
        for mode in ('recording', 'static'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.dialog._change_mode(mode)
            self.assertEqual(self.dialog.mode, 'native')

    def test_all_options_scroll_on_a_small_screen_without_hiding_actions(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        self.assertFalse(hasattr(dialog, 'advanced_button'))
        self.assertFalse(hasattr(dialog, 'wait_checkbox'))
        with patch.object(dialog, 'screen', return_value=NS(availableGeometry=lambda: QRect(0, 0, 800, 480))):
            dialog._resize_to_contents()
        dialog.show()
        self.qt.processEvents()
        self.assertTrue(dialog.settings_panel.isVisible())
        self.assertLessEqual(dialog.height(), 400)
        button_rect = QRect(dialog.start_button.mapTo(dialog, QPoint()), dialog.start_button.size())
        self.assertTrue(dialog.rect().contains(button_rect))
        self.assertGreater(dialog.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.scroll_area.horizontalScrollBar().maximum(), 0)

    def test_video_form_cannot_be_stretched_or_maximized(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        dialog.show()
        QTest.qWait(30)
        size = dialog.size()
        settings_height = dialog.settings_panel.height()
        dialog.resize(1600, 1000)
        QTest.qWait(30)
        self.assertEqual(dialog.size(), size)
        self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
        self.assertFalse(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
        self.assertEqual(dialog.settings_panel.height(), settings_height)
        self.assertEqual(dialog.width(), 700)
        self.assertEqual(dialog.show_mode_combo.width(), dialog.fps_combo.width())
        self.assertLess(dialog.fps_combo.width(), 400)
        self.assertEqual(dialog.scroll_area.horizontalScrollBar().maximum(), 0)

    def test_initial_recording_size_fits_all_fields_when_screen_has_room(self):
        dialog = self.dialog
        with patch.object(dialog, 'screen', return_value=NS(availableGeometry=lambda: QRect(0, 0, 1920, 1080))):
            dialog._change_mode('obs')
            dialog._resize_to_contents()
        dialog.show()
        QTest.qWait(30)
        self.assertEqual(dialog.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.scroll_area.horizontalScrollBar().maximum(), 0)

    def test_status_and_quality_updates_keep_fixed_window_size(self):
        dialog = self.dialog
        dialog._change_mode('obs')
        dialog.show()
        QTest.qWait(30)
        size = dialog.size()
        dialog.obs_quality_combo.setCurrentIndex(dialog.obs_quality_combo.findData('Stream'))
        dialog._set_busy(True)
        dialog._native_progress('正在生成视频画面…', 35)
        QTest.qWait(30)
        self.assertEqual(dialog.size(), size)
        dialog._set_busy(False)
        dialog._resize_to_contents()
        dialog.reload()
        QTest.qWait(30)
        self.assertEqual(dialog.size(), size)
        self.assertEqual(dialog.minimumSize(), dialog.maximumSize())

    def test_fractional_progress_moves_pixels_and_keeps_percentage_width_stable(self):
        dialog=self.dialog
        dialog.show()
        dialog._set_busy(True)
        dialog._native_progress('正在生成视频画面…',58.10)
        QTest.qWait(200)
        height,width=dialog.height(),dialog.progress_caption.width()
        first=dialog.progress.grab().toImage()
        self.assertEqual(dialog.progress.value(),5810)
        self.assertEqual(dialog.progress_caption.text(),'58.1%')
        dialog._native_progress('正在生成视频画面…',58.36)
        QTest.qWait(200)
        self.assertEqual(dialog.progress.value(),5836)
        self.assertEqual(dialog.progress_caption.text(),'58.4%')
        self.assertNotEqual(first,dialog.progress.grab().toImage())
        self.assertEqual(dialog.height(),height)
        self.assertEqual(dialog.progress_caption.width(),width)
        dialog._native_progress('正在保存视频画面…',-1)
        self.assertEqual(dialog.progress_caption.text(),'')
        self.assertTrue(dialog.busy_progress.isStarted())
        self.assertEqual(dialog.height(),height)
        dialog._native_progress('正在准备讲稿配音…',80.05)
        self.assertEqual(dialog.progress.value(),8005)
        self.assertFalse(dialog.busy_progress.isStarted())
        self.assertEqual(dialog.progress_caption.width(),width)
        dialog._set_busy(False)
        self.assertTrue(dialog.progress_caption.isHidden())

    def export(self, behavior='success', cancel=False, save_error=False):
        dialog = self.dialog
        destination = self.root / '已有视频.mkv'
        destination.write_bytes(b'previous video')
        observations = {}

        class Task(QThread):
            progress = Signal(str, int)
            estimated_time = Signal(str)
            stage_changed = Signal(str)

            def __init__(self, source, timeline, notes, target, options, parent, *, preference='auto'):
                super().__init__(parent)
                self.target = target
                self.error = ''
                self.was_cancelled = False
                observations.update(source=source, timeline=timeline, notes=notes, options=options,
                                    preference=preference)

            def cancel(self):
                self.requestInterruption()

            def run(self):
                self.target.write_bytes(b'complete video and subtitles')
                observations['old_file_protected'] = destination.read_bytes() == b'previous video'
                self.stage_changed.emit('rendering')
                self.progress.emit('正在生成视频画面…', -1)
                if behavior == 'cancel':
                    while not self.isInterruptionRequested():
                        self.msleep(5)
                    self.msleep(80)
                    observations['audio_exists_before_exit'] = observations['timeline'].cues[0].path.is_file()
                    self.was_cancelled = True
                elif behavior == 'error':
                    self.error = '第 1 页包含暂不支持的动画。'
                else:
                    self.progress.emit('视频已导出', 100)

        def on_progress(message, percent):
            if cancel and message == '正在生成视频画面…':
                self.assertTrue(dialog.progress.isHidden())
                self.assertFalse(dialog.busy_progress.isHidden())
                self.assertTrue(dialog.busy_progress.isStarted())
                dialog.reject()
                observations['cancel_waits_for_thread'] = dialog.busy and dialog.audio_directory is not None

        original_start = Task.start

        def start(task):
            task.progress.connect(on_progress)
            original_start(task)

        original_cleanup = dialog._cleanup

        def cleanup():
            original_cleanup()
            self.loop.quit()

        with patch('ui.dialogs.video_recording_dialog.PowerPointExportTask', Task), \
                patch.object(Task, 'start', start), \
                patch('ui.dialogs.video_recording_dialog.ensure_native_media_available') as media, \
                patch('ui.dialogs.video_recording_dialog.VideoMuxTask') as second_mux, \
                patch('ui.dialogs.video_recording_dialog.QFileDialog.getSaveFileName',
                      return_value=(str(destination), '')), \
                patch('ui.dialogs.video_recording_dialog.paths.TEMP_DIR', self.root), \
                patch.object(dialog, '_cleanup', side_effect=cleanup):
            if save_error:
                replacement = patch('pathlib.Path.replace', side_effect=PermissionError('文件被占用'))
            else:
                replacement = patch('ui.dialogs.video_recording_dialog.sys.platform', 'win32')
            with replacement:
                self.timeout.start(10000)
                QTimer.singleShot(0, dialog.start)
                self.loop.exec()
                self.timeout.stop()
            media.assert_called_once()
            second_mux.assert_not_called()
        self.assertFalse(dialog.busy, dialog.status.text())
        self.assertFalse(dialog.busy_progress.isStarted())
        self.assertTrue(dialog.busy_progress.isHidden())
        self.assertTrue(dialog.estimate_caption.isHidden())
        self.assertIsNone(dialog.native_export)
        self.assertIsNone(dialog.audio_directory)
        self.assertFalse(list(self.root.glob('video-audio-*')))
        self.assertEqual(self.source.read_bytes(), b'original presentation')
        return destination, observations

    def test_estimate_is_compact_and_clears_on_phase_changes_or_cleanup(self):
        dialog = self.dialog
        dialog.show()
        dialog._set_busy(True)
        dialog._native_stage('rendering')
        dialog._native_progress('正在生成视频画面…', 58.36)
        self.qt.processEvents()
        height = dialog.height()
        for text in ('正在估算时间…', '预计剩余约 3 分钟', '预计剩余约 40 秒'):
            dialog._native_estimate(text)
            self.qt.processEvents()
            self.assertTrue(dialog.estimate_caption.isVisible())
            self.assertEqual(dialog.estimate_caption.text(), text)
            self.assertEqual(dialog.height(), height)
            self.assertGreaterEqual(dialog.estimate_caption.width(),
                                    dialog.estimate_caption.fontMetrics().horizontalAdvance(text))
        for stage, message, value in (('picture_save', '正在保存视频画面…', -1),
                                      ('narration', '正在合成讲稿配音…', 0)):
            dialog._native_estimate('预计剩余约 3 分钟')
            dialog._native_stage(stage)
            dialog._native_progress(message, value)
            self.assertTrue(dialog.estimate_caption.isHidden())
        dialog._native_estimate('预计剩余约 3 分钟')
        dialog._set_busy(False)
        self.assertTrue(dialog.estimate_caption.isHidden())
        dialog._native_estimate('旧任务延迟送达的预估')
        self.assertTrue(dialog.estimate_caption.isHidden())
        dialog._set_busy(True)
        self.assertEqual(dialog.estimate_caption.text(), '')
        dialog._set_busy(False)
        dialog._change_mode('obs')
        dialog._set_busy(True)
        dialog._native_estimate('旧任务延迟送达的预估')
        self.assertTrue(dialog.estimate_caption.isHidden())
        dialog._set_busy(False)

    def test_each_native_stage_resets_progress_and_rendering_estimate_survives_75_percent(self):
        dialog = self.dialog
        dialog.show()
        dialog._set_busy(True)
        dialog._native_stage('preparing')
        dialog._native_progress('正在准备文稿…', 95)
        self.assertEqual(dialog.progress.value(), 9500)
        dialog._native_stage('rendering')
        self.assertEqual(dialog.progress.value(), 0)
        self.assertEqual(dialog.progress.getVal(), 0)
        dialog._native_progress('正在生成视频画面…', 2.5)
        self.assertEqual(dialog.progress.value(), 250)
        dialog._native_estimate('预计剩余约 3 分钟')
        dialog._native_stage('rendering')
        self.assertEqual(dialog.progress.value(), 250)
        dialog._native_progress('正在生成视频画面…', 90)
        self.assertTrue(dialog.estimate_caption.isVisible())
        dialog._native_stage('picture_save')
        self.assertTrue(dialog.busy_progress.isStarted())
        self.assertEqual(dialog.progress_caption.text(), '')
        self.assertTrue(dialog.estimate_caption.isHidden())
        dialog._native_stage('narration')
        self.assertEqual(dialog.progress.value(), 0)
        self.assertEqual(dialog.progress.getVal(), 0)
        self.assertEqual(dialog.progress_caption.text(), '0.0%')
        self.assertFalse(dialog.busy_progress.isStarted())
        dialog._native_progress('正在合成讲稿配音…', 75)
        self.assertEqual(dialog.recording_badge.text(), '合成配音')
        self.assertEqual(dialog.progress.value(), 7500)
        dialog._native_stage('joining')
        self.assertTrue(dialog.busy_progress.isStarted())
        dialog._native_stage('saving')
        self.assertEqual(dialog.progress.value(), 0)
        dialog._native_progress('正在保存视频…', 3.25)
        self.assertEqual(dialog.progress.value(), 325)
        dialog._set_busy(False)
        dialog._native_stage('rendering')
        self.assertIsNone(dialog._native_stage_name)

    def test_complete_export_commits_once_and_saves_native_mode(self):
        destination, result = self.export()
        self.assertTrue(result['old_file_protected'])
        self.assertEqual(destination.read_bytes(), b'complete video and subtitles')
        self.assertEqual(self.dialog.completed, str(destination))
        self.assertEqual(self.dialog.progress.maximum(), 100)
        self.assertEqual(self.dialog.progress.value(), 100)
        self.assertEqual(self.context.app_settings.get('video_export_mode'), 'native')
        self.assertEqual(result['timeline'].cues[0].audio_frame, 0)
        self.assertEqual(result['notes'][0]['text'], '当前采用的配音')
        self.assertTrue(result['options'].embed_subtitles)
        self.assertFalse(list(self.root.glob('.ppt-*')))

    def test_native_export_receives_the_saved_software_preference(self):
        self.context.app_settings.set('presentation_preference', 'wps')
        _destination, result = self.export()
        self.assertEqual(result['preference'], 'wps')

    def test_cancel_waits_for_worker_before_removing_audio_and_keeps_old_video(self):
        destination, result = self.export('cancel', cancel=True)
        self.assertTrue(result['cancel_waits_for_thread'])
        self.assertTrue(result['audio_exists_before_exit'])
        self.assertEqual(destination.read_bytes(), b'previous video')
        self.assertFalse(list(self.root.glob('.ppt-*')))

    def test_native_failure_does_not_replace_existing_video(self):
        destination, _result = self.export('error')
        self.assertEqual(destination.read_bytes(), b'previous video')
        self.assertIn('第 1 页', self.dialog.status.text())
        self.assertFalse(list(self.root.glob('.ppt-*')))

    def test_final_save_failure_retains_complete_video(self):
        destination, _result = self.export(save_error=True)
        self.assertEqual(destination.read_bytes(), b'previous video')
        preserved = list(self.root.glob('.ppt-native-*.mkv'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), b'complete video and subtitles')
        self.assertIn(str(preserved[0]), self.dialog.status.text())

    def test_many_preflight_findings_keep_summary_compact_and_preserve_all_details(self):
        message = '以下页面需要处理后才能导出：\n' + '\n'.join(f'第 {page} 页的动画需要确认。' for page in range(1, 101))
        with patch('app.logging_setup.get_logger'):
            self.dialog._failed(message)
        self.assertLess(len(self.dialog.status.text()), 180)
        self.assertFalse(self.dialog.details_button.isHidden())
        self.assertEqual(self.dialog.error_details, message)
        self.assertIn('第 100 页', self.dialog.error_details)


if __name__ == '__main__':
    unittest.main()
