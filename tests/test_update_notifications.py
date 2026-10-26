"""验证自动更新通知、设置开关及后台检查与退出之间的交互。"""

import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from qfluentwidgets import PushButton

from app.settings_store import AppSettings
from tasks.update_task import UpdateCheckTask
from test_sync_playback import SyncPlaybackFixture


class UpdateNotificationTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.info = NS(TargetFullRelease=NS(Version='1.9.0', Size=100, NotesMarkdown='更新说明'),
                       BaseRelease=None, DeltasToTarget=[])
        self.entered = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.finish_worker)

    def finish_worker(self):
        self.release.set()
        task = self.settings.update_thread
        if task is not None:
            self.assertTrue(task.wait(5000))
        self.qt.processEvents()

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition())

    def blocked_check(self):
        self.entered.set()
        self.release.wait(4)
        return self.info

    def test_switch_is_enabled_by_default_and_saves_without_dirtying_voice_settings(self):
        self.assertTrue(self.settings.auto_update_switch.isChecked())
        self.settings.auto_update_switch.setChecked(False)
        self.assertFalse(self.ctx.app_settings.get('auto_check_updates'))
        self.ctx.config.save_later.assert_called_once()
        self.assertFalse(self.settings.is_dirty())
        loaded = AppSettings()
        loaded.load_from(self.ctx.app_settings.to_dict())
        self.assertFalse(loaded.get('auto_check_updates'))

    def test_disabled_and_source_runs_do_not_start_automatic_network_checks(self):
        with patch('tasks.update_task.updater.create_manager') as create:
            with patch('app.paths.IS_VELOPACK_INSTALL', False):
                self.settings.check_updates_automatically()
            self.ctx.app_settings.set('auto_check_updates', False)
            with patch('app.paths.IS_VELOPACK_INSTALL', True):
                self.settings.check_updates_automatically()
        create.assert_not_called()
        self.assertIsNone(self.settings.update_thread)

    def test_background_check_notifies_once_and_only_opens_details_on_click(self):
        manager = Mock()
        manager.check_for_updates.return_value = self.info
        with patch('app.paths.IS_VELOPACK_INSTALL', True), \
             patch('tasks.update_task.updater.create_manager', return_value=manager) as create, \
             patch.object(self.settings, 'show_update_dialog') as details, \
             patch.object(self.settings, 'download_update') as download:
            self.settings.check_updates_automatically()
            self.wait_until(lambda: self.settings.update_thread is None)
            bar = self.settings._update_bar
            self.assertIn('1.9.0', bar.content)
            self.assertEqual(bar.duration, -1)
            details.assert_not_called()
            download.assert_not_called()
            self.settings.check_updates_automatically()
            create.assert_called_once()
            bar.findChild(PushButton).click()
            details.assert_called_once_with(self.info)
            download.assert_not_called()
        self.assertIsNone(self.settings._update_bar)

    def test_automatic_no_update_and_errors_do_not_interrupt_the_user(self):
        self.settings._update_check_automatic = True
        with patch.object(self.settings, 'show_update_dialog') as details, \
             patch.object(self.settings, 'create_success_info_bar') as success, \
             patch.object(self.settings, 'create_error_info_bar') as error, \
             patch.object(self.settings, 'create_warning_info_bar') as warning, \
             patch('ui.pages.settings_page.get_logger') as logger:
            for status in ('latest', 'error', 'unmanaged'):
                self.settings.on_update_checked(status, '网络不可用')
            logger.return_value.warning.assert_called_once()
        for action in (details, success, error, warning):
            action.assert_not_called()
        self.assertIsNone(self.settings._update_bar)

    def test_disabling_during_a_check_suppresses_its_result(self):
        manager = Mock()
        manager.check_for_updates.side_effect = self.blocked_check
        with patch('app.paths.IS_VELOPACK_INSTALL', True), \
             patch('tasks.update_task.updater.create_manager', return_value=manager), \
             patch.object(self.settings, 'show_update_dialog') as details:
            self.settings.check_updates_automatically()
            self.wait_until(self.entered.is_set)
            self.settings.auto_update_switch.setChecked(False)
            self.release.set()
            self.wait_until(lambda: self.settings.update_thread is None)
            details.assert_not_called()
        self.assertIsNone(self.settings._update_bar)

    def test_manual_and_automatic_checks_share_one_worker(self):
        manager = Mock()
        manager.check_for_updates.side_effect = self.blocked_check
        with patch('app.paths.IS_VELOPACK_INSTALL', True), \
             patch('tasks.update_task.updater.create_manager', return_value=manager) as create:
            self.settings.check_updates_automatically()
            self.wait_until(self.entered.is_set)
            self.settings.get_update()
            self.settings.check_updates_automatically()
            create.assert_called_once()
            self.release.set()
            self.wait_until(lambda: self.settings.update_thread is None)

    def test_manual_check_still_shows_update_details(self):
        manager = Mock()
        manager.check_for_updates.return_value = self.info
        self.ctx.app_settings.set('auto_check_updates', False)
        with patch('tasks.update_task.updater.create_manager', return_value=manager), \
             patch.object(self.settings, 'show_update_dialog') as details:
            self.settings.get_update()
            self.wait_until(lambda: self.settings.update_thread is None)
            details.assert_called_once_with(self.info)
        self.assertTrue(self.settings.versionPrimaryPushButton.isEnabled())

    def test_turning_off_auto_check_dismisses_existing_notification(self):
        self.settings.show_update_notice(self.info)
        self.settings.auto_update_switch.setChecked(False)
        self.assertIsNone(self.settings._update_bar)

    def test_notification_and_details_use_visible_window_when_settings_are_hidden(self):
        root = QWidget()
        root.resize(1000, 750)
        self.settings.setParent(root)
        self.addCleanup(root.deleteLater)
        root.show()
        self.settings.hide()
        self.settings.show_update_notice(self.info)
        self.assertIs(self.settings._update_bar.parentWidget(), root)
        with patch('ui.pages.settings_page.MessageBox') as dialog:
            dialog.return_value.exec.return_value = False
            self.settings._update_bar.findChild(PushButton).click()
        self.assertIs(dialog.call_args.args[2], root)

    def test_notification_with_action_is_centered_on_show_and_after_resize(self):
        root = QWidget()
        root.resize(850, 750)
        self.settings.setParent(root)
        self.addCleanup(root.deleteLater)
        root.show()
        self.settings.hide()
        self.settings.show_update_notice(self.info)
        bar = self.settings._update_bar
        QTest.qWait(250)
        self.assertLessEqual(abs(bar.x() * 2 + bar.width() - root.width()), 1)
        root.resize(1100, 750)
        self.qt.processEvents()
        self.assertLessEqual(abs(bar.x() * 2 + bar.width() - root.width()), 1)

    def test_result_during_shutdown_does_not_open_any_prompt(self):
        self.settings._waiting_to_close = True
        self.settings._update_check_automatic = True
        with patch.object(self.settings, 'show_update_notice') as notice, \
             patch.object(self.settings, 'show_update_dialog') as details:
            self.settings.on_update_checked('available', self.info)
            self.settings._update_check_automatic = False
            self.settings.on_update_checked('available', self.info)
        notice.assert_not_called()
        details.assert_not_called()

    def test_manager_initialization_failure_falls_back_to_other_source(self):
        task = UpdateCheckTask()
        results = []
        task.checked.connect(lambda *args: results.append(args))
        manager = Mock()
        manager.check_for_updates.return_value = self.info
        with patch('tasks.update_task.updater.source_count', return_value=2), \
             patch('tasks.update_task.updater.create_manager',
                   side_effect=[RuntimeError('连接失败'), manager]):
            task.run()
        self.assertEqual(results, [('available', self.info)])
