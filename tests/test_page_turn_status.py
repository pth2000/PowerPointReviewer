"""设置页可见时自动更新放映状态，隐藏时停止探测。"""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEventLoop, QTimer

from app import slideshow
from test_sync_playback import SyncPlaybackFixture


class PageTurnStatusTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.settings.page_turn_status_timer.setInterval(20)

    def wait_badge(self, text):
        loop = QEventLoop()
        timeout, check = QTimer(), QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        check.setInterval(5)
        check.timeout.connect(lambda: loop.quit() if self.settings.page_turn_card.status_badge.text() == text else None)
        timeout.start(1500)
        check.start()
        loop.exec()
        timeout.stop()
        check.stop()
        self.assertEqual(self.settings.page_turn_card.status_badge.text(), text)

    def test_visible_page_detects_start_page_change_and_exit_with_sync_disabled(self):
        with patch('app.slideshow.describe', return_value='') as probe:
            self.settings.show()
            self.assertTrue(self.settings.page_turn_status_timer.isActive())
            self.assertEqual(self.settings.page_turn_card.status_badge.text(), '未检测到对应放映')
            probe.return_value = 'PowerPoint｜第 1 页'
            self.wait_badge(probe.return_value)
            probe.assert_called_with(self.source)
            probe.return_value = 'PowerPoint｜第 2 页'
            self.wait_badge(probe.return_value)
            probe.return_value = ''
            self.wait_badge('未检测到对应放映')
        self.assertFalse(self.ctx.app_settings.get('scroll_enabled'))
        self.ctx.config.save_later.assert_not_called()
        self.assertFalse(self.settings.is_dirty())

    def test_hidden_page_stops_polling_and_reopening_refreshes_immediately(self):
        with patch('app.slideshow.describe', return_value='PowerPoint｜第 1 页') as probe:
            self.settings.show()
            self.settings.hide()
            self.assertFalse(self.settings.page_turn_status_timer.isActive())
            probe.reset_mock()
            self.settings._poll_page_turn_status()
            probe.assert_not_called()
            probe.return_value = 'WPS 演示｜第 2 页'
            self.settings.show()
            self.assertEqual(self.settings.page_turn_card.status_badge.text(), probe.return_value)
            self.assertTrue(self.settings.page_turn_status_timer.isActive())

    def test_selected_window_status_updates_and_foreground_mode_clears_tag(self):
        with patch('app.slideshow.describe', return_value='') as com, \
             patch('app.window_target.describe', return_value='selected window') as window:
            self.settings.show()
            self.settings.page_turn_combo.setCurrentText('指定窗口')
            self.assertEqual(self.settings.page_turn_card.status_badge.text(), '未选择窗口')
            self.ctx.app_settings.set('page_turn_window', {'hwnd': 123})
            self.wait_badge('窗口有效')
            window.return_value = ''
            self.wait_badge('窗口已关闭')
            com.reset_mock()
            window.reset_mock()
            self.settings.page_turn_combo.setCurrentText('前台按键')
            self.settings._poll_page_turn_status()
            self.assertTrue(self.settings.page_turn_card.status_badge.isHidden())
            com.assert_not_called()
            window.assert_not_called()

    def test_live_show_collection_is_checked_for_both_powerpoint_and_wps(self):
        selected_view = NS(CurrentShowPosition=1)
        selected = NS(Presentation=NS(FullName=str(self.source)), View=selected_view)
        other = NS(Presentation=NS(FullName='other.pptx'), View=NS(CurrentShowPosition=7))
        pp = NS(SlideShowWindows=Mock(Count=1))
        pp.SlideShowWindows.side_effect = lambda index: other
        wps = NS(SlideShowWindows=Mock(Count=0))
        wps.SlideShowWindows.side_effect = lambda index: selected
        with patch('app.slideshow._running_apps', side_effect=lambda: iter([('PowerPoint', pp), ('WPS 演示', wps)])):
            self.settings.show()
            self.assertEqual(self.settings.page_turn_card.status_badge.text(), '未检测到对应放映')
            wps.SlideShowWindows.Count = 1
            self.wait_badge('WPS 演示｜第 1 页')
            selected_view.CurrentShowPosition = 2
            self.wait_badge('WPS 演示｜第 2 页')
            wps.SlideShowWindows.Count = 0
            self.wait_badge('未检测到对应放映')
            pp.SlideShowWindows.side_effect = lambda index: selected
            self.wait_badge('PowerPoint｜第 2 页')
            pp.SlideShowWindows.Count = 0
            self.wait_badge('未检测到对应放映')


if __name__ == '__main__':
    unittest.main()
