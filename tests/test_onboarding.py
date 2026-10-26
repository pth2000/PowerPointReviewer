"""使用指引的真实控件交互、状态持久化、任务及弹窗避让。"""

from types import SimpleNamespace as NS
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QHBoxLayout, QScrollArea, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, NavigationInterface, NavigationItemPosition, PushButton

from app.project.model import ProjectSession
from app.settings_store import AppSettings
from qt_test_case import QtTestCase
from ui.onboarding import UsageGuide


class Worker(QObject):
    started = Signal()
    finished = Signal()
    signal_finish = Signal(object)


class Page(QWidget):
    usage_guide_requested = Signal()
    advanced_guide_requested = Signal()
    audio_changed = Signal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        self.bgScrollArea = QScrollArea(self)
        self.bgScrollArea.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.CardWidget_5 = QWidget(content)
        self.CardWidget_5.setMinimumHeight(80)
        engine_layout = QHBoxLayout(self.CardWidget_5)
        self.engine_button = PushButton('选择声音', self.CardWidget_5)
        engine_layout.addWidget(self.engine_button)
        self.CardWidget_4 = QWidget(content)
        self.CardWidget_4.setMinimumHeight(80)
        QHBoxLayout(self.CardWidget_4)
        self.getFileButton = PushButton('导入', content)
        self.editMarkPushButton = PushButton('编辑分隔符', content)
        self.playButton = PushButton('播放', content)
        self.script_comments_switch = PushButton('识别讲稿注释', self.CardWidget_5)
        self.animation_check_switch = PushButton('生成前校验点击动画', self.CardWidget_4)
        self.page_turn_card = self.CardWidget_5
        self.hotkey_card = self.CardWidget_4
        self.audio_review_card = self.CardWidget_5
        self.ai_rewrite_card = self.CardWidget_4
        self.script_export_card = self.CardWidget_5
        self.audio_export_card = self.CardWidget_4
        self.video_export_card = self.CardWidget_4
        self.project_package_card = self.getFileButton
        layout.addWidget(self.CardWidget_5)
        layout.addWidget(self.CardWidget_4)
        layout.addWidget(self.editMarkPushButton)
        layout.addWidget(self.getFileButton)
        layout.addWidget(self.playButton)
        layout.addStretch()
        self.bgScrollArea.setWidget(content)
        outer.addWidget(self.bgScrollArea)
        self.project = ProjectSession()
        self.project.notes = {}
        self.project.media_list = []
        self.project.audio_loaded = False
        self.busy = False
        self.save_thread = Worker(self)
        self.import_pptx = Mock()
        self.prompt_unsaved_changes = Mock(return_value=True)
        self.is_dirty = Mock(return_value=False)
        self.create_warning_info_bar = Mock()

    def is_busy(self):
        return self.busy


class GuideHost(QWidget):
    def __init__(self):
        super().__init__()
        self.context = NS(app_settings=AppSettings(), config=Mock())
        layout = QHBoxLayout(self)
        self.navigationInterface = NavigationInterface(self)
        layout.addWidget(self.navigationInterface)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)
        self.ppt_r = Page()
        self.setting_interface = Page()
        self.setting_interface.setObjectName('settings-test')
        self.tools_interface = Page()
        for page in (self.ppt_r, self.setting_interface, self.tools_interface):
            self.stack.addWidget(page)
        self.settings_button = self.navigationInterface.addItem(
            'settings-test', FluentIcon.SETTING, '设置',
            onClick=lambda: self.switchTo(self.setting_interface), position=NavigationItemPosition.BOTTOM)

    def switchTo(self, page):
        self.stack.setCurrentWidget(page)


