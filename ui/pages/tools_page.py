"""工具页：负责导出当前已导入讲稿"""

import json
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from pptx import Presentation
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import InfoBar, InfoBarPosition

from toolsInterface import Ui_toolsInterface


class ToolsInterface(QWidget, Ui_toolsInterface):
    """工具页"""

    def __init__(self, reviewer_page, parent=None):
        super().__init__(parent=parent)
        self.reviewer_page = reviewer_page
        self.setupUi(self)
        self.bgScrollArea.enableTransparentBackground()

        self.transformPPTPushButton.clicked.connect(self.write_to_ppt)
        self.transformWordPushButton.clicked.connect(self.write_to_word)
        self.transformJsonPushButton.clicked.connect(self.write_to_json)

    def write_to_ppt(self):
        """将当前讲稿写入 PPT 备注"""
        notes_dict = self.reviewer_page.notes
        if not notes_dict:
            self.create_warning_info_bar('演讲稿未导入', '请先导入演讲稿。')
            return False

        ppt_path = self.get_ppt_path()
        if not ppt_path:
            self.create_warning_info_bar('文件选择已取消', '请重新选择文件进行导入。')
            return False

        dir_path = self.get_dir_path()
        if not dir_path:
            self.create_warning_info_bar('目录选择已取消', '请重新选择保存目录。')
            return False

        try:
            ppt = Presentation(str(ppt_path))
            for slide in ppt.slides:
                slide_number = ppt.slides.index(slide) + 1
                if slide_number in notes_dict:
                    slide.notes_slide.notes_text_frame.text = notes_dict[slide_number]
            ppt_name = ppt_path.stem
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 读取错误', f'详情：{e}')
            return False

        output_path = dir_path / f'{ppt_name}_NEW.pptx'
        try:
            ppt.save(str(output_path))
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 保存错误', f'详情：{e}')
            return False

        self.create_success_info_bar('生成成功', f'讲稿备注已导入完毕，文件路径：{output_path}')

    def write_to_word(self):
        """将当前讲稿导出为 Word"""
        notes = self.reviewer_page.notes
        file_name = self.reviewer_page.note_file_name
        if not notes:
            self.create_warning_info_bar('演讲稿未导入', '请先导入演讲稿。')
            return False

        dir_path = self.get_dir_path()
        if not dir_path:
            self.create_warning_info_bar('目录选择已取消', '请重新选择保存目录。')
            return False

        file_path = dir_path / f'{file_name}_Notes.docx'
        try:
            document = Document()
            for page_number, page_note in notes.items():
                p_page_number = document.add_paragraph()
                p_page_number.style = 'Heading 1'
                p_page_number.paragraph_format.space_before = Pt(0)
                p_page_number.paragraph_format.space_after = Pt(0)
                p_page_number.paragraph_format.line_spacing = 1.5
                run_page_number = p_page_number.add_run(f'第{page_number}页')
                run_page_number.font.name = 'Microsoft YaHei'
                run_page_number.font.size = Pt(14)
                run_page_number.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
                run_page_number.bold = True
                run_page_number.font.color.rgb = RGBColor(0, 0, 0)

                p_page_note = document.add_paragraph()
                p_page_note.paragraph_format.space_before = Pt(0)
                p_page_note.paragraph_format.space_after = Pt(0)
                p_page_note.paragraph_format.line_spacing = 1.5
                page_note = re.sub(u"[\\x00-\\x08\\x0b\\x0e-\\x1f\\x7f]", "", page_note)
                run_page_note = p_page_note.add_run(page_note.strip("\n"))
                run_page_note.font.name = 'Microsoft YaHei'
                run_page_note.font.size = Pt(11)
                run_page_note.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
            document.save(str(file_path))
        except Exception as e:
            print(e)
            self.create_error_info_bar('Word 保存错误', f'详情：{e}')
            return False

        self.create_success_info_bar('转换成功', f'讲稿备注已转换完毕，文件路径：{file_path}')

    def write_to_json(self):
        """将当前讲稿导出为 JSON"""
        notes_dict = self.reviewer_page.notes
        file_name = self.reviewer_page.note_file_name
        if not notes_dict:
            self.create_warning_info_bar('演讲稿未导入', '请先导入演讲稿。')
            return False

        dir_path = self.get_dir_path()
        if not dir_path:
            self.create_warning_info_bar('目录选择已取消', '请重新选择保存目录。')
            return False

        file_path = dir_path / f'{file_name}_Notes.json'
        try:
            with file_path.open('w', encoding='utf-8') as json_file:
                json.dump(notes_dict, json_file, ensure_ascii=False, indent=2)
        except Exception as e:
            print(e)
            self.create_error_info_bar('JSON 保存错误', f'详情：{e}')
            return False

        self.create_success_info_bar('转换成功', f'讲稿备注已转换完毕，文件路径：{file_path}')

    def get_dir_path(self):
        """获取目录"""
        selected_dir = QFileDialog.getExistingDirectory(self, '选择保存文件夹', '', QFileDialog.Option.ShowDirsOnly)
        return Path(selected_dir) if selected_dir else None

    def get_ppt_path(self):
        """获取 PPT 路径"""
        selected_files = QFileDialog.getOpenFileName(self, '选择PowerPoint文件', '', 'PowerPoint Files (*.pptx)')
        return Path(selected_files[0]) if selected_files[0] else None

    def create_success_info_bar(self, title, text):
        """成功消息框"""
        InfoBar.success(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self
        )

    def create_warning_info_bar(self, title, text):
        """警告消息框"""
        InfoBar.warning(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self
        )

    def create_error_info_bar(self, title, text):
        """错误消息框"""
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self
        )
