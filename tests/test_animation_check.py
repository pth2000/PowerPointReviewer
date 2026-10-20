"""导入后的点击步骤预检与继续/取消生成流程。"""

from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from pptx import Presentation
from pptx.oxml import parse_xml
from PySide6.QtCore import QCoreApplication, QEvent, QTimer

from app.presentation.animation import AnimationIssue, check_alignment
from app.audio.playback import PlaybackBus
from app.settings_store import AppSettings
from ui.dialogs.animation_check_dialog import AnimationCheckDialog

from qt_test_case import QtTestCase


P = 'http://schemas.openxmlformats.org/presentationml/2006/main'


def make_deck(path, pages):
    """Create ordinary click groups with optional automatic or interactive groups."""
    ppt = Presentation()
    for groups in pages:
        slide = ppt.slides.add_slide(ppt.slide_layouts[6])
        slide.notes_slide.notes_text_frame.text = '讲稿'
        if groups is None:
            continue
        nodes = []
        for index, effects in enumerate(groups):
            auto = all(kind in ('withEffect', 'afterEffect') for kind in effects)
            conditions = '<p:cond delay="indefinite"/>'
            if auto:
                conditions += '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond>'
            effects_xml = ''.join(f'<p:par><p:cTn nodeType="{kind}"/></p:par>' for kind in effects)
            nodes.append(f'<p:par><p:cTn id="{index + 3}"><p:stCondLst>{conditions}</p:stCondLst>'
                         f'<p:childTnLst>{effects_xml}</p:childTnLst></p:cTn></p:par>')
        slide._element.append(parse_xml(
            f'<p:timing xmlns:p="{P}"><p:tnLst><p:seq><p:cTn nodeType="mainSeq">'
            f'<p:childTnLst>{"".join(nodes)}</p:childTnLst></p:cTn></p:seq></p:tnLst></p:timing>'))
    ppt.save(path)
    return path


def segments(*counts):
    return [{'page': page, 'text': f'第 {part} 段'}
            for page, count in enumerate(counts, 1) for part in range(count)]


class AnimationCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / '讲稿.pptx'

    def test_click_groups_include_paragraphs_but_not_with_or_after_effects(self):
        make_deck(self.source, [None,
                               [('clickEffect', 'withEffect', 'afterEffect'), ('clickEffect',)],
                               [('clickEffect',), ('clickPar',), ('clickPar',)],
                               [('afterEffect', 'withEffect')]])
        original = self.source.read_bytes()
        self.assertEqual(check_alignment(self.source, segments(1, 3, 4, 1)), [])
        self.assertEqual(self.source.read_bytes(), original)

    def test_only_mismatching_pages_are_reported_in_slide_order(self):
        make_deck(self.source, [[('clickEffect',)], [('clickEffect',), ('clickEffect',)], None])
        issues = check_alignment(self.source, segments(3, 2, 1))
        self.assertEqual([(i.page, i.segments, i.clicks) for i in issues], [(1, 3, 1), (2, 2, 2)])
        self.assertIn('多出 1', issues[0].reason)
        self.assertIn('还有 1', issues[1].reason)

    def test_interactive_media_and_unknown_animation_are_not_counted_as_zero(self):
        make_deck(self.source, [[('interactiveSeq',)], [('clickEffect',)], [('unknown',)]])
        ppt = Presentation(self.source)
        ppt.slides[1]._element.find(f'{{{P}}}timing').append(parse_xml(f'<p:video xmlns:p="{P}"/>'))
        ppt.save(self.source)
        issues = check_alignment(self.source, segments(1, 1, 1))
        self.assertEqual([i.page for i in issues], [1, 2, 3])
        self.assertTrue(all(i.clicks is None for i in issues))

    def test_document_order_is_used_instead_of_slide_part_filenames(self):
        make_deck(self.source, [None, [('clickEffect',)]])
        ppt = Presentation(self.source)
        ids = ppt.slides._sldIdLst
        ids.insert(0, ids[-1])
        ppt.save(self.source)
        self.assertEqual(check_alignment(self.source, segments(2, 1)), [])

    def test_unreferenced_slides_are_ignored_and_missing_page_is_reported(self):
        make_deck(self.source, [None, [('clickEffect',)]])
        self.assertEqual(check_alignment(self.source, segments(1)), [])
        issues = check_alignment(self.source, [{'page': 3, 'text': '无对应页'}])
        self.assertEqual(issues[0].page, 3)
        self.assertIn('超出', issues[0].reason)

    def test_independent_script_is_skipped_and_old_ppt_is_unconfirmed(self):
        self.assertEqual(check_alignment(None, segments(2)), [])
        self.assertIsNone(check_alignment(self.source.with_suffix('.ppt'), segments(2))[0].clicks)

    def test_blank_pages_are_reported_without_click_animations(self):
        make_deck(self.source, [None, None, None])
        current = [{'page': 1, 'text': '正常讲稿'},
                   {'page': 2, 'text': ' \n\t'},
                   {'page': 3, 'text': ''}]
        issues = check_alignment(self.source, current)
        self.assertEqual([(i.page, i.segments, i.clicks, i.reason) for i in issues],
                         [(page, 0, 0, '此页没有可朗读的讲稿') for page in (2, 3)])

    def test_blank_pages_keep_animation_findings_in_the_same_row(self):
        make_deck(self.source, [[('clickEffect',)], [('interactiveSeq',)]])
        issues = check_alignment(self.source, [{'page': 1, 'text': ''}, {'page': 2, 'text': ''}])
        self.assertEqual([(i.page, i.segments, i.clicks) for i in issues], [(1, 0, 1), (2, 0, None)])
        self.assertTrue(all('此页没有可朗读的讲稿' in i.reason for i in issues))
        self.assertIn('还有 1 个点击步骤', issues[0].reason)
        self.assertIn('点击指定对象', issues[1].reason)

    def test_empty_segments_warn_even_when_click_counts_match(self):
        make_deck(self.source, [[('clickEffect',)]])
        issues = check_alignment(self.source, [{'page': 1, 'text': ''}, {'page': 1, 'text': ' '}])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0], AnimationIssue(1, 0, 1, '此页没有可朗读的讲稿'))


