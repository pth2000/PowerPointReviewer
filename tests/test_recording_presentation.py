"""COM 窗口绑定、32/64 位类型库与页码/动画控制的回归验证。"""

import ctypes
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtGui import QImage, QWindow
from PySide6.QtMultimedia import QVideoFrame

from app.com_window import slideshow_hwnd
from app.recording_presentation import RecordingPresentation, list_presentations


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(hwnd=123, pid=456, process='powerpnt.exe',
                             class_name='screenClass', title='', area=1920*1080)
        self.user32 = Mock()
        self.user32.GetAncestor.return_value = 123
        self.user32.IsWindowVisible.return_value = True
        self.user32.IsIconic.return_value = False
        self.view = Mock()
        self.view.Slide = NS(SlideIndex=1)
        self.view.GetClickIndex.return_value = 0
        self.view.GetClickCount.return_value = 2
        def jump(page, reset):
            self.view.Slide.SlideIndex = page
        def click(index):
            self.view.GetClickIndex.return_value = index
        self.view.GotoSlide.side_effect = jump
        self.view.GotoClick.side_effect = click
        self.presentation = NS(Name='test.pptx', FullName='D:/test.pptx', Slides=NS(Count=3),
                               PageSetup=NS(SlideWidth=960, SlideHeight=540),
                               SlideShowSettings=NS(AdvanceMode=2))
        self.show = NS(Presentation=self.presentation, View=self.view, IsFullScreen=True)
        for name, value in (
                ('app.recording_presentation.slideshow_hwnd', 123),
                ('app.recording_presentation.window_target.window_info', self.identity),
                ('app.recording_presentation.window_target.is_fullscreen_window', True),
                ('app.recording_presentation.ctypes.windll.user32', self.user32)):
            patcher = patch(name, value) if name.endswith('user32') else patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.adapter = RecordingPresentation(self.show)

    def test_untitled_native_window_binds_without_title_guessing(self):
        self.assertEqual(self.adapter.hwnd, 123)
        self.adapter.begin(1)
        self.assertTrue(self.adapter.indexed_clicks)
        self.adapter.restore()
        self.assertEqual(self.presentation.SlideShowSettings.AdvanceMode, 2)

    def test_slide_change_skips_remaining_clicks(self):
        self.adapter.begin(1)
        self.adapter.advance(2, 1)
        self.view.GotoSlide.assert_called_with(2, True)
        self.view.Next.assert_not_called()
        self.view.GotoClick.assert_not_called()

    def test_animation_clicks_have_explicit_indexes(self):
        self.adapter.begin(1)
        self.adapter.advance(1, 1)
        self.adapter.advance(1, 1)
        self.assertEqual(self.view.GotoClick.call_args_list[0].args, (1,))
        self.assertEqual(self.view.GotoClick.call_args_list[1].args, (2,))
        self.view.Next.assert_not_called()

    def test_extra_segment_does_not_advance_to_next_page(self):
        self.adapter.begin(1)
        self.adapter.advance(1, 1)
        self.adapter.advance(1, 1)
        with self.assertRaisesRegex(RuntimeError, '只有 2 次点击'):
            self.adapter.advance(1, 1)
        self.assertEqual(self.view.Slide.SlideIndex, 1)
        self.view.Next.assert_not_called()

    def test_manual_click_and_page_changes_stop_recording(self):
        self.adapter.begin(1)
        self.view.GetClickIndex.return_value = 1
        with self.assertRaisesRegex(RuntimeError, '点击位置已改变'):
            self.adapter.advance(1, 1)
        self.view.Slide.SlideIndex = 3
        with self.assertRaisesRegex(RuntimeError, '第 3 页'):
            self.adapter.verify(1)

    def test_wps_retained_click_state_is_reset_at_start_and_on_jump(self):
        self.adapter.begin(1)
        self.adapter.advance(1, 1)
        self.adapter.advance(2, 1)
        self.view.GotoClick.assert_called_with(0)
        self.adapter.advance(1, 2)
        self.adapter.advance(1, 1)
        self.adapter.begin(1)
        self.view.GotoClick.assert_called_with(0)
        self.assertEqual(self.adapter.click_index, 0)

    def test_stale_or_recycled_window_identity_is_rejected(self):
        for identity in ({}, dict(self.identity, pid=999), dict(self.identity, class_name='editor')):
            with patch('app.recording_presentation.window_target.window_info', return_value=identity):
                with self.assertRaisesRegex(RuntimeError, '窗口已关闭或改变'):
                    self.adapter.verify(1)

    def test_minimized_window_is_rejected(self):
        self.user32.IsIconic.return_value = True
        with self.assertRaisesRegex(RuntimeError, '最小化'):
            self.adapter.verify(1)

    def test_manual_binding_rejects_other_app_or_editor(self):
        for identity in (dict(self.identity, process='notepad.exe'),
                         dict(self.identity, hwnd=789, class_name='PPTFrameClass')):
            self.user32.GetAncestor.return_value = identity['hwnd']
            with patch('app.recording_presentation.window_target.window_info', return_value=identity):
                with self.assertRaises(RuntimeError):
                    self.adapter.bind_capture_window(identity)
        self.assertEqual(self.adapter.hwnd, 123)

    def test_manual_override_changes_capture_and_keeps_com_control(self):
        target = dict(self.identity, hwnd=789)
        self.user32.GetAncestor.return_value = 789
        with patch('app.recording_presentation.window_target.window_info', return_value=target):
            self.adapter.bind_capture_window(target)
            self.assertEqual(self.adapter.hwnd, 789)
            self.assertEqual(self.adapter.capture_mode, 'manual')
            self.adapter.begin(1)
            self.adapter.advance(2, 1)
        self.assertIs(self.adapter.window, self.show)
        self.assertEqual(self.adapter.auto_target['hwnd'], 123)
        self.view.GotoSlide.assert_called_with(2, True)
        self.user32.GetAncestor.return_value = 123
        self.adapter.restore_auto_window()
        self.assertEqual(self.adapter.hwnd, 123)
        self.assertEqual(self.adapter.capture_mode, 'auto')

    def test_failed_manual_or_auto_binding_keeps_previous_selection(self):
        with patch('app.recording_presentation.window_target.is_fullscreen_window', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '全屏'):
                self.adapter.bind_capture_window(self.identity)
        self.assertEqual(self.adapter.capture_mode, 'auto')
        self.adapter.capture_mode = 'manual'
        with patch('app.recording_presentation.window_target.window_info', return_value={}):
            with self.assertRaises(RuntimeError):
                self.adapter.restore_auto_window()
        self.assertEqual(self.adapter.capture_mode, 'manual')
        self.assertEqual(self.adapter.hwnd, 123)

    def test_recycled_snapshot_is_rejected_before_binding(self):
        for target in (dict(self.identity, pid=999), dict(self.identity, class_name='other')):
            with self.assertRaisesRegex(RuntimeError, '窗口已关闭或改变'):
                self.adapter.bind_capture_window(target)

    def test_exiting_fullscreen_stops_capture(self):
        with patch('app.recording_presentation.window_target.is_fullscreen_window', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '退出全屏'):
                self.adapter.verify(1)

    def test_wps_process_and_unbound_shows_are_discoverable(self):
        application = NS(SlideShowWindows=Mock(return_value=self.show))
        application.SlideShowWindows.Count = 1
        with patch('app.recording_presentation.slideshow_hwnd', return_value=0), \
                patch('win32com.client.GetActiveObject', return_value=application):
            found = list_presentations()
        self.assertEqual([p.provider for p in found], ['PowerPoint', 'WPS 演示'])
        self.assertEqual([p.hwnd for p in found], [0, 0])
        with self.assertRaisesRegex(RuntimeError, '选择窗口'):
            found[1].verify(1)
        self.identity['process'] = 'wpp.exe'
        self.identity['class_name'] = 'Qt5QWindowIcon'
        found[1].bind_capture_window(self.identity)
        self.assertEqual(found[1].hwnd, 123)

    def test_restore_auto_retries_lookup_when_initial_discovery_failed(self):
        with patch('app.recording_presentation.slideshow_hwnd', return_value=0):
            adapter = RecordingPresentation(self.show)
        self.assertFalse(adapter.hwnd)
        adapter.bind_capture_window(self.identity)
        adapter.restore_auto_window()
        self.assertEqual(adapter.capture_mode, 'auto')
        self.assertEqual(adapter.auto_target['hwnd'], 123)

    def test_missing_index_api_falls_back_and_checks_page(self):
        self.view.GetClickIndex.side_effect = AttributeError('unsupported')
        self.adapter.begin(1)
        self.view.Next.side_effect = lambda: setattr(self.view.Slide, 'SlideIndex', 2)
        with self.assertRaisesRegex(RuntimeError, '第 2 页'):
            self.adapter.advance(1, 1)

    def _track_document_changes(self, mode, timed=False, saved=True):
        document = self.presentation
        document.Saved = saved
        writes = []
        class Settings:
            @property
            def AdvanceMode(self):
                return self.mode

            @AdvanceMode.setter
            def AdvanceMode(self, value):
                writes.append(value)
                self.mode = value
                document.Saved = False
        settings = Settings()
        settings.mode = mode
        document.SlideShowSettings = settings
        document.Slides.Item = lambda index: NS(SlideShowTransition=NS(AdvanceOnTime=timed))
        return writes

    def test_manual_show_does_not_write_settings_or_mark_document_dirty(self):
        writes = self._track_document_changes(1)
        self.adapter.begin(1)
        self.adapter.restore()
        self.assertEqual(writes, [])
        self.assertTrue(self.presentation.Saved)

    def test_show_without_timed_transitions_does_not_mark_document_dirty(self):
        writes = self._track_document_changes(2)
        self.adapter.begin(1)
        self.adapter.advance(2, 1)
        self.adapter.restore()
        self.assertEqual(writes, [])
        self.assertTrue(self.presentation.Saved)
        self.assertEqual(self.presentation.SlideShowSettings.AdvanceMode, 2)

    def test_timed_show_restores_mode_without_hiding_unsaved_changes(self):
        writes = self._track_document_changes(2, timed=True)
        self.adapter.begin(1)
        self.adapter.restore()
        self.assertEqual(writes, [1, 2])
        self.assertFalse(self.presentation.Saved)

    def test_existing_unsaved_edits_remain_unsaved(self):
        writes = self._track_document_changes(2, saved=False)
        self.adapter.begin(1)
        self.adapter.restore()
        self.assertEqual(writes, [])
        self.assertFalse(self.presentation.Saved)

    def test_repeated_begin_keeps_original_mode_for_restoration(self):
        writes = self._track_document_changes(2, timed=True)
        self.adapter.begin(1)
        self.adapter.begin(1)
        self.adapter.restore()
        self.assertEqual(writes, [1, 2])


