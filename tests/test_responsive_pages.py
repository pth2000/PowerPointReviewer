"""主页面缩放保留编辑状态、功能分区和可访问的控件。"""

from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSignalBlocker
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import CaptionLabel, FluentIcon, FluentWindow, PushButton, SubtitleLabel
from test_sync_playback import SyncPlaybackFixture

from app.window import Window
from ui.pages.tools_page import ToolsInterface


class ResponsivePageTests(SyncPlaybackFixture):
    def settle(self, page, width, height):
        page.resize(width, height)
        page.show()
        QTest.qWait(20)

    def test_tools_descriptions_fit_on_one_line_at_default_window_width(self):
        font_file = Path('C:/Windows/Fonts/msyh.ttc')
        if not font_file.is_file():
            self.skipTest('需要微软雅黑验证工具页的中文排版')
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        self.addCleanup(lambda: QFontDatabase.removeApplicationFont(font_id))
        tools = ToolsInterface(self.ctx, self.page)
        self.addCleanup(tools.deleteLater)
        self.settle(tools, 732, 662)
        for card in tools.processing_cards + tools.export_cards:
            caption = card.findChild(CaptionLabel)
            with self.subTest(caption=caption.text()):
                self.assertLessEqual(caption.heightForWidth(caption.width()), caption.fontMetrics().height() + 2)
                for button in card.findChildren(PushButton):
                    self.assertTrue(card.rect().contains(button.geometry()))

    def test_settings_reflow_whole_sections_and_keep_pending_voice_changes(self):
        settings = self.settings
        controls = dict(settings.dynamic_option_widgets)
        speed = controls['rate']
        speed.setValue(speed.value() + 1)
        value = speed.value()
        for width, expected in ((850, 1), (1500, 2), (1000, 1), (1800, 2)):
            self.settle(settings, width, 850)
            self.assertEqual(settings.settings_columns.columns, expected)
            self.assertEqual(settings.dynamic_option_widgets, controls)
            self.assertEqual(speed.value(), value)
            self.assertTrue(settings.is_dirty())
            self.assertEqual(settings.bgScrollArea.horizontalScrollBar().maximum(), 0)
            voice = settings.importWidget
            left, right = settings.settings_columns.panes
            if expected == 2:
                self.assertEqual(left.y(), right.y())
                self.assertLess(left.geometry().right(), right.x())
                self.assertTrue(right.isVisible())
            else:
                self.assertIs(voice.parentWidget(), left)
                self.assertFalse(right.isVisible())
            for control in (settings.engineSelectComboBox, settings.savePushButton,
                            settings.slideshow_display_combo, settings.countdown_seconds_spin):
                card = control.parentWidget()
                self.assertTrue(card.rect().contains(control.geometry()))

    def test_wide_settings_keep_dynamic_engine_controls_before_save(self):
        settings = self.settings
        self.settle(settings, 1500, 850)
        with patch.object(self.ctx.tts_engine, 'get_voices_list', return_value=['测试音色']):
            for mode in ('qwentts', 'bailian', 'edge'):
                self.ctx.tts_engine.set_mode(mode)
                settings.apply_engine_schema_to_ui()
                settings.setup_voices_list()
                QTest.qWait(20)
                layout = settings.verticalLayout_2
                for card in settings.dynamic_option_cards:
                    self.assertLess(layout.indexOf(card), layout.indexOf(settings.CardWidget_4))
                    self.assertIs(card.parentWidget(), settings.importWidget)
                self.assertEqual(settings.settings_columns.columns, 2)
                self.assertEqual(settings.bgScrollArea.horizontalScrollBar().maximum(), 0)

    def test_settings_preferences_remain_in_reading_order_and_about_is_last(self):
        settings = self.settings
        for width in (850, 1500):
            self.settle(settings, width, 850)
            sections = settings.preference_sections
            titles = [section.findChild(SubtitleLabel).text() for section in sections]
            self.assertEqual(titles, ['讲稿与播放', '放映控制', '外观'])
            reading_order = [pane.layout().itemAt(index).widget()
                             for pane in settings.settings_columns.panes
                             for index in range(pane.layout().count())
                             if pane.layout().itemAt(index).widget() is not None]
            self.assertEqual(reading_order, [settings.importWidget, *sections, settings.about_section])
            settings.bgScrollArea.ensureWidgetVisible(settings.usage_guide_button)
            self.qt.processEvents()
            point = settings.usage_guide_button.mapTo(settings.bgScrollArea.viewport(), QPoint(1, 1))
            self.assertTrue(settings.bgScrollArea.viewport().rect().contains(point))

    def test_home_preview_keeps_fixed_height_as_window_grows(self):
        page = self.page
        preview = page.playing_script_preview
        heights = []
        for height in (560, 1000, 560):
            self.settle(page, 1000, height)
            heights.append(preview.height())
            self.assertEqual(page.playCardWidget_3.height(), 80)
            self.assertEqual(page.playbackOverview.height(), 132)
            self.assertEqual(page.bgScrollArea.horizontalScrollBar().maximum(), 0)
        self.assertEqual(heights[1], heights[0])
        self.assertEqual(heights[0], heights[2])
        self.assertEqual(page.bgScrollArea.verticalScrollBar().maximum(), 0)
        self.assertGreater(page.minimumHeight(), 560)
        controls = page.playCardWidget_3
        bottom = controls.mapTo(page.bgScrollArea.viewport(), controls.rect().bottomRight())
        self.assertTrue(page.bgScrollArea.viewport().rect().contains(bottom))

    def test_settings_balance_at_section_boundaries_for_short_and_long_voice_schemas(self):
        settings = self.settings
        self.settle(settings, 1500, 850)
        panel = settings.settings_columns
        with patch.object(self.ctx.tts_engine, 'get_voices_list', return_value=['测试音色']):
            for mode in ('edge', 'qwentts'):
                self.ctx.tts_engine.set_mode(mode)
                settings.apply_engine_schema_to_ui()
                settings.setup_voices_list()
                QTest.qWait(30)
                panel._update_columns()
                width = (panel.width() - panel.COLUMN_SPACING) // 2
                heights = [panel._section_height(section, width) + 20 for section in panel.sections]
                split = panel._assignment[1]
                difference = abs(sum(heights[:split]) - sum(heights[split:]))
                best = min(abs(sum(heights[:i]) - sum(heights[i:])) for i in range(1, len(heights)))
                self.assertEqual(difference, best)
                if mode == 'edge':
                    self.assertGreater(split, 1)

    def test_home_keeps_all_controls_visible_while_generation_progress_appears(self):
        page = self.page
        self.settle(page, 800, 560)
        minimum = page.minimumHeight()
        for visible in (True, False):
            page.IndeterminateProgressBar.setVisible(visible)
            QTest.qWait(20)
            self.assertEqual(page.minimumHeight(), minimum)
            self.assertFalse(page.bgScrollArea.verticalScrollBar().isVisible())
            self.assertEqual(page.bgScrollArea.verticalScrollBar().maximum(), 0)
            control = page.playCardWidget_3
            bottom = control.mapTo(page.bgScrollArea.viewport(), control.rect().bottomRight())
            self.assertTrue(page.bgScrollArea.viewport().rect().contains(bottom))

    def test_main_window_minimum_height_includes_navigation_frame(self):
        window = FluentWindow()
        window.addSubInterface(self.page, FluentIcon.HOME, '主页')
        try:
            Window._set_home_minimum_height(window, self.page.minimumHeight())
            window.resize(760, 560)
            window.show()
            QTest.qWait(30)
            self.assertGreaterEqual(self.page.height(), self.page.minimumHeight())
            self.assertEqual(self.page.bgScrollArea.verticalScrollBar().maximum(), 0)
            control = self.page.playCardWidget_3
            bottom = control.mapTo(window, control.rect().bottomRight())
            self.assertTrue(window.rect().contains(bottom))
        finally:
            with QSignalBlocker(window.stackedWidget):
                window.stackedWidget.removeWidget(self.page)
            self.page.setParent(None)
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_settings_focus_survives_moving_a_section_between_columns(self):
        settings = self.settings
        self.settle(settings, 1500, 850)
        control = settings.presentation_preference_combo
        control.setFocus()
        self.assertIs(QApplication.focusWidget(), control)
        self.settle(settings, 850, 850)
        self.assertIs(QApplication.focusWidget(), control)
        self.settle(settings, 1500, 850)
        self.assertIs(QApplication.focusWidget(), control)

    def test_home_preview_does_not_resize_as_content_or_playback_state_changes(self):
        page = self.page
        self.settle(page, 1000, 1000)
        preview = page.playing_script_preview
        height = preview.height()
        for text, playing, countdown in (('短句', False, False), ('长讲稿' * 500, True, False),
                                         ('准备朗读', True, True), ('', False, False)):
            preview.set_content(text, playing=playing, countdown=countdown)
            self.qt.processEvents()
            self.assertEqual(preview.height(), height)
        page.project.current_index = 1
        page.set_current_label_text()
        self.assertEqual(preview.height(), height)

    def test_large_font_keeps_setting_controls_inside_the_cards(self):
        settings = self.settings
        font = QFont(settings.font())
        font.setPointSize(14)
        settings.setFont(font)
        for width in (850, 1500):
            self.settle(settings, width, 850)
            self.assertEqual(settings.bgScrollArea.horizontalScrollBar().maximum(), 0)
            for control in (settings.engineSelectComboBox, settings.presentation_preference_combo,
                            settings.slideshow_display_combo):
                self.assertTrue(control.parentWidget().rect().contains(control.geometry()))
