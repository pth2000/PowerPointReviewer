"""首页快捷控制与设置页保持一致，并正确处理不可播放的讲稿。"""

from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionFrame
from test_sync_playback import SyncPlaybackFixture


class PlaybackControlsTests(SyncPlaybackFixture):
    def test_home_switch_and_settings_share_one_preference(self):
        control = self.page.playCardWidget_3.sync_switch
        control.setChecked(True)
        self.assertTrue(self.page.sync_playback_enabled())
        self.assertTrue(self.settings.sync_playback_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()
        self.ctx.config.reset_mock()
        self.settings.sync_playback_switch.setChecked(False)
        self.assertFalse(control.isChecked())
        self.assertFalse(self.page.sync_playback_enabled())
        self.ctx.config.save_later.assert_called_once()

    def test_restricted_project_cannot_change_sync_from_home(self):
        self.page.project.presentation_path = None
        self.page.project.presentation_link = None
        self.page.refresh_presentation_state()
        control = self.page.playCardWidget_3.sync_switch
        self.assertFalse(control.isEnabled())
        control.setChecked(True)
        self.assertFalse(control.isChecked())
        self.assertFalse(self.page.sync_playback_enabled())
        self.ctx.config.save_later.assert_not_called()

    def test_countdown_button_toggles_without_opening_a_popup(self):
        controls = self.page.playCardWidget_3
        controls.countdown_button.click()
        self.assertIsNone(QApplication.activePopupWidget())
        self.assertFalse(controls.countdown_button.isChecked())
        self.assertFalse(controls.countdown_seconds.isEnabled())
        self.assertTrue(controls.countdown_seconds.isHidden())
        self.assertEqual(controls.countdown_seconds.accessibleName(), '倒计时时长：5 秒')
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.assertFalse(self.settings.countdown_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()
        controls.countdown_button.click()
        self.assertTrue(controls.countdown_button.isChecked())
        self.assertFalse(controls.countdown_seconds.isHidden())
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.assertEqual(controls.countdown_seconds.accessibleName(), '倒计时时长：5 秒')

    def test_inline_seconds_commit_on_outside_click_and_escape_cancels(self):
        self.page.show()
        self.qt.processEvents()
        controls = self.page.playCardWidget_3
        seconds = controls.countdown_seconds
        seconds.click()
        QTest.keyClicks(seconds.editor, '8')
        self.assertEqual(seconds.value(), 5)
        self.ctx.config.save_later.assert_not_called()
        QTest.mouseClick(controls, Qt.MouseButton.LeftButton, pos=QPoint(controls.width()-4, 4))
        self.assertFalse(seconds.editor.isVisible())
        self.assertEqual(seconds.value(), 8)
        self.assertEqual(self.settings.countdown_seconds_spin.value(), 8)
        self.assertEqual(self.ctx.app_settings.get('countdown_seconds'), 8)
        self.ctx.config.reset_mock()
        seconds.click()
        QTest.keyClicks(seconds.editor, '12')
        QTest.keyClick(seconds.editor, Qt.Key.Key_Escape)
        self.assertEqual(seconds.value(), 8)
        self.assertFalse(seconds.editor.isVisible())
        self.ctx.config.save_later.assert_not_called()

    def test_countdown_label_and_keyboard_toggle_the_same_preference_once(self):
        self.page.show()
        self.qt.processEvents()
        controls = self.page.playCardWidget_3
        QTest.mouseClick(controls.countdown_label, Qt.MouseButton.LeftButton)
        self.assertFalse(controls.countdown_button.isChecked())
        self.assertFalse(self.settings.countdown_switch.isChecked())
        self.assertTrue(controls.countdown_seconds.isHidden())
        self.ctx.config.save_later.assert_called_once()
        self.ctx.config.reset_mock()
        controls.countdown_button.setFocus()
        QTest.keyClick(controls.countdown_button, Qt.Key.Key_Space)
        self.assertTrue(controls.countdown_button.isChecked())
        self.assertTrue(self.settings.countdown_switch.isChecked())
        self.ctx.config.save_later.assert_called_once()
        self.ctx.config.reset_mock()
        controls.countdown_button.setEnabled(False)
        QTest.mouseClick(controls.countdown_label, Qt.MouseButton.LeftButton)
        self.assertTrue(controls.countdown_button.isChecked())
        self.ctx.config.save_later.assert_not_called()

    def test_countdown_and_sync_labels_share_typography_and_colors(self):
        controls = self.page.playCardWidget_3
        first, second = controls.countdown_label, controls.sync_label
        self.assertIs(type(first), type(second))
        for property_name in ('family', 'pointSizeF', 'pixelSize', 'weight'):
            self.assertEqual(getattr(first.font(), property_name)(),
                             getattr(second.font(), property_name)())
        self.assertEqual(first.lightColor, second.lightColor)
        self.assertEqual(first.darkColor, second.darkColor)
        self.assertEqual(controls.countdown_button.size(), controls.previous_button.size())

    def test_inline_seconds_cancel_when_page_hides_or_settings_change(self):
        self.page.show()
        self.qt.processEvents()
        seconds = self.page.currentSpinBox
        seconds.click()
        QTest.keyClicks(seconds.editor, '12')
        self.settings.countdown_seconds_spin.setValue(9)
        self.assertFalse(seconds.editor.isVisible())
        self.assertEqual(seconds.value(), 9)
        seconds.click()
        QTest.keyClicks(seconds.editor, '15')
        self.ctx.config.reset_mock()
        self.page.hide()
        self.assertEqual(seconds.value(), 9)
        self.ctx.config.save_later.assert_not_called()

    def test_inline_seconds_reject_zero_and_accept_one(self):
        seconds = self.page.currentSpinBox
        for value in ('0', '11'):
            with self.subTest(value=value), patch.object(self.page, 'create_warning_info_bar') as warning:
                seconds.click()
                seconds.editor.setText(value)
                QTest.keyClick(seconds.editor, Qt.Key.Key_Return)
                self.assertEqual(seconds.value(), 5)
                warning.assert_called_once()
        seconds.click()
        seconds.editor.setText('1')
        QTest.keyClick(seconds.editor, Qt.Key.Key_Return)
        self.assertEqual(seconds.value(), 1)
        self.assertEqual(self.settings.countdown_seconds_spin.value(), 1)

    def test_inline_editor_keeps_enough_text_space_for_two_digits(self):
        self.page.show()
        self.qt.processEvents()
        seconds = self.page.currentSpinBox
        seconds.click()
        editor = seconds.editor
        geometry = editor.geometry()
        for text in ('5', '10'):
            editor.setText(text)
            self.qt.processEvents()
            self.assertEqual(editor.geometry(), geometry)
            option = QStyleOptionFrame()
            editor.initStyleOption(option)
            contents = editor.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, editor)
            margins = editor.textMargins()
            usable_width = contents.width() - margins.left() - margins.right() - 4
            self.assertGreaterEqual(usable_width, editor.fontMetrics().horizontalAdvance(text))

    def test_no_audio_disables_all_home_countdown_controls_without_changing_preferences(self):
        page, settings, _ctx = self.make_ui(ready=False)
        controls = page.playCardWidget_3
        self.assertFalse(page.playButton.isEnabled())
        self.assertFalse(page.resetButton.isEnabled())
        self.assertFalse(controls.sync_switch.isEnabled())
        self.assertFalse(controls.countdown_button.isEnabled())
        self.assertFalse(controls.countdown_label.isEnabled())
        self.assertFalse(controls.countdown_seconds.isEnabled())
        controls.countdown_button.click()
        self.assertTrue(controls.countdown_button.isChecked())
        settings.countdown_switch.setChecked(False)
        self.assertFalse(controls.countdown_button.isChecked())
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.assertFalse(settings.countdown_switch.isChecked())
        self.assertFalse(page.currentSpinBox.isEnabled())
        settings.countdown_switch.setChecked(True)
        self.assertTrue(page.currentSwitch.isChecked())
        self.assertFalse(page.currentSwitch.isEnabled())
        self.assertFalse(page.currentSpinBox.isEnabled())

    def test_icon_buttons_expose_actions_without_drawing_elided_text(self):
        controls = self.page.playCardWidget_3
        for button in (controls.play_button, controls.previous_button,
                       controls.next_button, controls.reset_button):
            self.assertEqual(button.text(), '')
            self.assertTrue(button.accessibleName())
            self.assertTrue(button.toolTip())
        self.page._playing_sequence = True
        self.page._refresh_play_button()
        self.assertEqual(self.page.playButton.text(), '')
        self.assertEqual(self.page.playButton.accessibleName(), '停止')
        self.assertEqual(self.page.playButton.toolTip(), '停止')

    def test_countdown_follows_audio_readiness_without_saving_new_preferences(self):
        controls = self.page.playCardWidget_3
        self.page.project.audio_loaded = False
        self.page.check_import()
        for control in (controls.countdown_button, controls.countdown_label, controls.countdown_seconds):
            self.assertFalse(control.isEnabled())
        self.assertTrue(controls.countdown_button.isChecked())
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.page.project.audio_loaded = True
        self.page.check_import()
        for control in (controls.countdown_button, controls.countdown_label, controls.countdown_seconds):
            self.assertTrue(control.isEnabled())
        self.ctx.config.save_later.assert_not_called()

    def test_becoming_busy_disables_countdown_and_cancels_unconfirmed_input(self):
        self.page.show()
        self.qt.processEvents()
        controls = self.page.playCardWidget_3
        controls.countdown_seconds.click()
        QTest.keyClicks(controls.countdown_seconds.editor, '12')
        with patch.object(self.page, 'is_busy', return_value=True):
            self.page.refresh_presentation_state()
            for control in (controls.countdown_button, controls.countdown_label, controls.countdown_seconds):
                self.assertFalse(control.isEnabled())
            self.assertFalse(controls.countdown_seconds.editor.isVisible())
            QTest.mouseClick(controls.countdown_label, Qt.MouseButton.LeftButton)
            self.assertTrue(controls.countdown_button.isChecked())
        self.page.refresh_presentation_state()
        self.assertTrue(controls.countdown_button.isEnabled())
        self.assertTrue(controls.countdown_label.isEnabled())
        self.assertTrue(controls.countdown_seconds.isEnabled())
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.ctx.config.save_later.assert_not_called()

    def test_play_stays_centered_and_regions_do_not_overlap(self):
        self.page.show()
        self.qt.processEvents()
        controls = self.page.playCardWidget_3
        for width in (640, 700, 920):
            with self.subTest(width=width):
                controls.resize(width, 80)
                self.qt.processEvents()
                self.assertLessEqual(abs(controls.play_button.geometry().center().x()
                                         - controls.rect().center().x()), 1)
                self.assertLess(controls.countdown_seconds.geometry().right(),
                                controls.return_button.geometry().left())
                self.assertLess(controls.reset_button.geometry().right(),
                                controls.sync_group.geometry().left())
                self.assertLessEqual(abs(controls.return_button.geometry().center().x()
                                         + controls.reset_button.geometry().center().x()
                                         - 2 * controls.rect().center().x()), 1)
                self.assertEqual(controls.height(), 80)


class CountdownPlaybackTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.elapsed_ms = 0
        self.page.countdown.elapsed = Mock()
        self.page.countdown.elapsed.start.side_effect = lambda: setattr(self, 'elapsed_ms', 0)
        self.page.countdown.elapsed.elapsed.side_effect = lambda: self.elapsed_ms
        self.page.countdown._beep = Mock()
        self.page.project.media_list = [self.source.parent / f'notes{number}.wav' for number in range(2)]
        for method in ('setSource', 'play', 'stop'):
            patcher = patch.object(self.page.player, method)
            patcher.start()
            self.addCleanup(patcher.stop)

    def advance_countdown(self):
        self.elapsed_ms += 1000
        self.page.countdown._tick()

    def test_display_follows_elapsed_seconds_then_hides_without_changing_preferences(self):
        controls = self.page.playCardWidget_3
        seconds = controls.countdown_seconds
        self.page.play_wait()
        for number in range(5, 0, -1):
            self.assertFalse(seconds.isHidden())
            self.assertEqual(seconds.accessibleName(), f'倒计时剩余：{number} 秒')
            self.assertEqual(seconds.value(), 5)
            self.assertEqual(self.settings.countdown_seconds_spin.value(), 5)
            self.advance_countdown()
        self.assertTrue(self.page.is_play_notes)
        self.assertTrue(seconds.isHidden())
        self.assertTrue(controls.countdown_button.isChecked())
        self.ctx.config.save_later.assert_not_called()

    def test_stop_and_restart_restore_configured_duration_and_keep_layout(self):
        self.page.show()
        self.qt.processEvents()
        controls = self.page.playCardWidget_3
        geometry = controls.play_button.geometry()
        self.page.play_wait()
        self.advance_countdown()
        self.page.stop_audio()
        self.assertEqual(controls.countdown_seconds.accessibleName(), '倒计时时长：5 秒')
        self.assertFalse(controls.countdown_seconds.isHidden())
        self.assertEqual(controls.countdown_seconds.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.page.play_wait()
        self.assertEqual(controls.countdown_seconds.accessibleName(), '倒计时剩余：5 秒')
        for _ in range(5):
            self.advance_countdown()
        self.assertTrue(controls.countdown_seconds.isHidden())
        self.assertEqual(controls.play_button.geometry(), geometry)
        self.ctx.config.save_later.assert_not_called()

    def test_finishing_narration_restores_setting_for_next_playback(self):
        self.page.play_notes()
        self.assertTrue(self.page.currentSpinBox.isHidden())
        self.page.timeout_play_next_audio()
        self.page.timeout_play_next_audio()
        self.assertFalse(self.page._playing_sequence)
        self.assertFalse(self.page.currentSpinBox.isHidden())
        self.assertEqual(self.page.currentSpinBox.accessibleName(), '倒计时时长：5 秒')
        self.assertTrue(self.page.currentSwitch.isEnabled())
        self.assertTrue(self.page.playCardWidget_3.sync_switch.isEnabled())
        self.assertTrue(self.settings.countdown_switch.isEnabled())
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())

    def test_runtime_display_and_settings_cannot_be_edited_until_stopped(self):
        seconds = self.page.currentSpinBox
        self.page.play_wait()
        seconds.click()
        self.assertFalse(seconds._editing)
        self.settings.countdown_seconds_spin.setValue(8)
        self.assertEqual(seconds.accessibleName(), '倒计时剩余：5 秒')
        self.assertEqual(seconds.value(), 5)
        self.assertEqual(self.settings.countdown_seconds_spin.value(), 5)
        self.advance_countdown()
        self.assertEqual(seconds.accessibleName(), '倒计时剩余：4 秒')
        self.page.stop_audio()
        self.assertEqual(seconds.accessibleName(), '倒计时时长：5 秒')
        self.ctx.config.save_later.assert_not_called()
        self.settings.countdown_seconds_spin.setValue(8)
        self.assertEqual(seconds.value(), 8)
        self.ctx.config.save_later.assert_called_once()

    def test_side_preferences_lock_for_countdown_narration_and_gaps_without_stopping_sync(self):
        self.page.set_sync_playback_enabled(True)
        self.ctx.config.reset_mock()
        controls = self.page.playCardWidget_3
        self.page.play_wait()
        for stage in ('countdown', 'narration', 'gap'):
            with self.subTest(stage=stage):
                self.assertTrue(self.page.can_sync_playback())
                self.assertTrue(self.page.sync_playback_enabled())
                for control in (controls.countdown_button, controls.countdown_label,
                                controls.countdown_seconds, controls.sync_switch, controls.sync_label,
                                self.settings.countdown_switch, self.settings.countdown_seconds_spin,
                                self.settings.sync_playback_switch):
                    self.assertFalse(control.isEnabled())
                self.assertTrue(controls.play_button.isEnabled())
                self.assertTrue(controls.next_button.isEnabled())
                if stage == 'countdown':
                    for _ in range(5):
                        self.advance_countdown()
                elif stage == 'narration':
                    self.page.next_timer.start(10000)
                    self.page._refresh_play_button()
        self.page.stop_audio()
        for control in (controls.countdown_button, controls.countdown_label,
                        controls.countdown_seconds, controls.sync_switch, controls.sync_label,
                        self.settings.countdown_switch, self.settings.countdown_seconds_spin,
                        self.settings.sync_playback_switch):
            self.assertTrue(control.isEnabled())
        self.ctx.config.save_later.assert_not_called()

    def test_preparation_locks_preferences_and_cancellation_restores_them(self):
        self.page.playback_show.preparing = True
        self.page._refresh_play_button()
        controls = self.page.playCardWidget_3
        self.assertFalse(controls.countdown_button.isEnabled())
        self.assertFalse(controls.countdown_seconds.isEnabled())
        self.assertFalse(controls.sync_switch.isEnabled())
        self.assertFalse(self.settings.countdown_switch.isEnabled())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.page.stop_audio()
        self.assertFalse(self.page.playback_show.preparing)
        self.assertTrue(controls.countdown_button.isEnabled())
        self.assertTrue(controls.sync_switch.isEnabled())
        self.assertTrue(self.settings.countdown_switch.isEnabled())
        self.assertTrue(self.settings.sync_playback_switch.isEnabled())

    def test_disabled_controls_reject_programmatic_changes_without_persisting(self):
        self.page.set_sync_playback_enabled(True)
        self.ctx.config.reset_mock()
        self.page.play_wait()
        controls = self.page.playCardWidget_3
        controls.countdown_button.setChecked(False)
        controls.countdown_seconds.setValue(12)
        controls.sync_switch.setChecked(False)
        self.settings.countdown_switch.setChecked(False)
        self.settings.countdown_seconds_spin.setValue(12)
        self.settings.sync_playback_switch.setChecked(False)
        self.page.set_countdown_preferences(False, 12)
        self.page.set_sync_playback_enabled(False)
        follow = self.settings.slideshow_preference_switches['slideshow_follow_page']
        before = self.ctx.app_settings.get('slideshow_follow_page')
        follow.setChecked(not before)
        self.assertEqual(follow.isChecked(), before)
        for control in (controls.countdown_button, controls.sync_switch,
                        self.settings.countdown_switch, self.settings.sync_playback_switch):
            self.assertTrue(control.isChecked())
        self.assertEqual(controls.countdown_seconds.value(), 5)
        self.assertEqual(self.settings.countdown_seconds_spin.value(), 5)
        self.assertTrue(self.ctx.app_settings.get('countdown_enabled'))
        self.assertEqual(self.ctx.app_settings.get('countdown_seconds'), 5)
        self.assertTrue(self.ctx.app_settings.get('scroll_enabled'))
        self.assertTrue(self.page._playing_sequence)
        self.ctx.config.save_later.assert_not_called()

    def test_unlock_preserves_unavailable_states_and_disabled_countdown_preference(self):
        self.page.currentSwitch.setChecked(False)
        self.ctx.config.reset_mock()
        self.page.play_notes()
        self.page.project.presentation_path = None
        self.page.project.presentation_link = None
        self.page.refresh_presentation_state()
        self.page.stop_audio()
        controls = self.page.playCardWidget_3
        self.assertTrue(controls.countdown_button.isEnabled())
        self.assertFalse(controls.countdown_seconds.isEnabled())
        self.assertTrue(controls.countdown_seconds.isHidden())
        self.assertFalse(controls.sync_switch.isEnabled())
        self.assertFalse(self.settings.sync_playback_switch.isEnabled())
        self.assertTrue(self.settings.countdown_switch.isEnabled())
        self.assertFalse(self.settings.countdown_seconds_spin.isEnabled())
        self.ctx.config.save_later.assert_not_called()

    def test_disabled_countdown_goes_straight_to_narration(self):
        self.page.currentSwitch.setChecked(False)
        self.ctx.config.reset_mock()
        with patch.object(self.page, 'create_warning_info_bar') as warning:
            self.page._continue_audio_playback()
        warning.assert_not_called()
        self.assertTrue(self.page.is_play_notes)
        self.assertTrue(self.page._playing_sequence)
        self.assertTrue(self.page.currentSpinBox.isHidden())
        self.assertFalse(self.page.countdown.timer.isActive())
        self.ctx.config.save_later.assert_not_called()
