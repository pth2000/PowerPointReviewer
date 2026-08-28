"""实现讲稿导入、分段、音频生成、会话恢复和播放。"""

import ctypes
import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import (
    Action,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    InfoLevel,
    MessageBox,
    RoundMenu,
)

from Ui_mainwindow import Ui_mainwindow
from app import icons, paths, project_package, script_io
from app.app_context import AppContext
from app.playback import AudioOutputWatcher
from tasks.audio_generation_task import AudioGenerationTask
from ui.dialogs.edit_mark_dialog import EditMarkMessageBox
from ui.dialogs.session_history_dialog import SessionHistoryDialog


# 翻页只需向前台窗口发送一次 PageDown。为此引入 pyautogui 会连带装载
# Pillow 与 tkinter，启动多花约 240 毫秒，打包也多出二十余兆，因此直接调用 Win32。
_VK_NEXT = 0x22           # Page Down
_KEYEVENTF_KEYUP = 0x0002


def _press_page_down():
    """向当前前台窗口发送一次 PageDown 按键。"""
    user32 = ctypes.windll.user32
    user32.keybd_event(_VK_NEXT, 0, 0, 0)
    user32.keybd_event(_VK_NEXT, 0, _KEYEVENTF_KEYUP, 0)


class PPTReviewer(QWidget, Ui_mainwindow):
    """协调讲稿导入、分段、音频生成、会话恢复和连续播放。"""

    # PowerPoint 软换行和 Word 分页符都表达停顿边界，应转换为换行而不是直接删除。
    _LINE_BREAK_RE = re.compile(r'\r\n|[\r\x0b\x0c\u2028\u2029]')
    # 其余控制字符无朗读意义；保留制表符和换行供 TTS 断句。
    _CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0e-\x1f\x7f]+')
    # 仅接受“页码_序号”命名的正文音频，避免倒计时或试听文件混入播放列表。
    _AUDIO_NAME_RE = re.compile(r'^(\d+)_(\d+)$')

    def __init__(self, context: AppContext, parent=None):
        super().__init__(parent=parent)
        self.ctx = context
        self.setupUi(self)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.device_watcher = AudioOutputWatcher(self.player, self.audio_output, '播放器', parent=self)
        self.ctx.playback_bus.stop_requested.connect(self.on_stop_requested)

        self.media_list = []  # 正文音频，索引与 notes_list 一一对应。
        self.wait_media_list = []  # 本次播放使用的倒计时音频。
        self.current_index = 0  # 当前正文段索引。
        self.wait_current_index = 0  # 当前倒计时音频索引。
        self.note_file_path = None
        self.note_file_name = ''
        self.notes = {}  # 页码到整页讲稿的映射。
        self.notes_list = []  # 按页内分隔符展开后的有序讲稿段。
        self.notes_duration_list = []
        self.note_cache_keys = []
        self.note_cache_exts = []
        self.cache_hit_count = 0
        self.is_play_notes = False
        self.is_import = False
        self.mark = '●'  # restore_preferences() 会用持久化偏好覆盖初值。

        icons.apply(self.partingIconWidget, ':/image/image/parting.svg')
        icons.apply(self.fileIconWidget, ':/image/image/Folder.svg')
        icons.apply(self.currentIconWidget, ':/image/image/countdown.svg')
        icons.apply(self.currentTimeIconWidget, ':/image/image/Clock.svg')
        self.pageJumpToolButton.setIcon(FluentIcon.ACCEPT_MEDIUM)
        icons.apply(self.playButton, ':/image/image/play.svg')
        icons.apply(self.stopButton, ':/image/image/stop.svg')
        icons.apply(self.resetButton, ':/image/image/backward.svg')
        self.IndeterminateProgressBar.setVisible(False)

        icons.apply(self.getFileButton, ':/image/image/ppt.svg')
        self.file_button_menu = RoundMenu(parent=self)
        self.file_button_menu.addAction(icons.apply(
            Action('导入 Word 讲稿', triggered=self.import_word), ':/image/image/word.svg'))
        self.file_button_menu.addAction(
            Action(FluentIcon.DOCUMENT, '导入 JSON 讲稿', triggered=self.import_data_script)
        )
        self.file_button_menu.addAction(
            Action(FluentIcon.FOLDER, '导入工程包', triggered=self.import_package)
        )
        self.file_button_menu.addSeparator()
        self.file_button_menu.addAction(icons.apply(
            Action('历史记录列表', triggered=self.show_session_history_dialog),
            ':/image/image/update.svg'))
        # 上半部分导入新内容，分隔线下方的操作只作用于当前讲稿。
        self.file_button_menu.addSeparator()
        self.regenerate_action = Action(
            FluentIcon.SYNC, '忽略缓存重新生成', triggered=self.force_regenerate)
        self.file_button_menu.addAction(self.regenerate_action)
        self.getFileButton.setFlyout(self.file_button_menu)

        self.bgScrollArea.enableTransparentBackground()

        self.playButton.clicked.connect(self.init_play)
        self.stopButton.clicked.connect(self.stop_audio)
        self.resetButton.clicked.connect(self.reset_audio)
        self.getFileButton.clicked.connect(self.import_pptx)
        self.editMarkPushButton.clicked.connect(self.show_edit_mark_dialog)
        self.pageJumpToolButton.clicked.connect(self.jump_page)
        self.infoPushButton.clicked.connect(self.show_info_dialog)

        self.next_timer = QTimer(self)
        self.next_timer.timeout.connect(self.timeout_play_next_audio)

        paths.ensure_runtime_dirs()
        self.wav_temp_path = paths.TEMP_DIR
        self.countdown_wav_temp_path = paths.COUNTDOWN_CACHE_DIR
        self.audio_cache_path = paths.AUDIO_CACHE_DIR
        self.session_root_path = paths.SESSION_DIR

        self.save_thread = AudioGenerationTask(self.ctx.tts_engine, self)
        self.save_thread.signal_import_index.connect(self.thread_print_index)
        self.save_thread.signal_finish.connect(self.thread_save_finish)
        self.save_thread.signal_error.connect(self.thread_save_error)

        self.restore_preferences()
        self.check_import()

    def restore_preferences(self):
        """将已保存的分隔符和播放偏好恢复到主页控件。"""
        settings = self.ctx.app_settings
        self.mark = str(settings.get('mark')).strip() or '●'
        self.currentSwitch.setChecked(bool(settings.get('countdown_enabled')))
        self.currentSpinBox.setValue(int(settings.get('countdown_seconds')))
        self.currentSpinBox.setEnabled(self.currentSwitch.isChecked())
        self.scrollEnableSwitch.setChecked(bool(settings.get('scroll_enabled')))

        # 初值设置完成后再接信号，避免把恢复动作误当成用户修改。
        self.currentSwitch.checkedChanged.connect(self.persist_preferences)
        self.currentSpinBox.valueChanged.connect(self.persist_preferences)
        self.scrollEnableSwitch.toggled.connect(self.persist_preferences)

    def persist_preferences(self, *_):
        """即时保存不影响音频生成的主页播放偏好。"""
        self.ctx.app_settings.update({
            'mark': self.mark,
            'countdown_enabled': self.currentSwitch.isChecked(),
            'countdown_seconds': self.currentSpinBox.value(),
            'scroll_enabled': self.scrollEnableSwitch.isChecked(),
        })
        self.ctx.config.save_later()

    def check_import(self):
        """根据讲稿导入状态更新播放控件和状态徽标。"""
        if self.is_import:
            self.playCardWidget.setEnabled(True)
            self.playCardWidget_2.setEnabled(True)
            self.playCardWidget_3.setEnabled(True)
            self.statusLabel.setText('已导入')
            self.IconInfoBadge.setLevel(InfoLevel.SUCCESS)
            self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
            self.regenerate_action.setEnabled(True)
        else:
            self.playCardWidget.setEnabled(False)
            self.playCardWidget_2.setEnabled(False)
            self.playCardWidget_3.setEnabled(False)
            self.statusLabel.setText('未导入')
            self.IconInfoBadge.setLevel(InfoLevel.INFOAMTION)
            self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
            self.regenerate_action.setEnabled(False)

    def on_stop_requested(self, requester):
        """响应其它页面的播放请求，停止当前正文或倒计时音频。"""
        if requester is self:
            return
        self.stop_audio()

    def play_audio(self):
        """播放当前倒计时或正文音频。"""
        if self.is_play_notes:
            if self.current_index < len(self.media_list):
                self.player.setSource(QUrl.fromLocalFile(str(self.media_list[self.current_index])))
                self.player.play()
                self.playButton.setEnabled(False)
                self.currentStatusLabel.setText('播放')
                self.set_current_label_text()
                print(self.notes_list[self.current_index]['text'])
            else:
                print('播放完毕')
                self.reset_audio()
        else:
            if self.wait_current_index < len(self.wait_media_list):
                self.player.setSource(QUrl.fromLocalFile(str(self.wait_media_list[self.wait_current_index])))
                self.player.play()
                self.playButton.setEnabled(False)
                temp_index = len(self.wait_media_list) - self.wait_current_index
                self.currentStatusLabel.setText('倒计时')
                self.currentPageLabel.setText(f'{temp_index}')
                self.currentIndexLabel.setText(f'{temp_index}')
            else:
                print('播放完毕')
                self.wait_current_index = 0
                self.play_notes()

    def media_status_changed(self, status):
        """在媒体结束后推进倒计时或正文播放状态机。"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_timer.start(100)

    def stop_audio(self):
        """停止全部播放器并保留当前正文索引。"""
        self.player.stop()
        self.playButton.setEnabled(True)
        self.currentStatusLabel.setText('停止')
        self.ctx.playback_bus.request_stop(self)

    def reset_audio(self):
        """停止播放并将正文索引重置到首段。"""
        self.stop_audio()
        self.current_index = 0
        if self.notes_list:
            self.set_current_label_text()

    def set_current_label_text(self):
        """刷新当前段数和页码标签。"""
        if not self.notes_list:
            return
        index = min(max(self.current_index, 0), len(self.notes_list) - 1)
        self.currentPageLabel.setText(f'{self.notes_list[index]["page"]} / {len(self.notes)}')
        self.currentIndexLabel.setText(f'{index + 1} / {len(self.notes_list)}')

    def get_index_from_page(self, page):
        """返回指定页第一条讲稿的索引，找不到时返回 -1。"""
        for i, item in enumerate(self.notes_list):
            if item['page'] == page:
                return i
        return -1

    def timeout_play_next_audio(self):
        """推进到下一段，并按设置选择是否发送翻页指令。"""
        self.next_timer.stop()
        if self.is_play_notes:
            if self.scrollEnableSwitch.isChecked():
                _press_page_down()
            self.current_index += 1
        else:
            self.wait_current_index += 1
        self.play_audio()

    def is_busy(self) -> bool:
        """返回音频生成任务是否仍在运行。"""
        return self.save_thread.isRunning()

    def force_regenerate(self):
        """确认后绕过缓存，重新合成当前讲稿的全部音频。"""
        if not self.notes:
            self.create_warning_info_bar('尚未导入讲稿', '请先导入 PowerPoint 或 Word 文件。')
            return

        if self.is_busy():
            self.create_warning_info_bar('正在生成音频', '请等待当前转换完成后再试。')
            return

        box = MessageBox(
            '重新生成音频',
            '将跳过音频缓存，按当前引擎设置重新合成全部语句。\n'
            '在线引擎可能需要一些时间，并会产生相应的调用开销。\n\n确定继续吗？',
            self,
        )
        box.yesButton.setText('重新生成')
        box.cancelButton.setText('取消')
        if not box.exec():
            return

        self.regenerate(force=True)

    def regenerate(self, force: bool = False) -> bool:
        """按当前分隔符重新切分讲稿并启动音频生成。

        正在生成或尚未导入时返回 ``False``，由跨页调用方在当前页面展示提示。
        """
        if self.is_busy() or not self.notes:
            return False

        self.getFileButton.setEnabled(False)
        self.init_general_play(force_regenerate=force)
        return True

    def apply_rewritten_notes(self, mapping: dict) -> int:
        """写回确认采用的逐页改写结果，并重建音频。

        未改动页面会自然命中原缓存。返回实际写回页数；任务忙时返回 -1。
        """
        if self.is_busy():
            return -1

        applied = 0
        for page, text in dict(mapping).items():
            if page in self.notes and str(text).strip():
                self.notes[page] = text
                applied += 1

        if not applied:
            return 0

        self.regenerate()
        return applied

    def import_pptx(self):
        """打开文件选择器并从 PowerPoint 备注导入讲稿。"""
        return self.import_script('选择 PowerPoint 文件', 'PowerPoint 演示文稿 (*.pptx)')

    def import_word(self):
        """打开文件选择器并导入可往返编辑的 Word 讲稿。"""
        return self.import_script('选择 Word 讲稿', 'Word 文档 (*.docx)')

    def import_data_script(self):
        """打开文件选择器并导入结构化讲稿 JSON。"""
        return self.import_script('选择 JSON 讲稿', 'JSON 讲稿 (*.json)')

    def import_script(self, title: str, file_filter: str):
        """执行脚本文件的选择、解析、装载和音频生成共用流程。"""
        selected, _ = QFileDialog.getOpenFileName(
            self, title, '', f'{file_filter};;{script_io.SCRIPT_FILTER};;所有文件 (*.*)')
        if not selected:
            self.create_warning_info_bar('导入已取消', '未选择文件。')
            return False

        path = Path(selected)
        self.getFileButton.setEnabled(False)
        try:
            data = script_io.load_script(path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('讲稿解析失败', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        self.note_file_path = path
        self.note_file_name = data.source_name or path.stem
        self.notes = data.notes
        # 交换文件声明的分隔符是其结构一部分，应优先于当前应用偏好。
        if data.mark.strip():
            self.mark = data.mark
            self.persist_preferences()

        self.notesPathLabel.setText(str(path))
        if data.report:
            self.create_success_info_bar('导入完成', ' / '.join(data.report))

        self.init_general_play()
        return True

    def import_package(self):
        """导入工程包，并复用历史记录加载路径恢复讲稿和音频。"""
        if self.is_busy():
            self.create_warning_info_bar('正在生成音频', '请等待当前转换完成后再试。')
            return

        selected, _ = QFileDialog.getOpenFileName(
            self, '选择工程包', '', project_package.PACKAGE_FILTER)
        if not selected:
            return

        try:
            result = project_package.import_package(Path(selected))
        except Exception as e:
            print(e)
            self.create_error_info_bar('工程包导入失败', f'详情：{e}')
            return

        if not result['has_audio']:
            self.create_warning_info_bar(
                '工程包不含音频', '该工程包仅包含讲稿与配置，需在本机重新合成。')

        try:
            self.load_session_record(result['record_path'])
        except Exception as e:
            print(e)
            self.create_error_info_bar('工程包加载失败', f'详情：{e}')
            return

        detail = f'已导入 {result["restored"]} 条音频'
        if result['skipped']:
            detail += f'，另有 {result["skipped"]} 条本机已存在'
        self.create_success_info_bar('导入完成', detail)

    def init_general_play(self, force_regenerate: bool = False):
        """规范化当前讲稿、重建分段并启动后台音频生成。"""
        try:
            self.mark_split()
        except Exception as e:
            print(e)
            self.create_error_info_bar('讲稿解析错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        self.clean_and_reset()

        try:
            self.clean_temp_folder(self.wav_temp_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('缓存清理错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

        try:
            self.save_thread.configure(
                self.notes_list, self.currentSpinBox.maximum(), force_regenerate=force_regenerate)
            self.save_thread.start()
            self.IndeterminateProgressBar.setVisible(True)
        except Exception as e:
            print(e)
            self.create_error_info_bar('语音转换错误', f'详情：{e}')
            self.getFileButton.setEnabled(True)
            return False

    def clean_and_reset(self):
        """清空当前会话数据、临时媒体和播放状态。"""
        self.stop_audio()
        self.player.setSource(QUrl())

        self.media_list = []
        self.wait_media_list = []
        self.current_index = 0
        self.notes_duration_list = []
        self.note_cache_keys = []
        self.note_cache_exts = []
        self.cache_hit_count = 0
        self.is_import = False
        self.check_import()

    def refresh_notes_duration_list(self):
        """重新读取当前媒体文件时长并更新汇总。"""
        if not self.media_list or len(self.media_list) != len(self.notes_list):
            self.load_audio_files()
        duration_list = []
        for path in self.media_list:
            duration = AudioGenerationTask.get_audio_duration(path)
            duration_list.append(duration)
        self.notes_duration_list = duration_list

    def mark_split(self):
        """按当前分隔符将逐页正文展开为有序讲稿段。"""
        notes_list = []
        # Word 页码可能不连续，必须遍历实际键而不是假定从 1 连续递增。
        for page in sorted(self.notes):
            note_text = self._LINE_BREAK_RE.sub('\n', str(self.notes[page]))
            note_text = self._CONTROL_CHARS_RE.sub('', note_text)
            # PPT 备注通常以换行结尾；先去除外围空白，才能识别真正位于末尾的分隔符，
            # 避免额外空段带来多余静音和翻页动作。
            note_text = note_text.strip()
            # 分隔符允许多字符，不能只比较最后一个字符。
            if self.mark and note_text.endswith(self.mark):
                note_text = note_text[:-len(self.mark)]
            for one_note in note_text.split(self.mark):
                notes_list.append({'page': page, 'text': one_note.strip()})
        self.notes_list = notes_list
        print(f'讲稿分割完毕，共 {len(notes_list)} 条')

    @staticmethod
    def clean_temp_folder(path: Path):
        """删除指定目录顶层的临时 WAV 和 MP3 文件。"""
        for pattern in ('*.wav', '*.mp3'):
            for file_path in path.glob(pattern):
                try:
                    file_path.unlink()
                    print(f'已清理 {file_path.name}')
                except Exception as e:
                    print(f'清理文件失败: {file_path.name}, 原因: {e}')
        print('临时音频清理完成')

    def thread_print_index(self, import_index):
        """根据已完成条数刷新生成进度。"""
        text = f'已生成：{import_index}/{len(self.notes_list)}'
        print(text)
        self.statusLabel.setText(text)

    def thread_save_error(self, message: str):
        """报告生成错误，并恢复被任务占用的界面状态。"""
        print(f'音频生成失败：{message}')
        self.IndeterminateProgressBar.setVisible(False)
        self.getFileButton.setEnabled(True)
        self.is_import = False
        self.check_import()
        self.create_error_info_bar('语音转换失败', f'详情：{message}')

    def thread_save_finish(self, result):
        """接收有序生成结果、恢复界面并保存会话。"""
        print('转换完成')
        # 并行任务完成顺序不稳定，播放列表必须使用按输入索引归位后的结果。
        self.media_list = list(result.media_paths)
        self.notes_duration_list = list(result.durations)
        self.note_cache_keys = list(result.cache_keys)
        self.note_cache_exts = list(result.cache_exts)
        self.cache_hit_count = result.cache_hit_count
        self.save_session_record()
        self.IndeterminateProgressBar.setVisible(False)
        
        # 将缓存命中数纳入提示，便于用户判断是否发生重新合成。
        total_notes = len(self.notes_list)
        if self.cache_hit_count > 0:
            cache_info = f'（命中缓存 {self.cache_hit_count}/{total_notes} 条）'
            self.create_success_info_bar('转换完成', f'音频播放功能已准备就绪 {cache_info}')
        else:
            self.create_success_info_bar('转换完成', '音频播放功能已准备就绪')
        self.getFileButton.setEnabled(True)
        self.is_import = True
        self.check_import()
        if self.notes_list:
            self.set_current_label_text()
        self.pageJumpSpinBox.setMaximum(len(self.notes))

    def save_session_record(self):
        """保存讲稿、生成配置和缓存引用，供历史记录恢复。"""
        if not self.notes_list:
            return

        generation_profile = self.ctx.tts_engine.get_generation_profile()
        if len(self.note_cache_keys) != len(self.notes_list):
            self.note_cache_keys = [
                self.ctx.tts_engine.build_audio_cache_key(item['text'], generation_profile)
                for item in self.notes_list
            ]

        if len(self.note_cache_exts) != len(self.notes_list):
            output_ext = self.ctx.tts_engine.get_output_extension()
            self.note_cache_exts = [output_ext] * len(self.notes_list)

        durations = self.notes_duration_list[:]
        if len(durations) != len(self.notes_list):
            durations = [0.0] * len(self.notes_list)

        items = []
        for index, note in enumerate(self.notes_list):
            items.append({
                'index': index,
                'page': note['page'],
                'text': note['text'],
                'duration': float(durations[index]),
                'cache_key': self.note_cache_keys[index],
                'cache_ext': self.note_cache_exts[index],
            })

        now = datetime.now()
        session_id = now.strftime('%Y%m%d_%H%M%S')
        speaker_name = self.ctx.tts_engine.get_selected_voice_name().strip()
        record = {
            'version': 1,
            'session_id': session_id,
            'created_at': now.isoformat(timespec='seconds'),
            'source_file': str(self.note_file_path) if self.note_file_path else '',
            'source_name': self.note_file_name,
            'mark': self.mark,
            'speaker': speaker_name,
            'generation_profile': generation_profile,
            'notes': self.notes,
            'items': items,
        }

        record_path = self.session_root_path / f'{session_id}.json'
        with record_path.open('w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def load_session_record_from_file(self):
        """打开记录文件选择器并恢复一条历史会话。"""
        selected = QFileDialog.getOpenFileName(
            self,
            '选择历史记录文件',
            str(self.session_root_path),
            'Session Files (*.json)'
        )
        if not selected[0]:
            return

        try:
            self.load_session_record(Path(selected[0]))
        except Exception as e:
            print(e)
            self.create_error_info_bar('历史记录加载失败', f'详情：{e}')

    def show_session_history_dialog(self):
        """显示历史记录管理器，并加载用户确认的记录。"""
        dialog = SessionHistoryDialog(self.session_root_path, self)
        if not dialog.exec():
            return

        record_path = dialog.get_selected_record_path()
        if not record_path:
            self.create_warning_info_bar('未选择记录', '请在历史记录列表中选择一条记录。')
            return

        try:
            self.load_session_record(record_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('历史记录加载失败', f'详情：{e}')

    def load_session_record(self, record_path: Path):
        """解析指定会话记录，并从缓存恢复完整播放状态。"""
        with record_path.open('r', encoding='utf-8') as f:
            record = json.load(f)

        items = record.get('items', [])
        if not items:
            raise RuntimeError('历史记录为空，无法加载')

        self.clean_and_reset()

        missing_list = []
        media_list = []
        notes_list = []
        duration_list = []
        cache_keys = []
        cache_exts = []

        for idx, item in enumerate(items):
            page = int(item.get('page', 0))
            text = str(item.get('text', ''))
            cache_key = str(item.get('cache_key', '')).strip()
            if not cache_key:
                profile = record.get('generation_profile', {})
                cache_key = self.ctx.tts_engine.build_audio_cache_key(text, profile)

            cache_ext = str(item.get('cache_ext', '')).strip().lower().lstrip('.')
            ext_candidates = [cache_ext] if cache_ext else []
            for ext in ('wav', 'mp3'):
                if ext not in ext_candidates:
                    ext_candidates.append(ext)

            cache_path = None
            for ext in ext_candidates:
                candidate = self.audio_cache_path / f'{cache_key}.{ext}'
                if candidate.exists() and candidate.stat().st_size > 0:
                    cache_path = candidate
                    cache_ext = ext
                    break

            if cache_path is None:
                missing_list.append(f'第{page}页-第{idx + 1}条')
                continue

            media_list.append(cache_path)
            notes_list.append({'page': page, 'text': text})
            duration_list.append(float(item.get('duration', 0.0)))
            cache_keys.append(cache_key)
            cache_exts.append(cache_ext)

        if missing_list:
            missing_text = '、'.join(missing_list[:10])
            raise RuntimeError(f'存在缺失音频缓存：{missing_text}')

        self.note_file_name = str(record.get('source_name', ''))
        source_file = str(record.get('source_file', '')).strip()
        self.note_file_path = Path(source_file) if source_file else None
        self.mark = str(record.get('mark', self.mark))

        notes = record.get('notes')
        restored_notes = {}
        if isinstance(notes, dict):
            for key, value in notes.items():
                try:
                    restored_notes[int(key)] = value
                except (TypeError, ValueError):
                    print(f'[历史记录] 跳过无法解析的页码：{key!r}')

        if restored_notes:
            self.notes = restored_notes
        else:
            rebuilt_notes = {}
            for item in notes_list:
                rebuilt_notes.setdefault(item['page'], []).append(item['text'])
            self.notes = {k: self.mark.join(v) for k, v in rebuilt_notes.items()}

        self.notes_list = notes_list
        self.notes_duration_list = duration_list
        self.note_cache_keys = cache_keys
        self.note_cache_exts = cache_exts

        self.media_list = media_list
        self.current_index = 0
        self.is_import = True
        self.check_import()
        self.pageJumpSpinBox.setMaximum(len(self.notes))
        if self.notes_list:
            self.set_current_label_text()

        self.notesPathLabel.setText(f'历史记录：{record_path.name}')
        self.create_success_info_bar('加载成功', '历史记录已恢复，可直接播放')


    def init_play(self):
        """从当前索引开始新的倒计时或正文播放流程。"""
        self.ctx.playback_bus.request_stop(self)
        if self.currentSwitch.isChecked():
            self.play_wait()
        else:
            self.play_notes()

    def play_wait(self):
        """播放倒计时列表中的当前音频。"""
        self.is_play_notes = False
        self.wait_current_index = 0
        self.load_wait_audio_files()
        if not self.wait_media_list:
            self.create_warning_info_bar('倒计时音频缺失', '未找到可用倒计时音频文件，请检查 data/cache/countdown 目录')
            return
        print('已导入倒计时')
        self.play_audio()

    def play_notes(self):
        """播放当前正文段对应的音频。"""
        self.is_play_notes = True
        if not self.media_list:
            self.load_audio_files()
        self.play_audio()

    def load_audio_files(self):
        """扫描临时目录，作为内存播放列表缺失时的恢复兜底。"""
        indexed = []
        for path in list(self.wav_temp_path.glob('*.wav')) + list(self.wav_temp_path.glob('*.mp3')):
            match = self._AUDIO_NAME_RE.match(path.stem)
            if match:
                indexed.append((int(match.group(1)), int(match.group(2)), path))

        self.media_list = [item[2] for item in sorted(indexed)]
        print(f'音频列表载入完成，共 {len(self.media_list)} 条')

    def load_wait_audio_files(self):
        """按降序构造当前设置秒数的现有倒计时媒体列表。"""
        audio_files = [
            path for path in self.countdown_wav_temp_path.glob('*.wav')
            if path.stem.isdigit() and int(path.stem) <= self.currentSpinBox.value()
        ]
        audio_files = sorted(audio_files, key=lambda path: int(path.stem), reverse=True)
        self.wait_media_list = audio_files
        print('倒计时列表载入完成')

    def jump_page(self):
        """将播放索引定位到用户输入页码的第一段。"""
        self.stop_audio()
        index = self.get_index_from_page(self.pageJumpSpinBox.value())
        if index > -1:
            self.current_index = index
        if self.notes_list:
            self.set_current_label_text()

    def create_success_info_bar(self, title, text):
        """在主页顶部显示短暂的成功提示。"""
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
        """在主页顶部显示短暂的警告提示。"""
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
        """在主页顶部显示需要手动关闭的错误提示。"""
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self
        )

    def show_edit_mark_dialog(self):
        """编辑页内分隔符，并在确认后重切讲稿。"""
        box = EditMarkMessageBox(self.mark, self)
        if box.exec():
            text = box.urlLineEdit.text()
            print(f'分隔符：{text}')
            self.mark = text
            self.persist_preferences()

    @staticmethod
    def s_to_str(s):
        """将秒数格式化为中文分钟和秒标签。"""
        if s < 60:
            return f'{round(s, 2)} 秒'
        # 先对总秒数取整再拆分，避免分别舍入得到“60 秒”。
        total_seconds = int(round(s))
        minutes, seconds = divmod(total_seconds, 60)
        if minutes < 60:
            return f'{minutes} 分钟 {seconds} 秒'
        hours, minutes = divmod(minutes, 60)
        return f'{hours} 小时 {minutes} 分钟 {seconds} 秒'

    def count_words(self):
        """统计全部讲稿段去除空白后的字符数。"""
        text = ''
        for page in self.notes:
            text += self.notes[page]
        text = re.sub(r'\s+', '', text)
        return len(text)

    def show_info_dialog(self):
        """展示当前讲稿的页数、段数、字数和预计时长。"""
        title = '统计信息'
        if len(self.notes_duration_list) != len(self.notes_list):
            self.refresh_notes_duration_list()
        if not self.notes_duration_list or not self.notes_list:
            self.create_warning_info_bar('暂无统计信息', '请先导入并生成音频后再查看统计。')
            return

        words_count = self.count_words()
        max_duration = max(self.notes_duration_list)
        max_duration_index = self.notes_duration_list.index(max_duration)
        min_duration = min(self.notes_duration_list)
        min_duration_index = self.notes_duration_list.index(min_duration)

        content_list = [
            ['页码总计', f'{len(self.notes)} 页'],
            ['音频总计', f'{len(self.notes_list)} 条'],
            ['演讲稿字数总计', f'{words_count} 字'],
            ['音频总时长', f'{self.s_to_str(sum(self.notes_duration_list))}\n'],
            ['最长音频时长', f'{self.s_to_str(max_duration)}'],
            ['最长音频序号', f'第 {max_duration_index + 1} 条'],
            ['最长音频所属页码', f'第 {self.notes_list[max_duration_index]["page"]} 页'],
            ['最短音频时长', f'{self.s_to_str(min_duration)}'],
            ['最短音频序号', f'第 {min_duration_index + 1} 条'],
            ['最短音频所属页码', f'第 {self.notes_list[min_duration_index]["page"]} 页'],
        ]

        content = ''
        for item in content_list:
            content += f'{item[0]}：{item[1]}\n'
        dialog = MessageBox(title, content, self)
        dialog.yesButton.setText('确定')
        dialog.cancelButton.setVisible(False)
        dialog.exec()