class OnboardingTests(QtTestCase):
    def setUp(self):
        self.window = GuideHost()
        self.window.resize(850, 750)
        self.window.show()
        self.guide = UsageGuide(self.window)
        self.qt.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def settle(self):
        self.guide.overlay.animation.setCurrentTime(220)
        self.qt.processEvents()

    def start_at_engine(self):
        self.guide.start()
        self.assertEqual(self.guide.step, 'welcome')
        self.guide.next()
        self.assertEqual(self.guide.step, 'settings')
        self.window.settings_button.click()
        self.assertEqual(self.guide.step, 'engine')

    def generate(self, success=True):
        page = self.window.ppt_r
        page.busy = True
        page.save_thread.started.emit()
        self.assertFalse(self.guide.overlay.isVisible())
        if success:
            page.project.audio_loaded = True
            page.project.media_list = ['audio.wav']
            page.save_thread.signal_finish.emit(NS())
        page.busy = False
        page.save_thread.finished.emit()
        self.settle()

    def test_welcome_skip_is_remembered_and_replay_remains_available(self):
        self.guide.maybe_offer()
        self.assertEqual(self.guide.step, 'welcome')
        self.guide.overlay.card.skip_button.click()
        self.assertTrue(self.window.context.app_settings.get('onboarding_seen'))
        self.guide.maybe_offer()
        self.assertIsNone(self.guide.step)
        self.window.setting_interface.usage_guide_requested.emit()
        self.assertEqual(self.guide.step, 'welcome')
        self.assertTrue(self.guide.overlay.isVisible())

    def test_first_step_highlights_settings_and_waits_for_navigation_click(self):
        self.guide.maybe_offer()
        self.assertEqual(self.guide.overlay.card.title.text(), '欢迎使用 PowerPointReviewer')
        self.guide.next()
        self.settle()
        self.assertEqual(self.guide.step, 'settings')
        self.assertIs(self.guide.target, self.window.settings_button)
        self.assertIs(self.window.stack.currentWidget(), self.window.ppt_r)
        self.assertEqual(self.guide.overlay.card.progress.text(), '开始使用 · 1 / 6')
        point = self.window.settings_button.mapTo(self.window, self.window.settings_button.rect().center())
        item = self.window.childAt(point)
        self.assertTrue(item is self.window.settings_button or self.window.settings_button.isAncestorOf(item))
        QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=item.mapFrom(self.window, point))
        self.assertEqual(self.guide.step, 'engine')
        self.assertIs(self.window.stack.currentWidget(), self.window.setting_interface)
        self.guide.back()
        self.assertEqual(self.guide.step, 'settings')
        self.assertIs(self.window.stack.currentWidget(), self.window.ppt_r)
        self.guide.overlay.card.next_button.click()
        self.assertEqual(self.guide.step, 'engine')

    def test_navigation_click_does_not_restart_skipped_guide(self):
        self.guide.start()
        self.guide.skip()
        self.window.settings_button.click()
        self.assertIsNone(self.guide.step)

    def test_existing_history_is_not_interrupted_by_welcome(self):
        self.window.context.app_settings.set('last_session', {'record_path': 'old.json'})
        self.guide.maybe_offer()
        self.assertIsNone(self.guide.step)
        self.assertTrue(self.window.context.app_settings.get('onboarding_seen'))

    def test_actual_import_still_advances_after_successful_generation(self):
        self.start_at_engine()
        self.guide.next()
        self.assertEqual(self.guide.step, 'preview')
        self.guide.next()
        self.window.setting_interface.prompt_unsaved_changes.assert_called_once_with(allow_regenerate=False)
        self.assertEqual(self.guide.step, 'separator')
        self.guide.next()
        self.assertEqual(self.guide.step, 'import')
        self.window.ppt_r.import_pptx.assert_not_called()
        self.generate(success=False)
        self.assertEqual(self.guide.step, 'import')
        self.generate()
        self.assertEqual(self.guide.step, 'play')
        self.assertTrue(self.window.context.app_settings.get('onboarding_tools_seen'))
        self.guide.next()
        self.assertEqual(self.guide.step, 'basics_complete')
        self.guide.overlay.card.next_button.click()
        self.assertEqual(self.guide.step, 'update_script')

    def test_failed_settings_save_does_not_leave_preview_step(self):
        self.start_at_engine()
        self.guide.next()
        self.window.setting_interface.prompt_unsaved_changes.return_value = False
        self.guide.next()
        self.assertEqual(self.guide.step, 'preview')

    def test_modal_dialog_gets_escape_and_guide_resumes_after_close(self):
        self.start_at_engine()
        dialog = QDialog(self.window)
        dialog.setModal(True)
        dialog.show()
        self.qt.processEvents()
        self.assertFalse(self.guide.overlay.isVisible())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.qt.processEvents()
        self.assertEqual(self.guide.step, 'engine')
        self.assertTrue(self.guide.overlay.isVisible())

    def test_real_controls_stay_clickable_and_card_escape_skips(self):
        self.start_at_engine()
        self.settle()
        target = self.window.setting_interface.engine_button
        clicked = Mock()
        target.clicked.connect(clicked)
        point = target.mapTo(self.window, target.rect().center())
        self.assertIs(self.window.childAt(point), target)
        QTest.mouseClick(target, Qt.MouseButton.LeftButton)
        clicked.assert_called_once()
        QTest.keyClick(self.guide.overlay.card.next_button, Qt.Key.Key_Escape)
        self.assertIsNone(self.guide.step)

    def test_resize_keeps_card_inside_window_and_animation_stops(self):
        self.start_at_engine()
        for width, height in ((760, 560), (850, 750), (1200, 800)):
            self.window.resize(width, height)
            self.settle()
            self.assertTrue(self.window.rect().contains(self.guide.overlay.card.geometry()))
        self.assertEqual(self.guide.overlay.animation.state(), self.guide.overlay.animation.State.Stopped)

    def test_first_generation_hint_is_shown_once_and_opens_tools(self):
        self.guide.skip()
        self.generate()
        self.assertEqual(self.guide.step, 'generated')
        self.guide.next()
        self.assertIs(self.window.stack.currentWidget(), self.window.tools_interface)
        self.generate()
        self.assertIsNone(self.guide.step)

    def test_loading_ready_audio_also_completes_import_step(self):
        self.start_at_engine()
        self.guide.next()
        self.guide.next()
        self.guide.next()
        page = self.window.ppt_r
        page.project.audio_loaded = True
        page.project.media_list = ['history.wav']
        page.audio_changed.emit()
        self.assertEqual(self.guide.step, 'play')

    def test_separator_dialog_returns_to_same_step_and_can_continue_with_default(self):
        from ui.dialogs.edit_mark_dialog import EditMarkMessageBox
        self.start_at_engine()
        self.guide.next()
        self.guide.next()
        self.assertEqual(self.guide.step, 'separator')
        self.assertEqual(self.guide.overlay.card.progress.text(), '开始使用 · 4 / 6')
        dialog = EditMarkMessageBox('●', self.window)
        visible = []
        def accept():
            visible.append(self.guide.overlay.isVisible())
            dialog.yesButton.click()
        QTimer.singleShot(0, accept)
        self.assertEqual(dialog.exec(), dialog.DialogCode.Accepted)
        self.assertEqual(visible, [False])
        self.qt.processEvents()
        self.assertEqual(self.guide.step, 'separator')
        self.assertTrue(self.guide.overlay.isVisible())
        self.guide.next()
        self.assertEqual(self.guide.step, 'import')
        self.guide.back()
        self.assertEqual(self.guide.step, 'separator')
        self.guide.back()
        self.assertEqual(self.guide.step, 'preview')

    def test_regenerating_after_separator_change_does_not_skip_import_guidance(self):
        self.start_at_engine()
        self.guide.next()
        self.guide.next()
        self.generate()
        self.assertEqual(self.guide.step, 'separator')
        self.assertTrue(self.guide.overlay.isVisible())

    def test_back_and_busy_replay_keep_existing_work_untouched(self):
        self.start_at_engine()
        self.guide.next()
        self.guide.back()
        self.assertEqual(self.guide.step, 'engine')
        self.guide.skip()
        self.window.ppt_r.busy = True
        self.guide.start()
        self.assertIsNone(self.guide.step)
        self.window.ppt_r.create_warning_info_bar.assert_called_once()

    def test_tutorial_can_finish_without_importing_files_or_generating_audio(self):
        self.start_at_engine()
        for _ in range(3):
            self.guide.next()
        self.assertEqual(self.guide.step, 'import')
        self.assertEqual(self.guide.overlay.card.next_button.text(), '下一步')
        self.guide.next()
        self.assertEqual(self.guide.step, 'play')
        self.guide.back()
        self.assertEqual(self.guide.step, 'import')
        self.guide.next()
        self.guide.next()
        self.assertEqual(self.guide.step, 'basics_complete')
        self.assertEqual(self.guide.overlay.card.title.text(), '入门指引已完成')
        self.assertEqual(self.guide.overlay.card.skip_button.text(), '暂时结束')
        self.guide.overlay.card.skip_button.click()
        self.assertIsNone(self.guide.step)
        self.window.ppt_r.import_pptx.assert_not_called()
        self.assertFalse(self.window.ppt_r.project.notes)
        self.assertFalse(self.window.ppt_r.project.media_list)
        self.assertFalse(self.window.ppt_r.project.audio_loaded)
        self.assertFalse(self.window.ppt_r.busy)

    def test_replay_from_settings_shows_welcome_even_after_guide_was_completed(self):
        self.guide.skip()
        self.window.context.app_settings.set('onboarding_tools_seen', True)
        self.window.switchTo(self.window.setting_interface)
        self.window.setting_interface.usage_guide_requested.emit()
        self.assertEqual(self.guide.step, 'welcome')
        self.assertIs(self.window.stack.currentWidget(), self.window.ppt_r)
        self.assertEqual(self.guide.overlay.card.next_button.text(), '开始引导')

    def test_replayed_basic_guide_also_offers_advanced_tips_only_when_completed(self):
        self.guide.skip()
        self.window.setting_interface.usage_guide_requested.emit()
        self.guide.overlay.card.skip_button.click()
        self.assertIsNone(self.guide.step)
        self.start_at_engine()
        for _ in range(5):
            self.guide.next()
        self.assertEqual(self.guide.step, 'basics_complete')
        self.window.resize(760, 560)
        self.settle()
        self.assertTrue(self.window.rect().contains(self.guide.overlay.card.geometry()))
        self.guide.overlay.card.next_button.click()
        self.assertEqual(self.guide.step, 'update_script')

    def test_completion_prompt_resumes_after_generation_and_escape_closes_it(self):
        self.start_at_engine()
        for _ in range(5):
            self.guide.next()
        for success in (False, True):
            self.generate(success)
            self.assertEqual(self.guide.step, 'basics_complete')
            self.assertTrue(self.guide.overlay.isVisible())
        QTest.keyClick(self.guide.overlay.card.skip_button, Qt.Key.Key_Escape)
        self.assertIsNone(self.guide.step)

    def test_advanced_walkthrough_visits_real_targets_without_changing_project_or_basic_preferences(self):
        self.window.switchTo(self.window.setting_interface)
        self.window.setting_interface.advanced_guide_requested.emit()
        page, settings, tools = self.window.ppt_r, self.window.setting_interface, self.window.tools_interface
        destinations = (page, settings, settings, settings, tools, tools, tools, tools, tools, tools)
        targets = (page.getFileButton.parentWidget(), settings.script_comments_switch.parentWidget(),
                   settings.page_turn_card, settings.hotkey_card, tools.audio_review_card,
                   tools.ai_rewrite_card, tools.script_export_card, tools.audio_export_card,
                   tools.video_export_card, tools.project_package_card)
        self.window.resize(760, 560)
        for index, step in enumerate(self.guide.ADVANCED_STEPS):
            self.settle()
            self.assertEqual(self.guide.step, step)
            self.assertIs(self.guide.target, targets[index])
            self.assertIs(self.window.stack.currentWidget(), destinations[index])
            self.assertEqual(self.guide.overlay.card.progress.text(), f'进阶技巧 · {index + 1} / {len(destinations)}')
            self.assertTrue(self.window.rect().contains(self.guide.overlay.card.geometry()))
            self.assertEqual(self.guide.overlay.card.back_button.isVisible(), index > 0)
            if index:
                self.guide.back()
                self.assertEqual(self.guide.step, self.guide.ADVANCED_STEPS[index - 1])
                self.guide.next()
            self.guide.next()
        self.assertIsNone(self.guide.step)
        self.assertFalse(self.window.context.app_settings.get('onboarding_seen'))
        self.assertFalse(self.window.context.app_settings.get('onboarding_tools_seen'))
        self.window.context.config.save_later.assert_not_called()
        page.import_pptx.assert_not_called()
        self.assertFalse(page.project.audio_loaded)
        self.assertFalse(page.project.notes)
        self.assertFalse(page.project.media_list)

    def test_advanced_guide_can_close_and_restart_without_marking_basic_guide_seen(self):
        self.guide.start_advanced()
        QTest.keyClick(self.guide.overlay.card.next_button, Qt.Key.Key_Escape)
        self.assertIsNone(self.guide.step)
        self.assertFalse(self.window.context.app_settings.get('onboarding_seen'))
        self.guide.start_advanced()
        self.assertEqual(self.guide.step, 'update_script')

    def test_advanced_guide_keeps_current_tip_after_successful_or_failed_generation(self):
        self.guide.start_advanced()
        self.guide.next()
        for success in (False, True):
            self.generate(success)
            self.assertEqual(self.guide.step, 'script_options')
            self.assertTrue(self.guide.overlay.isVisible())
            self.assertFalse(self.guide._generated)

    def test_advanced_start_waits_for_running_task(self):
        self.window.ppt_r.busy = True
        self.guide.start_advanced()
        self.assertIsNone(self.guide.step)
        self.window.ppt_r.create_warning_info_bar.assert_called_once()

    def test_leaving_advanced_settings_does_not_bypass_unsaved_changes(self):
        self.guide.start_advanced()
        self.guide.next()
        settings = self.window.setting_interface
        settings.is_dirty.return_value = True
        settings.prompt_unsaved_changes.return_value = False
        self.guide.next()
        self.guide.next()
        self.guide.next()
        settings.prompt_unsaved_changes.assert_called_once_with(allow_regenerate=False)
        self.assertIs(self.window.stack.currentWidget(), settings)
        self.assertEqual(self.guide.step, 'hotkeys')
        self.assertTrue(self.guide.overlay.isVisible())
