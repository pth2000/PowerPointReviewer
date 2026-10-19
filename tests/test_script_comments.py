"""整行注释的解析、讲稿文档回导与字幕输出。"""

import json
from pathlib import Path
import tempfile
import unittest

from app.documents import export as exporters, io as script_io
from app.presentation.monitor import changed_pages
from app.documents.text import normalize, replace_spoken_text, spoken_text


class ScriptCommentsTests(unittest.TestCase):
    def test_only_indented_line_prefix_is_comment_and_all_line_breaks_work(self):
        text = ('// 提问●不算点击\r\n第一段\x0b\t // 资料\u2028'
                '●网址 https://example.org/a//b\r正文 // 仍然朗读\n')
        self.assertEqual(spoken_text(text, True),
                         '第一段\n●网址 https://example.org/a//b\n正文 // 仍然朗读\n')
        self.assertEqual(spoken_text(text, False), normalize(text))
        self.assertEqual(spoken_text('/* 普通正文 */\n／／全角符号', True),
                         '/* 普通正文 */\n／／全角符号')

    def test_rewrite_keeps_comment_order_when_body_has_fewer_lines(self):
        original = '// 开头\n第一行\n  // 中间\n第二行\n// 结尾'
        updated = replace_spoken_text(original, '新的讲稿')
        self.assertEqual(updated, '// 开头\n新的讲稿\n  // 中间\n// 结尾')
        self.assertEqual(spoken_text(updated, True), '新的讲稿')
        self.assertEqual(replace_spoken_text('原文', '新文'), '新文')

    def test_comment_edits_do_not_count_as_script_changes_when_enabled(self):
        original = {1: '// 旧提醒\n正文', 2: '第二页'}
        updated = {1: '// 新提醒●\n正文', 2: '第二页'}
        self.assertEqual(changed_pages(original, updated, comments_enabled=True), [])
        self.assertEqual(changed_pages(original, updated), [1])
        updated[2] = '新的第二页'
        self.assertEqual(changed_pages(original, updated, comments_enabled=True), [2])

    def test_word_json_roundtrip_keeps_comments_and_srt_has_only_spoken_text(self):
        raw = {1: '// 提问●\n第一段\n●第二段\n// 资料', 2: '// 本页提示'}
        segments = [{'page': 1, 'text': '第一段'}, {'page': 1, 'text': '第二段'},
                    {'page': 2, 'text': ''}]
        durations = [1, 2, 1]
        with tempfile.TemporaryDirectory() as folder:
            word, data = Path(folder) / 'notes.docx', Path(folder) / 'notes.json'
            exporters.write_docx(word, segments, durations, notes=raw)
            self.assertEqual(script_io.load_script(word).notes, raw)
            payload = exporters.build_script_json(segments, durations, notes=raw)
            data.write_text(json.dumps(payload), encoding='utf-8')
            self.assertEqual(script_io.load_script(data).notes, raw)
            self.assertEqual(payload['pages'][0]['words'], 6)
        subtitles = exporters.build_srt(segments, durations)
        self.assertIn('第一段', subtitles)
        self.assertIn('第二段', subtitles)
        self.assertNotIn('//', subtitles)
        self.assertNotIn('提问', subtitles)


if __name__ == '__main__':
    unittest.main()