class NativeGetterTests(unittest.TestCase):
    def _invoke(self, syskind, invalid=False, failed=False):
        from comtypes import HRESULT
        from comtypes.automation import IDispatch, VT_HRESULT, VT_I4, VT_PTR
        from comtypes.typeinfo import INVOKE_PROPERTYGET, TKIND_INTERFACE
        calls = []
        def get(pointer, output):
            calls.append(True)
            output[0] = -2147483500  # HWND's signed 32-bit declaration.
            return -1 if failed else 0
        callback = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long))(get)
        table = (ctypes.c_void_p * 21)()
        table[20] = ctypes.cast(callback, ctypes.c_void_p).value
        instance = ctypes.pointer(ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p)))
        pointer_size = 4 if syskind == 1 else 8
        parameter = NS(tdesc=NS(vt=VT_PTR, lptdesc=NS(contents=NS(vt=VT_I4))),
                       _=NS(paramdesc=NS(wParamFlags=0 if invalid else 10)))
        descriptor = NS(memid=1, invkind=INVOKE_PROPERTYGET, cParams=1,
                        elemdescFunc=NS(tdesc=NS(vt=VT_HRESULT)), oVft=20*pointer_size,
                        lprgelemdescParam=[parameter])
        attributes = NS(typekind=TKIND_INTERFACE, cFuncs=2, cbSizeVft=21*pointer_size, guid='unused')
        info = Mock()
        info.GetTypeAttr.return_value = attributes
        # Deliberately contradictory library bitness: native offsets are authoritative.
        info.GetContainingTypeLib.return_value = [NS(GetLibAttr=lambda: NS(syskind=1))]
        info.GetRefTypeInfo.return_value = NS(GetTypeAttr=lambda: NS(guid=IDispatch._iid_))
        first = NS(memid=2, invkind=INVOKE_PROPERTYGET, oVft=7*pointer_size)
        info.GetFuncDesc.side_effect = [first, descriptor]
        info.GetDocumentation.side_effect = lambda memid: ['HWND' if memid == 1 else 'Application']
        dispatch = Mock()
        dispatch.GetTypeInfo.return_value = info
        dispatch.QueryInterface.return_value = instance
        application = NS(SlideShowWindows=NS(Item=lambda index: NS(_comobj=dispatch)))
        with patch('comtypes.client.GetActiveObject', return_value=application):
            result = slideshow_hwnd('test', 1)
        return result, calls

    def test_win32_and_win64_type_offsets_use_same_native_proxy_slot(self):
        for syskind in (1, 3):
            with self.subTest(syskind=syskind):
                result, calls = self._invoke(syskind)
                self.assertEqual(result, 2147483796)
                self.assertEqual(calls, [True])

    def test_invalid_signature_never_calls_native_function(self):
        self.assertEqual(self._invoke(1, invalid=True), (0, []))

    def test_failed_hresult_leaves_manual_binding_available(self):
        self.assertEqual(self._invoke(3, failed=True), (0, [True]))


class WindowSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_manual_recording_selection_does_not_change_page_turn_settings(self):
        from app.settings_store import AppSettings
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        settings = AppSettings()
        settings.set('page_turn_window', {'hwnd': 77})
        presentation = NS(provider='WPS 演示', name='test.pptx', slide_count=2, hwnd=0,
                          target={}, matches_source=lambda source: False)
        def bind(target):
            presentation.hwnd = target['hwnd']
            presentation.target = target
            presentation.capture_mode = 'manual'
        presentation.bind_capture_window = bind
        def restore():
            presentation.capture_mode = 'auto'
        presentation.restore_auto_window = restore
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[presentation]):
            dialog = VideoRecordingDialog([], [], ctx=NS(app_settings=settings))
        self.assertFalse(dialog.start_button.isEnabled())
        picker = Mock(selected={'hwnd': 123}, DialogCode=QDialog.DialogCode)
        picker.exec.return_value = QDialog.DialogCode.Accepted
        with patch('ui.dialogs.video_recording_dialog.WindowTargetDialog', return_value=picker) as factory:
            dialog.choose_window()
        self.assertTrue(factory.call_args.kwargs['recording'])
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertIn('手动', dialog.capture_label.text())
        self.assertFalse(dialog.auto_window_button.isHidden())
        self.assertEqual(settings.get('page_turn_window'), {'hwnd': 77})
        dialog._set_busy(True)
        self.assertFalse(dialog.window_button.isEnabled())
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.auto_window_button.isEnabled())
        dialog._set_busy(False)
        dialog.restore_auto_window()
        self.assertIn('自动', dialog.capture_label.text())
        self.assertTrue(dialog.auto_window_button.isHidden())
        dialog.deleteLater()

    def test_shared_picker_keeps_ordinary_mode_and_can_pick_untitled_recording_window(self):
        from ui.dialogs.window_target_dialog import WindowTargetDialog
        window = dict(hwnd=123, pid=456, title='', process='wpp.exe', class_name='Qt5QWindowIcon', area=100)
        with patch('app.window_target.list_windows', return_value=[window]) as enumerate_windows:
            dialog = WindowTargetDialog(recording=True)
            enumerate_windows.assert_called_with(include_untitled=True)
        with patch('ui.dialogs.window_preview.WindowPreview.show_target',
                   lambda preview, target: preview.readyChanged.emit(True)):
            dialog.table.selectRow(0)
        dialog.accept()
        self.assertEqual(dialog.selected['pid'], 456)
        self.assertEqual(dialog.windowTitle(), '选择录制窗口')
        dialog.deleteLater()
        with patch('app.window_target.list_windows', return_value=[]):
            ordinary = WindowTargetDialog()
        self.assertEqual(ordinary.windowTitle(), '选择翻页目标')
        ordinary.deleteLater()

    def test_automatic_detection_does_not_disable_manual_picker(self):
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        presentation = NS(provider='PowerPoint', name='test.pptx', slide_count=2, hwnd=123,
                          target={'title': 'Automatic show'}, capture_mode='auto',
                          matches_source=lambda source: False)
        presentation.bind_capture_window = Mock()
        with patch('ui.dialogs.video_recording_dialog.list_presentations', return_value=[presentation]):
            dialog = VideoRecordingDialog([], [])
        self.assertTrue(dialog.window_button.isEnabled())
        picker = Mock(selected={'hwnd': 789}, DialogCode=QDialog.DialogCode)
        picker.exec.return_value = QDialog.DialogCode.Accepted
        with patch('ui.dialogs.video_recording_dialog.WindowTargetDialog', return_value=picker):
            dialog.choose_window()
        presentation.bind_capture_window.assert_called_once_with({'hwnd': 789})
        picker.deleteLater.assert_called_once()
        dialog.deleteLater()

    def test_refresh_keeps_manual_choice_for_same_com_show(self):
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        old = NS(provider='PowerPoint', name='test.pptx', slide_count=2, hwnd=789,
                 target={'hwnd': 789, 'title': 'Manual show'}, capture_mode='manual',
                 matches_source=lambda source: False)
        new = NS(provider='PowerPoint', name='test.pptx', slide_count=2, hwnd=123,
                 target={'hwnd': 123, 'title': 'Automatic show'}, capture_mode='auto',
                 matches_source=lambda source: False, same_show=lambda other: other is old)
        def bind(target):
            new.hwnd, new.target, new.capture_mode = target['hwnd'], target, 'manual'
        new.bind_capture_window = bind
        with patch('ui.dialogs.video_recording_dialog.list_presentations', side_effect=[[old], [new]]):
            dialog = VideoRecordingDialog([], [])
            dialog.reload()
        self.assertEqual(new.hwnd, 789)
        self.assertIn('Manual show', dialog.capture_label.text())
        self.assertFalse(dialog.auto_window_button.isHidden())
        dialog.deleteLater()

    def test_lost_manual_window_on_refresh_does_not_silently_switch_to_auto(self):
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog
        old = NS(provider='PowerPoint', name='test.pptx', slide_count=2, hwnd=789,
                 target={'hwnd': 789}, capture_mode='manual', matches_source=lambda source: False)
        new = NS(provider='PowerPoint', name='test.pptx', slide_count=2, hwnd=123,
                 target={'hwnd': 123}, capture_mode='auto', matches_source=lambda source: False,
                 same_show=lambda other: True, bind_capture_window=Mock(side_effect=RuntimeError('已关闭')))
        with patch('ui.dialogs.video_recording_dialog.list_presentations', side_effect=[[old], [new]]):
            dialog = VideoRecordingDialog([], [])
            dialog.reload()
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.auto_window_button.isHidden())
        self.assertIn('原手动窗口已失效', dialog.status.text())
        dialog.deleteLater()

    def test_preview_is_required_and_unsupported_window_cannot_be_confirmed(self):
        from ui.dialogs.window_target_dialog import WindowTargetDialog
        target = dict(hwnd=123, title='Edit window', process='powerpnt.exe', class_name='PPTFrameClass')
        presentation = NS(capture_processes={'powerpnt.exe'},
                          validate_capture_window=Mock(side_effect=RuntimeError('编辑窗口暂不支持')))
        with patch('app.window_target.list_windows', return_value=[target]):
            dialog = WindowTargetDialog(recording=True, presentation=presentation)
        dialog.table.selectRow(0)
        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertIn('编辑窗口暂不支持', dialog.status_label.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.selected, {})
        dialog.reject()
        dialog.deleteLater()


class WindowPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.dialogs.window_preview import WindowPreview
        self.preview = WindowPreview()
        self.window = QWindow()
        self.window.create()
        self.target = dict(hwnd=int(self.window.winId()), pid=456, class_name='test')
        self.patchers = [patch('app.window_target.window_info', return_value=self.target),
                         patch('ui.dialogs.window_preview.QCapturableWindow.isValid', return_value=True),
                         patch('ui.dialogs.window_preview.QWindowCapture.start')]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        self.preview.stop()
        self.preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window.destroy()
        for patcher in reversed(self.patchers):
            patcher.stop()

    def frame(self, color):
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(color)
        return QVideoFrame(image)

    def test_first_frame_releases_capture_and_keeps_preview_image(self):
        ready = []
        self.preview.readyChanged.connect(ready.append)
        self.preview.show_target(self.target)
        capture = self.preview.capture
        self.assertIsNotNone(capture)
        self.preview._frame(self.frame(0xff3377cc), self.preview.generation)
        self.assertIsNone(self.preview.capture)
        self.assertFalse(self.preview.timer.isActive())
        self.assertEqual(self.preview.image.pixelColor(0, 0).name(), '#3377cc')
        self.assertEqual(ready[-1], True)

    def test_switch_ignores_frames_and_errors_from_previous_capture(self):
        self.preview.show_target(self.target)
        previous = self.preview.generation
        self.preview.show_target(self.target)
        current_capture = self.preview.capture
        self.preview._frame(self.frame(0xff3377cc), previous)
        self.preview._error('old error', previous)
        self.assertIs(self.preview.capture, current_capture)
        self.assertTrue(self.preview.image.isNull())
        self.preview._frame(self.frame(0xffcc8833), self.preview.generation)
        self.assertEqual(self.preview.image.pixelColor(0, 0).name(), '#cc8833')

    def test_error_and_timeout_release_resources_and_disable_confirmation(self):
        ready = []
        self.preview.readyChanged.connect(ready.append)
        self.preview.show_target(self.target)
        self.preview._error('test failure', self.preview.generation)
        self.assertIsNone(self.preview.capture)
        self.assertFalse(ready[-1])
        self.assertIn('test failure', self.preview.text())
        self.preview.show_target(self.target)
        self.preview.timer.timeout.emit()
        self.assertIsNone(self.preview.capture)
        self.assertIn('预览超时', self.preview.text())