class AnimationGenerationTests(QtTestCase):

    def setUp(self):
        from ui.pages.reviewer_page import PPTReviewer
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = make_deck(Path(self.directory.name) / '讲稿.pptx', [None])
        ctx = NS(app_settings=AppSettings(), config=Mock(), playback_bus=PlaybackBus(),
                 hotkeys=Mock(), slideshow_watcher=Mock(), tts_engine=Mock())
        with patch('app.paths.ensure_runtime_dirs'):
            self.page = PPTReviewer(ctx)
        self.page.resize(1000, 800)
        self.page.presentation_path = self.source
        self.page.notes = {1: '第一段●第二段'}
        self.page.create_error_info_bar = Mock()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.page.presentation_monitor.shutdown()
        self.page.stop_audio()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def generate(self, decision):
        with patch.object(AnimationCheckDialog, 'exec', return_value=decision) as dialog, \
             patch.object(self.page.save_thread, 'configure') as configure, \
             patch.object(self.page.save_thread, 'start') as start, \
             patch.object(self.page, 'clean_temp_folder') as clean:
            result = self.page.regenerate()
        return result, dialog, configure, start, clean

    def test_cancel_keeps_imported_script_and_clears_incompatible_old_audio(self):
        self.page.notes_list = [{'page': 1, 'text': '原有讲稿'}]
        audio = Path(self.directory.name) / 'old.wav'
        audio.write_bytes(b'keep cached audio on disk')
        self.page.media_list = [audio]
        self.page.is_import = True
        result, dialog, configure, start, clean = self.generate(0)
        self.assertFalse(result)
        dialog.assert_called_once()
        configure.assert_not_called()
        start.assert_not_called()
        clean.assert_not_called()
        self.assertEqual(self.page.notes, {1: '第一段●第二段'})
        self.assertEqual(len(self.page.notes_list), 2)
        self.assertEqual(self.page.media_list, [])
        self.assertTrue(audio.is_file())
        self.assertFalse(self.page.is_import)
        self.assertTrue(self.page.getFileButton.isEnabled())
        self.assertFalse(self.page.is_busy())

    def test_continue_starts_generation_once_with_current_segments(self):
        result, dialog, configure, start, clean = self.generate(1)
        self.assertTrue(result)
        dialog.assert_called_once()
        start.assert_called_once_with()
        clean.assert_called_once()
        self.assertEqual(configure.call_args.args[0],
                         [{'page': 1, 'text': '第一段'}, {'page': 1, 'text': '第二段'}])

    def test_unchanged_retry_cancel_preserves_playable_audio(self):
        self.page.mark_split()
        self.page.media_list = [Path('a.wav'), Path('b.wav')]
        self.page.is_import = True
        self.generate(0)
        self.assertEqual(self.page.media_list, [Path('a.wav'), Path('b.wav')])
        self.assertTrue(self.page.is_import)

    def test_matched_page_and_standalone_script_generate_without_dialog(self):
        for source, text in ((self.source, '一段'), (None, '第一段●第二段')):
            self.page.presentation_path, self.page.notes = source, {1: text}
            result, dialog, configure, start, clean = self.generate(0)
            self.assertTrue(result)
            dialog.assert_not_called()
            start.assert_called_once_with()

    def test_disabled_check_skips_parsing_and_confirmation(self):
        self.page.ctx.app_settings.set('animation_check_enabled', False)
        self.source.write_bytes(b'not readable as pptx')
        with patch('app.presentation.animation.check_alignment') as check:
            result, dialog, configure, start, clean = self.generate(0)
        self.assertTrue(result)
        check.assert_not_called()
        dialog.assert_not_called()
        start.assert_called_once_with()

    def test_comments_filtered_before_generation_and_animation_check(self):
        make_deck(self.source, [[('clickEffect',)]])
        raw = '// 提醒●不参与分段\n第一段\n●第二段\n  // 结束●'
        self.page.ctx.app_settings.set('script_comments_enabled', True)
        self.page.notes = {1: raw}
        result, dialog, configure, start, clean = self.generate(0)
        self.assertTrue(result)
        dialog.assert_not_called()
        self.assertEqual(configure.call_args.args[0],
                         [{'page': 1, 'text': '第一段'}, {'page': 1, 'text': '第二段'}])
        self.assertEqual(self.page.notes, {1: raw})
        self.assertEqual(self.page.count_words(), 6)
        self.page.ctx.app_settings.set('script_comments_enabled', False)
        # 已有分段与统计保持生成时的规则，下次生成才应用新偏好。
        self.assertEqual(self.page.count_words(), 6)
        self.page.mark_split()
        self.assertIn('// 提醒', self.page.notes_list[0]['text'])

    def test_comment_only_page_keeps_blank_page_timing_without_extra_clicks(self):
        self.page.ctx.app_settings.set('script_comments_enabled', True)
        self.page.notes = {1: '// 提示●\n\t// 资料●'}
        self.page.mark_split()
        self.assertEqual(self.page.notes_list, [{'page': 1, 'text': ''}])

    def test_blank_and_comment_only_pages_require_confirmation_before_generation(self):
        self.page.ctx.app_settings.set('script_comments_enabled', True)
        for text in (' \n\t', '// 提示●\n\t// 资料●'):
            for decision in (0, 1):
                with self.subTest(text=text, decision=decision):
                    self.page.notes = {1: text}
                    result, dialog, configure, start, clean = self.generate(decision)
                    self.assertEqual(result, bool(decision))
                    dialog.assert_called_once()
                    self.assertEqual(self.page.notes_list, [{'page': 1, 'text': ''}])
                    if decision:
                        start.assert_called_once_with()
                        configure.assert_called_once()
                    else:
                        start.assert_not_called()
                        configure.assert_not_called()

    def test_read_failure_still_allows_user_to_continue(self):
        self.source.write_bytes(b'broken ppt')
        result, dialog, configure, start, clean = self.generate(1)
        self.assertTrue(result)
        dialog.assert_called_once()
        start.assert_called_once_with()

    def test_current_multi_character_separator_and_trailing_mark_are_used(self):
        make_deck(self.source, [[('clickEffect',)]])
        self.page.mark = '||'
        self.page.notes = {1: '第一段||第二段||\n'}
        result, dialog, configure, start, clean = self.generate(0)
        self.assertTrue(result)
        dialog.assert_not_called()
        self.assertEqual(len(configure.call_args.args[0]), 2)

    def test_confirmation_prevents_reentrant_import_and_resets_busy_state(self):
        def confirm(dialog):
            self.assertTrue(self.page.is_busy())
            self.assertFalse(self.page.import_file(self.source))
            return 1
        self.page.create_warning_info_bar = Mock()
        with patch.object(AnimationCheckDialog, 'exec', confirm), \
             patch.object(self.page.save_thread, 'start') as start, \
             patch.object(self.page.save_thread, 'configure'), \
             patch.object(self.page, 'clean_temp_folder'):
            self.assertTrue(self.page.regenerate())
        start.assert_called_once_with()
        self.assertFalse(self.page.is_busy())

    def test_cancel_after_ppt_import_keeps_new_script_without_starting_generation(self):
        ppt = Presentation(self.source)
        ppt.slides[0].notes_slide.notes_text_frame.text = '导入第一段●导入第二段'
        ppt.save(self.source)
        self.page.notes = {}
        with patch.object(AnimationCheckDialog, 'exec', return_value=0), \
             patch.object(self.page.save_thread, 'start') as start:
            self.assertTrue(self.page.import_file(self.source))
        start.assert_not_called()
        self.assertEqual(self.page.notes[1].strip(), '导入第一段●导入第二段')
        self.assertEqual(len(self.page.notes_list), 2)
        self.assertFalse(self.page.is_import)
        self.assertTrue(self.page.getFileButton.isEnabled())

    def test_many_issues_scroll_and_buttons_accept_or_cancel(self):
        issues = [AnimationIssue(i, 3, 1, '讲稿多出 1 个点击步骤') for i in range(1, 101)]
        dialog = AnimationCheckDialog(issues, self.page)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.table.rowCount(), 100)
        self.assertLessEqual(dialog.table.height(), 280)
        self.assertGreater(dialog.table.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.yesButton.text(), '继续生成')
        self.assertEqual(dialog.cancelButton.text(), '取消')
        QTimer.singleShot(0, dialog.yesButton.click)
        self.assertEqual(dialog.exec(), 1)
        dialog = AnimationCheckDialog(issues[:1], self.page)
        QTimer.singleShot(0, dialog.cancelButton.click)
        self.assertEqual(dialog.exec(), 0)


if __name__ == '__main__':
    unittest.main()
