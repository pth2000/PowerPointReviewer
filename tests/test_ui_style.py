"""避免 Designer 页面与运行时卡片的同级文字出现字号、字重差异。"""

from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLabel
from qfluentwidgets import CardWidget, StrongBodyLabel, SubtitleLabel
from test_sync_playback import SyncPlaybackFixture

from ui import theme
from ui.pages.tools_page import ToolsInterface


class UiStyleTests(SyncPlaybackFixture):
    def setUp(self):
        super().setUp()
        self.tools = ToolsInterface(self.ctx, self.page)
        self.addCleanup(self.dispose_tools)

    def dispose_tools(self):
        self.tools.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def style(label):
        label.ensurePolished()
        font = label.font()
        return font.pixelSize(), font.pointSizeF(), font.weight()

    def test_designer_and_dynamic_card_titles_share_the_same_type_style(self):
        reference = self.style(self.settings.engineSelectPathLabel)
        self.assertEqual(reference, (-1, 10, QFont.Weight.Bold))
        for page in (self.settings.importWidget, *self.settings.preference_sections, self.tools):
            for label in page.findChildren(StrongBodyLabel):
                with self.subTest(text=label.text()):
                    self.assertEqual(self.style(label), reference)
        for label in (self.page.notesPathLabel_5, self.page.notesPathShowLabel):
            self.assertEqual(self.style(label), reference)

    def test_about_cards_keep_the_existing_compact_title_style(self):
        reference = self.style(self.settings.versionShowLabel)
        self.assertEqual(reference, (-1, 9, QFont.Weight.Bold))
        titles = [label for label in self.settings.findChildren(StrongBodyLabel)
                  if label.text() in ('自动检查更新', '使用指引', '运行日志')]
        self.assertEqual(len(titles), 3)
        for label in (*titles, self.settings.copyrightShowLabel):
            self.assertEqual(self.style(label), reference)

    def assert_settings_order(self):
        settings = self.settings
        layout = settings.verticalLayout_2
        layouts = [layout, *(section.layout() for section in settings.preference_sections)]
        sections = [current.itemAt(i).widget().text() for current in layouts
                    for i in range(current.count())
                    if isinstance(current.itemAt(i).widget(), SubtitleLabel)]
        self.assertEqual(sections, ['配音设置', '讲稿与播放', '放映控制', '外观'])
        self.assertEqual(settings.SubtitleLabel_2.text(), '关于与帮助')
        cards = [settings.CardWidget_5, settings._preview_text_card, settings.CardWidget_4,
                 settings.resume_session_switch.parentWidget(), settings.script_comments_switch.parentWidget(),
                 settings.animation_check_switch.parentWidget(), settings.countdown_card, settings.hotkey_card,
                 settings.presentation_preference_combo.parentWidget(), settings.sync_playback_switch.parentWidget(),
                 settings.page_turn_card, settings.slideshow_preferences_card,
                 settings.theme_mode_combo.parentWidget(), settings.ui_font_combo.parentWidget()]
        ordered_cards = [current.itemAt(i).widget() for current in layouts
                         for i in range(current.count())
                         if isinstance(current.itemAt(i).widget(), CardWidget)]
        indices = [ordered_cards.index(card) for card in cards]
        self.assertTrue(all(index >= 0 for index in indices))
        self.assertEqual(indices, sorted(set(indices)))
        if settings._voice_card is not None:
            self.assertLess(layout.indexOf(settings._voice_card), layout.indexOf(settings._preview_text_card))
        about_cards = [settings.importCardWidget_2, settings.auto_update_card,
                       settings.usage_guide_card, settings.log_card, settings.importCardWidget]
        about = settings.verticalLayout_6
        self.assertEqual([about.itemAt(i).widget() for i in range(about.count())
                          if isinstance(about.itemAt(i).widget(), CardWidget)], about_cards)
        for card in about_cards:
            self.assertEqual(layout.indexOf(card), -1)

    def test_workflow_categories_and_order_survive_engine_switches(self):
        self.assert_settings_order()
        with patch.object(self.ctx.tts_engine, 'get_voices_list', return_value=['zh-CN-XiaoxiaoNeural']):
            for index in (0, 1):
                self.settings.engineSelectComboBox.setCurrentIndex(index)
                self.assert_settings_order()

    def test_guide_buttons_keep_their_signals_after_moving_to_about(self):
        beginner = QSignalSpy(self.settings.usage_guide_requested)
        advanced = QSignalSpy(self.settings.advanced_guide_requested)
        self.settings.usage_guide_button.click()
        self.settings.advanced_guide_button.click()
        self.assertEqual(beginner.count(), 1)
        self.assertEqual(advanced.count(), 1)

    def test_slideshow_titles_match_independent_setting_titles(self):
        titles = self.settings.slideshow_preferences_card.findChildren(StrongBodyLabel)
        self.assertEqual(len(titles), 5)
        for label in titles:
            self.assertEqual(self.style(label), self.style(self.settings.engineSelectPathLabel))

    def test_font_family_changes_preserve_title_and_body_hierarchy(self):
        labels = (self.page.notesPathShowLabel, self.page.statusLabel,
                  self.settings.engineSelectPathLabel, self.settings.versionShowLabel,
                  self.settings.SubtitleLabel)
        before = [self.style(label) for label in labels]
        theme.apply_font('')
        self.assertEqual([self.style(label) for label in labels], before)

    def test_designer_sources_declare_the_same_roles_as_runtime_widgets(self):
        root = Path(__file__).resolve().parent.parent
        for filename, page, names in (
            ('Ui_mainwindow.ui', self.page,
             ('notesPathLabel_5', 'notesPathShowLabel')),
            ('settingInterface.ui', self.settings,
             ('engineSelectPathLabel', 'savePathLabel', 'versionShowLabel', 'copyrightShowLabel')),
        ):
            tree = ET.parse(root / filename)
            for name in names:
                with self.subTest(filename=filename, name=name):
                    source = tree.find(f".//widget[@name='{name}']")
                    self.assertEqual(source.get('class'), 'QLabel')
                    label = getattr(page, name)
                    self.assertIs(type(label), QLabel)
                    css = source.find("property[@name='styleSheet']/string").text
                    self.assertIn(f'font-size: {int(self.style(label)[1])}pt;', css)
                    self.assertIn('font-weight: 700;', css)
