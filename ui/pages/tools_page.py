"""提供 AI 讲稿处理以及文档、字幕、音频和工程包导出。"""

import json
import re
from itertools import count
from pathlib import Path
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    IconWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

from app.audio import cache as audio_cache
from app.documents import export as exporters, package as project_package
from ui import icons, theme
from app.logging_setup import log_error
from app.app_context import AppContext
from app.project.model import UnavailableReason
from ui.widget_state import set_enabled
from toolsInterface import Ui_toolsInterface
from ui.dialogs.rewrite_dialog import RewriteDialog
from engines.defs import ENGINE_DEFS


class ToolsInterface(QWidget, Ui_toolsInterface):
    """根据主页当前会话提供讲稿处理与多格式导出操作。"""

    def __init__(self, context: AppContext, reviewer_page, parent=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.reviewer_page = reviewer_page
        self.project = reviewer_page.project
        self.setupUi(self)
        self.bgScrollArea.enableTransparentBackground()

        self.processing_cards = []
        self.export_cards = []
        self.build_layout()
        self.reviewer_page.presentation_state_changed.connect(self.refresh_state)
        self.refresh_state()

    # 页面装配

    def add_section(self, title: str, caption: str = ''):
        """向工具页追加分区标题和可选说明。"""
        label = SubtitleLabel(title, self.importWidget)
        font = label.font()
        font.setPointSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        label.setFixedHeight(28)
        self.verticalLayout_2.addSpacing(16)
        self.verticalLayout_2.addWidget(label)

        caption_label = None
        if caption:
            caption_label = CaptionLabel(caption, self.importWidget)
            caption_label.setWordWrap(True)
            self.verticalLayout_2.addWidget(caption_label)
        return label, caption_label

    def add_card(self, title: str, caption: str, controls,
                 primary_first: bool = False, button_width: int = 120, help_text: str = '',
                 icon=None):
        """追加操作卡片；``controls`` 提供按钮文本与槽函数。"""
        card = CardWidget(self.importWidget)
        card.setBorderRadius(8)
        h_layout = QHBoxLayout(card)
        h_layout.setContentsMargins(20, 16, 20, 16)
        h_layout.setSpacing(8)

        if icon is not None:
            glyph = IconWidget(icon, card)
            glyph.setFixedSize(20, 20)
            h_layout.addWidget(glyph)
            h_layout.addSpacing(12)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(3)

        title_row, _icon = theme.make_title_row(theme.make_card_title(title, card), help_text)
        info_layout.addLayout(title_row)

        caption_label = CaptionLabel(card)
        caption_label.setText(caption)
        caption_label.setWordWrap(True)
        info_layout.addWidget(caption_label)

        h_layout.addLayout(info_layout, stretch=1)
        h_layout.addSpacing(16)

        for index, (text, slot) in enumerate(controls):
            factory = PrimaryPushButton if (primary_first and index == 0) else PushButton
            button = factory(text, card)
            button.setProperty('requires_ppt', slot in (
                self.write_to_ppt, self.export_powerpoint_video, self.record_obs_video))
            button.setProperty('requires_audio', slot in (
                self.open_audio_review,
                self.export_project_package, self.write_to_srt, self.export_audio_files,
                self.export_merged_audio, self.export_powerpoint_video, self.record_obs_video))
            button.setProperty('requires_durations', slot in (
                self.write_to_srt, self.export_powerpoint_video, self.record_obs_video))
            button.clicked.connect(slot)
            button.setMinimumSize(QSize(button_width, 33))
            button.setMaximumSize(QSize(button_width, 16777215))
            h_layout.addWidget(button)

        self.verticalLayout_2.addWidget(card)
        return card

    def build_layout(self):
        """将 Designer 骨架重排为讲稿处理和导出两个分区。"""
        # 复用 .ui 中的静态标题，避免运行期重建造成样式差异。
        self.SubtitleLabel.setText('讲稿处理')
        section_font = self.SubtitleLabel.font()
        section_font.setPointSize(12)
        section_font.setWeight(QFont.Weight.DemiBold)
        self.SubtitleLabel.setFont(section_font)
        self.SubtitleLabel.setFixedHeight(28)
        self.verticalLayout_2.removeWidget(self.CaptionLabel_4)
        self.CaptionLabel_4.hide()
        self.verticalLayout_2.setSpacing(8)
        self.audio_review_card = self.add_card(
            '配音审听', '逐段试听，调整语速与停顿',
            [('打开面板', self.open_audio_review)],
            primary_first=True,
            icon=icons.FluentSystemIcon.HEADPHONE,
            help_text='选中片段即可试听，调整效果确认采用后用于正常播放和导出。')
        self.processing_cards.append(self.audio_review_card)

        self.ai_rewrite_card = self.add_card(
            'AI 优化',
            '纠正易错读法，优化讲稿表达',
            [('前往优化', self.open_rewrite_dialog)],
            icon=icons.FluentSystemIcon.EDIT,
            help_text='可通过兼容 OpenAI 的接口处理，也可导出含提示词的任务，在其他 AI 中处理后导回审查。'
                      '优化结果经确认后才会应用。',
        )
        self.processing_cards.append(self.ai_rewrite_card)

        self.add_section('导出')

        self.script_export_card = self.add_card(
            '讲稿文档',
            '导出为文档或写回 PPT 备注',
            [
                ('PPT 备注', self.write_to_ppt),
                ('Word', self.write_to_word),
                ('Markdown', self.write_to_markdown),
                ('JSON', self.write_to_json),
            ],
            button_width=100, icon=icons.FluentSystemIcon.DOCUMENT,
            help_text='写回 PowerPoint 时另存为新文件，不覆盖原文件。Word 与 JSON 文件修改后可重新导入。',
        )
        self.export_cards.append(self.script_export_card)

        self.audio_export_card = self.add_card(
            '字幕与音频',
            '导出 SRT 字幕与合成的语音',
            [
                ('SRT 字幕', self.write_to_srt),
                ('逐条音频', self.export_audio_files),
                ('合并音频', self.export_merged_audio),
            ],
            button_width=136, icon=icons.FluentSystemIcon.MUSIC,
            help_text='SRT 字幕的时间轴按各语句的音频时长生成。合并音频将全部语句依次拼接为一个文件。',
        )
        self.export_cards.append(self.audio_export_card)

        video_methods = (
                ('native', 'PowerPoint 导出', self.export_powerpoint_video,
                 '需要 Microsoft PowerPoint 2016 或更新版本，保留动画和转场。\n'
                 '生成时不占用放映窗口，可继续使用电脑。\n'
                 '耗时取决于视频长度、画面复杂度和导出设置。特殊动画可尝试 OBS 录制。'),
                ('obs', 'OBS 录制', self.record_obs_video,
                 '适用于 PowerPoint 和 WPS 演示，需安装 OBS Studio 28 或更新版本。\n'
                 '录制实际放映效果，通常需要与成片时长接近的时间。\n'
                 '录制期间请保持放映窗口正常显示，不要最小化或手动翻页。'))
        self.video_export_card = self.add_card(
            '演示视频', '将 PPT 画面与讲稿配音制作为视频',
            [(title, slot) for _mode, title, slot, _help in video_methods],
            button_width=136, icon=icons.FluentSystemIcon.VIDEO,
            help_text='选择 PowerPoint 导出或 OBS 录制，支持设置清晰度、帧率和字幕。',
        )
        for button, (mode, _title, _slot, help_text) in zip(
                self.video_export_card.findChildren(PushButton), video_methods):
            button.setToolTip(help_text)
            button.setProperty('available_tooltip', help_text)
            button.setProperty('video_export_mode', mode)
            button.setAccessibleDescription(help_text)
        self.export_cards.append(self.video_export_card)
        self.project_package_card = self.add_card(
            '工程包',
            '打包讲稿、音频与配置，用于备份或迁移',
            [('导出工程包', self.export_project_package)], icon=icons.FluentSystemIcon.ARCHIVE,
            help_text='包含讲稿与已生成的音频，在其他设备导入后即可播放。PPT 文稿可在“历史记录列表”中关联。',
        )
        self.export_cards.append(self.project_package_card)


    # 状态同步

    def showEvent(self, event):
        """页面显示时从主页同步最新会话状态。"""
        super().showEvent(event)
        self.refresh_state()

    def refresh_state(self):
        """按钮与操作入口共用当前工程的功能可用条件。"""
        state = self.project.availability(busy=self.reviewer_page.is_busy())
        hints = {
            UnavailableReason.NOTES: '导入讲稿后可使用',
            UnavailableReason.BUSY: '请等待当前配音生成完成',
            UnavailableReason.PRESENTATION: '在“历史记录列表”中关联 PPT 后可使用',
            UnavailableReason.AUDIO: '完成讲稿配音后可使用',
        }
        for card in self.processing_cards + self.export_cards:
            for button in card.findChildren(PushButton):
                reason = state.unavailable_for(
                    audio=bool(button.property('requires_audio')),
                    presentation=bool(button.property('requires_ppt')),
                    durations=bool(button.property('requires_durations')))
                set_enabled(button, reason is None)
                button.setToolTip(hints.get(reason, button.property('available_tooltip') or ''))

    # 讲稿处理

    def open_audio_review(self):
        if not self.require_project(audio=True):
            return
        self.reviewer_page.open_audio_review()

    def open_rewrite_dialog(self):
        """打开逐页改写对照框，并将用户确认的结果写回主页。"""
        if not self.require_project():
            return

        page = self.reviewer_page
        dialog = RewriteDialog(self.project.reading_notes(), self.project.mark, self.ctx.app_settings, self)

        accepted = dialog.exec()
        # 弹窗中可能只修改了接口或风格，即使未采用结果也应保存这些设置。
        self.ctx.config.save_later()
        if not accepted:
            return

        applied = page.apply_rewritten_notes(dialog.get_applied())
        if applied < 0:
            return
        if applied == 0:
            self.create_warning_info_bar('未写回任何内容', '改写结果为空或页码不匹配')
            return

        self.goto_reviewer(f'已更新 {applied} 页讲稿，正在重新合成受影响的音频')

    def goto_reviewer(self, message: str):
        """返回主页，并展示后续音频生成状态。"""
        window = self.window()
        if hasattr(window, 'switchTo'):
            window.switchTo(self.reviewer_page)
        self.reviewer_page.create_success_info_bar('已开始重新合成', message)

    # 共用前置检查

    def export_powerpoint_video(self):
        self.export_video('native')

    def record_obs_video(self):
        self.export_video('obs')

    def export_video(self, mode):
        """独占放映控制与播放状态，关闭录制窗口后恢复主页订阅。"""
        if sys.platform != 'win32':
            self.create_warning_info_bar('暂不支持此系统', 'PPT 视频导出目前需要 Windows 与 PowerPoint / WPS 演示')
            return
        if not self.require_project(audio=True, presentation=True, durations=True):
            return
        page = self.reviewer_page
        from ui.dialogs.video_recording_dialog import VideoRecordingDialog

        page.stop_audio()
        page.release_playback_show()
        self.ctx.hotkeys.clear()
        self.ctx.slideshow_watcher.stop()
        try:
            dialog = VideoRecordingDialog(self.project.notes_list, self.project.media_list,
                                          self.project.presentation_path, self, ctx=self.ctx, initial_mode=mode)
            dialog.finished.connect(dialog.deleteLater)
            dialog.exec()
        except Exception as exc:
            self.create_error_info_bar('无法导出视频', str(exc))
        finally:
            page.refresh_hotkeys()
            page.refresh_slideshow_watch()

    def require_project(self, *, audio=False, presentation=False, durations=False):
        """执行前重新校验，处理按钮启用后文件被移动或任务状态变化的情况。"""
        if presentation:
            self.reviewer_page.refresh_presentation_state()
        state = self.project.availability(busy=self.reviewer_page.is_busy())
        reason = state.unavailable_for(audio=audio, presentation=presentation, durations=durations)
        if reason is None:
            return True
        messages = {
            UnavailableReason.NOTES: ('尚未导入讲稿', '请先在主页导入讲稿'),
            UnavailableReason.BUSY: ('正在生成音频', '等待当前转换完成后再试'),
            UnavailableReason.PRESENTATION: ('受限模式', '请在“历史记录列表”中关联 PPT 后再使用此功能'),
            UnavailableReason.AUDIO: ('音频尚未就绪', '请先完成讲稿配音，再导出'),
        }
        self.refresh_state()
        self.create_warning_info_bar(*messages[reason])
        return False


    def audio_metadata(self):
        profile = self.project.generation_profile
        if profile is None:
            profile = self.ctx.tts_engine.get_generation_profile()
            speaker = self.ctx.tts_engine.get_selected_voice_name()
        else:
            speaker = self.project.generation_speaker
        engine = next((item['name'] for item in ENGINE_DEFS if item['id'] == profile.get('mode')), '')
        return profile, speaker, engine

    def export_name(self):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(self.project.note_file_name))
        return name.rstrip(' .') or '讲稿'


    # 导出目录

    def choose_export_dir(self):
        """让用户选择目标目录，并以上次选择作为起始位置。"""
        start_dir = str(self.ctx.app_settings.get('export_dir') or '')
        selected = QFileDialog.getExistingDirectory(
            self, '选择导出目录', start_dir, QFileDialog.Option.ShowDirsOnly)
        if not selected:
            return None

        # 只保存目录偏好，不代表后续导出可跳过确认。
        self.ctx.app_settings.set('export_dir', selected)
        self.ctx.config.save_later()
        return Path(selected)

    @staticmethod
    def unique_path(path: Path) -> Path:
        """为已存在的目标文件追加递增序号，避免静默覆盖。"""
        if not path.exists():
            return path
        for index in count(2):
            candidate = path.with_name(f'{path.stem}_{index}{path.suffix}')
            if not candidate.exists():
                return candidate

    def get_ppt_path(self):
        """选择待回写的 PowerPoint，优先定位到当前源文件。"""
        current = self.project.note_file_path
        associated = self.project.presentation_path
        if associated and associated.is_file() and associated.suffix.lower() == '.pptx':
            return associated
        start_path = str(current) if current and current.suffix.lower() == '.pptx' else ''
        selected_files = QFileDialog.getOpenFileName(
            self, '选择要写入备注的 PowerPoint 文件', start_path, 'PowerPoint Files (*.pptx)')
        return Path(selected_files[0]) if selected_files[0] else None

    # 讲稿文档导出

    def write_to_ppt(self):
        """将当前逐页讲稿回写到指定 PowerPoint 的备注。"""
        if not self.require_project(presentation=True):
            return
        notes_dict = self.project.notes

        ppt_path = self.get_ppt_path()
        if not ppt_path:
            self.create_warning_info_bar('已取消', '未选择 PowerPoint 文件')
            return

        try:
            from pptx import Presentation

            ppt = Presentation(str(ppt_path))
            if max(notes_dict, default=1) > len(ppt.slides):
                self.create_warning_info_bar(
                    '讲稿页码与 PPT 不匹配',
                    f'讲稿包含第 {max(notes_dict)} 页，但 PPT 只有 {len(ppt.slides)} 页。请先更新讲稿或选择对应的 PPT。')
                return
            for slide_number, slide in enumerate(ppt.slides, start=1):
                if slide_number in notes_dict:
                    slide.notes_slide.notes_text_frame.text = notes_dict[slide_number]
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 读取错误', f'详情：{e}')
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return
        output_path = self.unique_path(dir_path / f'{ppt_path.stem}_NEW.pptx')
        try:
            ppt.save(str(output_path))
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 保存错误', f'详情：{e}')
            return

        self.create_success_info_bar('生成成功', f'讲稿备注已写入：{output_path}')

    def write_to_word(self):
        """导出可修改后稳定回导的 Word 表格讲稿。"""
        if not self.require_project():
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        file_path = self.unique_path(dir_path / f'{self.export_name()}_Notes.docx')
        try:
            exporters.write_docx(
                file_path, self.project.notes_list, self.project.notes_duration_list, mark=self.project.mark,
                notes=self.project.notes if self.project.script_comments_enabled else None,
            )
        except Exception as e:
            print(e)
            self.create_error_info_bar('Word 保存错误', f'详情：{e}')
            return

        self.create_success_info_bar('转换成功', f'Word 已导出：{file_path}')

    def write_to_markdown(self):
        """导出适合阅读和打印的 Markdown 讲稿。"""
        if not self.require_project():
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        _profile, speaker, engine_name = self.audio_metadata()
        content = exporters.build_markdown(
            self.project.notes_list,
            self.project.notes_duration_list,
            title=self.project.note_file_name or '演讲稿',
            mark=self.project.mark,
            engine_name=engine_name,
            speaker=speaker,
        )

        file_path = self.unique_path(dir_path / f'{self.export_name()}_Notes.md')
        try:
            file_path.write_text(content, encoding='utf-8')
        except Exception as e:
            print(e)
            self.create_error_info_bar('Markdown 保存错误', f'详情：{e}')
            return

        self.create_success_info_bar('转换成功', f'Markdown 已导出：{file_path}')

    def write_to_json(self):
        """导出保留分页和分段结构的可回导 JSON。"""
        if not self.require_project():
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        _profile, speaker, engine_name = self.audio_metadata()
        payload = exporters.build_script_json(
            self.project.notes_list, self.project.notes_duration_list,
            source_name=self.project.note_file_name, mark=self.project.mark,
            engine_name=engine_name,
            speaker=speaker,
            notes=self.project.notes if self.project.script_comments_enabled else None,
        )

        file_path = self.unique_path(dir_path / f'{self.export_name()}_Notes.json')
        try:
            with file_path.open('w', encoding='utf-8') as json_file:
                json.dump(payload, json_file, ensure_ascii=False, indent=2)
        except Exception as e:
            print(e)
            self.create_error_info_bar('JSON 保存错误', f'详情：{e}')
            return

        self.create_success_info_bar('转换成功', f'JSON 已导出：{file_path}')

    def export_project_package(self):
        """将讲稿、音频和生成配置打包为可跨设备恢复的工程包。"""
        if not self.require_project(audio=True):
            return
        items = project_package.build_items(
            self.project.notes_list, self.project.notes_duration_list,
            self.project.note_cache_keys, self.project.note_cache_exts, self.project.media_list, audio_edits=self.project.audio_edits)
        audio_size = project_package.estimate_size(items)

        box = MessageBox('导出工程包',
                         f'打包 {len(items)} 条语句及对应音频，约 {audio_cache.format_size(audio_size)}', self)
        box.yesButton.setText('选择保存位置')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        target = self.unique_path(
            dir_path / f'{self.export_name()}{project_package.PACKAGE_SUFFIX}')
        try:
            profile, speaker, _engine_name = self.audio_metadata()
            info = project_package.export_package(
                target, items=items, notes=self.project.notes, mark=self.project.mark,
                source_name=self.project.note_file_name,
                generation_profile=profile,
                speaker=speaker,
                app_version=self.ctx.version,
                presentation=self.project.presentation_link,
                presentation_path=self.project.presentation_path,
                script_comments_enabled=self.project.script_comments_enabled,
            )
        except Exception as e:
            print(e)
            self.create_error_info_bar('工程包导出失败', f'详情：{e}')
            return

        detail = f'已打包 {info["audio_count"]} 条音频，大小 {audio_cache.format_size(info["size"])}'
        if info['missing']:
            detail += f'；{len(info["missing"])} 条音频缺失，未包含在内'
        self.create_success_info_bar('导出成功', f'{detail}：{target}')

    # 字幕与音频导出

    def write_to_srt(self):
        """按实际音频时长导出连续 SRT 字幕。"""
        if not self.require_project(audio=True, durations=True):
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        content = exporters.build_srt(self.project.notes_list, self.project.notes_duration_list)
        if not content.strip():
            self.create_warning_info_bar('没有可导出的字幕', '请先生成讲稿配音，再导出字幕')
            return

        file_path = self.unique_path(dir_path / f'{self.export_name()}_Notes.srt')
        try:
            file_path.write_text(content, encoding='utf-8')
        except Exception as e:
            print(e)
            self.create_error_info_bar('字幕保存错误', f'详情：{e}')
            return

        self.create_success_info_bar('转换成功', f'字幕已导出：{file_path}')

    def export_audio_files(self):
        """将每条讲稿音频复制为带顺序和页码的独立文件。"""
        if not self.require_project(audio=True):
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        target_dir = self.unique_path(dir_path / f'{self.export_name()}_Audio')
        try:
            exported = exporters.export_audio_files(self.project.media_list, self.project.notes_list, target_dir)
        except Exception as e:
            print(e)
            self.create_error_info_bar('音频导出错误', f'详情：{e}')
            return

        self.create_success_info_bar('导出成功', f'已导出 {exported} 条音频到：{target_dir}')

    def export_merged_audio(self):
        """将格式兼容的全部讲稿音频合并为单个文件。"""
        if not self.require_project(audio=True):
            return

        dir_path = self.choose_export_dir()
        if not dir_path:
            return

        direct = exporters.can_merge_audio_directly(self.project.media_list)
        suffix = (Path(self.project.media_list[0]).suffix.lower() or '.wav') if direct else '.wav'
        file_path = self.unique_path(dir_path / f'{self.export_name()}_Full{suffix}')
        if not direct:
            from ui.dialogs.audio_merge_dialog import AudioMergeDialog
            dialog = AudioMergeDialog(list(self.project.media_list), file_path, self)
            result = dialog.exec()
            if dialog.error:
                self.create_error_info_bar('音频合并失败', dialog.error)
            elif result:
                self.create_success_info_bar('导出成功', f'完整音频已导出：{file_path}')
            dialog.deleteLater()
            return
        try:
            exporters.merge_audio(self.project.media_list, file_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('音频合并失败', f'详情：{e}')
            return

        self.create_success_info_bar('导出成功', f'完整音频已导出：{file_path}')

    # 消息提示

    def create_success_info_bar(self, title, text):
        """在工具页顶部显示短暂的成功提示。"""
        InfoBar.success(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=5000, parent=self)

    def create_warning_info_bar(self, title, text):
        """在工具页顶部显示短暂的警告提示。"""
        InfoBar.warning(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=5000, parent=self)

    def create_error_info_bar(self, title, text):
        """在工具页顶部显示需要手动关闭的错误提示。"""
        log_error(title, text)
        InfoBar.error(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
                      position=InfoBarPosition.TOP, duration=-1, parent=self)