class FullscreenWindowTests(unittest.TestCase):
    def test_fullscreen_on_second_monitor_and_windowed_client_area(self):
        from app import window_target
        user32 = Mock()
        user32.IsWindow.return_value = True
        user32.MonitorFromWindow.return_value = 123
        def client_rect(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = 0, 0
            pointer._obj.right, pointer._obj.bottom = 1920, 1080
            return True
        def screen_point(hwnd, pointer):
            pointer._obj.x, pointer._obj.y = -1920, 0
            return True
        def monitor_info(handle, pointer):
            pointer._obj.rcMonitor.left, pointer._obj.rcMonitor.top = -1920, 0
            pointer._obj.rcMonitor.right, pointer._obj.rcMonitor.bottom = 0, 1080
            return True
        user32.GetClientRect.side_effect = client_rect
        user32.ClientToScreen.side_effect = screen_point
        user32.GetMonitorInfoW.side_effect = monitor_info
        with patch('app.window_target._user32', user32):
            self.assertTrue(window_target.is_fullscreen_window(123))
            def title_bar_offset(hwnd, pointer):
                pointer._obj.x, pointer._obj.y = -1920, 30
                return True
            user32.ClientToScreen.side_effect = title_bar_offset
            self.assertFalse(window_target.is_fullscreen_window(123))


if __name__ == '__main__':
    unittest.main()
