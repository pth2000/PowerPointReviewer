import json
import logging
import os
import re
import sys
import webbrowser
import pyautogui
import requests
from PySide6.QtGui import QIcon
from mutagen import File
from pptx import Presentation
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from PySide6.QtCore import Signal, QThread, QUrl, QTimer, Qt, QSize
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import QFileDialog, QApplication, QWidget
from qfluentwidgets import (FluentWindow, FluentIcon, InfoBar, InfoBarPosition, NavigationItemPosition,
                            InfoLevel, setThemeColor, RoundMenu, Action, MessageBoxBase, SubtitleLabel,
                            LineEdit, MessageBox, SplashScreen)

# Import our custom modules
from config import (
    VERSION, APP_NAME, COPYRIGHT, WINDOW_SIZE, SPLASH_ICON_SIZE,
    DEFAULT_THEME_COLOR, WORD_THEME_COLOR, PPT_THEME_COLOR,
    TEMP_DIR, COUNTDOWN_TEMP_DIR, DEFAULT_MARK, DEFAULT_COUNTDOWN_MAX,
    SUPPORTED_PPT_EXTENSIONS, SUPPORTED_WORD_EXTENSIONS, SUPPORTED_AUDIO_EXTENSIONS,
    INFO_BAR_DURATIONS, GITHUB_URL, GITEE_URL, UPDATE_API_URL, UI_TEXT,
    ensure_temp_directories
)
from exceptions import (
    PowerPointReviewerError, FileImportError, TTSError, AudioProcessingError,
    handle_exception, safe_execute
)
from logger_utils import get_logger, setup_logger
from utils import (
    sanitize_text_for_tts, validate_file_path, safe_file_operation,
    clean_audio_files, get_audio_duration, format_duration,
    safe_json_save, count_chinese_characters, generate_filename_with_suffix
)
from Ui_mainwindow import Ui_mainwindow
from settingInterface import Ui_settingInterface
from toolsInterface import Ui_toolsInterface
from tts_engine import TTSEngine

# Setup application logger
logger = get_logger()


class PPTReviewer(QWidget, Ui_mainwindow):
    """主要实现"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setupUi(self)
        logger.info("Initializing PPTReviewer")

        # Initialize media player
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        
        # Initialize data structures
        self.media_list = []  # 存储音频文件的列表
        self.current_index = 0  # 当前播放的音频文件索引
        self.wait_current_index = 0  # 倒计时索引
        self.note_file_path = ''
        self.note_file_name = ''
        self.notes = {}  # 每页讲稿
        self.notes_list = []  # 每块讲稿
        self.notes_duration_list = [0]
        self.is_play_notes = False
        self.is_import = False
        self.mark = DEFAULT_MARK

        # Setup UI icons and components
        self._setup_ui_components()
        
        # Setup event handlers
        self._setup_event_handlers()
        
        # Initialize timer for audio transitions
        self.next_timer = QTimer(self)
        self.next_timer.timeout.connect(self.timeout_play_next_audio)

        # Setup temporary directories
        self.wav_temp_path = TEMP_DIR
        self.countdown_wav_temp_path = COUNTDOWN_TEMP_DIR
        ensure_temp_directories()

        # Initialize worker thread
        self.save_thread = SaveThread()
        self.save_thread.signal_import_index.connect(self.thread_print_index)
        self.save_thread.signal_finish.connect(self.thread_save_finish)

        # Check initial import status
        self.check_import()
        logger.info("PPTReviewer initialized successfully")

    def _setup_ui_components(self) -> None:
        """Setup UI components and icons."""
        # Set up icons
        self.partingIconWidget.setIcon(QIcon(':/image/image/parting.svg'))
        self.fileIconWidget.setIcon(QIcon(':/image/image/Folder.svg'))
        self.currentIconWidget.setIcon(QIcon(':/image/image/countdown.svg'))
        self.currentTimeIconWidget.setIcon(QIcon(':/image/image/Clock.svg'))
        self.pageJumpToolButton.setIcon(FluentIcon.ACCEPT_MEDIUM)
        self.playButton.setIcon(QIcon(':/image/image/play.svg'))
        self.stopButton.setIcon(QIcon(':/image/image/stop.svg'))
        self.resetButton.setIcon(QIcon(':/image/image/backward.svg'))
        
        # Hide progress bar initially
        self.IndeterminateProgressBar.setVisible(False)

        # Setup file button and menu
        self.getFileButton.setIcon(QIcon(':/image/image/ppt.svg'))
        self.file_button_menu = RoundMenu(parent=self)
        self.file_button_menu.addAction(
            Action(QIcon(':/image/image/word.svg'), '导入 Word', triggered=self.init_word_play))
        self.getFileButton.setFlyout(self.file_button_menu)
        
        # Enable transparent background
        self.bgScrollArea.enableTransparentBackground()

    def _setup_event_handlers(self) -> None:
        """Setup event handlers for UI components."""
        self.playButton.clicked.connect(self.init_play)
        self.stopButton.clicked.connect(self.stop_audio)
        self.resetButton.clicked.connect(self.reset_audio)
        self.getFileButton.clicked.connect(self.init_ppt_play)
        self.editMarkPushButton.clicked.connect(self.show_edit_mark_dialog)
        self.pageJumpToolButton.clicked.connect(self.jump_page)
        self.infoPushButton.clicked.connect(self.show_info_dialog)
    def check_import(self) -> None:
        """检查导入状态并更新UI"""
        try:
            if self.is_import:
                self.playCardWidget.setEnabled(True)
                self.playCardWidget_2.setEnabled(True)
                self.playCardWidget_3.setEnabled(True)
                self.statusLabel.setText(UI_TEXT['status']['imported'])
                self.IconInfoBadge.setLevel(InfoLevel.SUCCESS)
                self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
                logger.debug("UI updated to imported state")
            else:
                self.playCardWidget.setEnabled(False)
                self.playCardWidget_2.setEnabled(False)
                self.playCardWidget_3.setEnabled(False)
                self.statusLabel.setText(UI_TEXT['status']['not_imported'])
                self.IconInfoBadge.setLevel(InfoLevel.INFOAMTION)
                self.IconInfoBadge.setIcon(FluentIcon.ACCEPT_MEDIUM)
                logger.debug("UI updated to not imported state")
        except Exception as e:
            logger.error(f"Error updating import status UI: {e}")

    def play_audio(self):
        """播放音频文件"""
        if self.is_play_notes:  # 播放讲稿
            if self.current_index < len(self.media_list):  # 正在播放
                self.player.setSource(QUrl.fromLocalFile(self.media_list[self.current_index]))
                self.player.play()
                self.playButton.setEnabled(False)  # 禁用播放按钮
                self.currentStatusLabel.setText('播放')
                self.set_current_label_text()
                print(self.notes_list[self.current_index]["text"])
            else:
                print('播放完毕')
                self.reset_audio()

        else:  # 播放倒计时
            if self.wait_current_index < len(self.media_list):  # 正在播放
                self.player.setSource(QUrl.fromLocalFile(self.media_list[self.wait_current_index]))
                self.player.play()
                self.playButton.setEnabled(False)  # 禁用播放按钮
                temp_index = len(self.media_list) - self.wait_current_index
                self.currentStatusLabel.setText('倒计时')
                self.currentPageLabel.setText(f'{temp_index}')
                self.currentIndexLabel.setText(f'{temp_index}')
            else:
                print('播放完毕')
                self.wait_current_index = 0
                self.play_notes()

    def media_status_changed(self, status):
        """媒体状态转移"""
        # 播放结束，切换至下一个音频
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_timer.start(100)

    def stop_audio(self):
        """停止播放"""
        self.player.stop()
        self.playButton.setEnabled(True)  # 启用播放按钮
        self.currentStatusLabel.setText('停止')

    def reset_audio(self):
        """重置播放"""
        self.stop_audio()
        self.current_index = 0
        self.set_current_label_text()

    def set_current_label_text(self):
        self.currentPageLabel.setText(f'{self.notes_list[self.current_index]["page"]} / {len(self.notes)}')
        self.currentIndexLabel.setText(f'{self.current_index + 1} / {len(self.notes_list)}')

    def get_index_from_page(self, page):
        notes_list = self.notes_list
        for i, item in enumerate(notes_list):
            if item['page'] == page:
                return i
        return -1

    def timeout_play_next_audio(self):
        """定时结束，自动播放下一个音频"""
        self.next_timer.stop()
        if self.is_play_notes:
            if self.scrollEnableSwitch.isChecked():  # 同步翻页
                pyautogui.press("pagedown")
            self.current_index += 1
        else:
            self.wait_current_index += 1
        self.play_audio()

    def init_word_play(self) -> None:
        """初始化Word文档导入"""
        logger.info("Starting Word document import")
        self.getFileButton.setEnabled(False)
        
        try:
            success, error_msg = self._get_word_path()
            if not success:
                if error_msg:
                    self.create_warning_info_bar(UI_TEXT['messages']['import_cancelled'], error_msg)
                self.getFileButton.setEnabled(True)
                return

            self.notesPathLabel.setText(self.note_file_path)
            setThemeColor(WORD_THEME_COLOR)

            success, error_msg = self._parse_word_document()
            if not success:
                self.create_error_info_bar('Word 文件解析错误', error_msg)
                self.getFileButton.setEnabled(True)
                return

            self.init_general_play()
            
        except Exception as e:
            error_msg = handle_exception(e, "Word导入")
            self.create_error_info_bar('Word 导入错误', error_msg)
            self.getFileButton.setEnabled(True)

    def init_ppt_play(self) -> None:
        """初始化PPT文档导入"""
        logger.info("Starting PowerPoint document import")
        self.getFileButton.setEnabled(False)

        try:
            success, error_msg = self._get_ppt_path()
            if not success:
                if error_msg:
                    self.create_warning_info_bar(UI_TEXT['messages']['import_cancelled'], error_msg)
                self.getFileButton.setEnabled(True)
                return

            self.notesPathLabel.setText(self.note_file_path)
            setThemeColor(PPT_THEME_COLOR)

            success, error_msg = self._parse_ppt_document()
            if not success:
                self.create_error_info_bar('PowerPoint 文件解析错误', error_msg)
                self.getFileButton.setEnabled(True)
                return

            self.init_general_play()
            
        except Exception as e:
            error_msg = handle_exception(e, "PowerPoint导入")
            self.create_error_info_bar('PowerPoint 导入错误', error_msg)
            self.getFileButton.setEnabled(True)

    def _get_word_path(self) -> tuple[bool, str]:
        """获取Word文档路径
        
        Returns:
            Tuple of (success, error_message)
        """
        selected_files = QFileDialog.getOpenFileName(
            self, "选择Word文件", "", "Word Files (*.docx)"
        )
        
        self.note_file_path = selected_files[0]
        if not self.note_file_path:
            logger.info("Word file selection cancelled by user")
            return False, ""
        
        if not validate_file_path(self.note_file_path, SUPPORTED_WORD_EXTENSIONS):
            return False, "选择的Word文件无效"
        
        self.set_filename()
        logger.info(f"Selected Word file: {self.note_file_path}")
        return True, ""

    def _get_ppt_path(self) -> tuple[bool, str]:
        """获取PowerPoint文档路径
        
        Returns:
            Tuple of (success, error_message)
        """
        selected_files = QFileDialog.getOpenFileName(
            self, "选择PowerPoint文件", "", "PowerPoint Files (*.pptx)"
        )
        
        self.note_file_path = selected_files[0]
        if not self.note_file_path:
            logger.info("PowerPoint file selection cancelled by user")
            return False, ""
        
        if not validate_file_path(self.note_file_path, SUPPORTED_PPT_EXTENSIONS):
            return False, "选择的PowerPoint文件无效"
        
        self.set_filename()
        logger.info(f"Selected PowerPoint file: {self.note_file_path}")
        return True, ""

    def _parse_word_document(self) -> tuple[bool, str]:
        """解析Word文档获取讲稿
        
        Returns:
            Tuple of (success, error_message)
        """
        success, result, error_msg = safe_execute(
            self.get_word_notes_dict,
            context="Word文档解析"
        )
        
        if not success:
            return False, error_msg
        
        logger.info(f"Successfully parsed Word document with {len(self.notes)} pages")
        return True, ""

    def _parse_ppt_document(self) -> tuple[bool, str]:
        """解析PowerPoint文档获取讲稿
        
        Returns:
            Tuple of (success, error_message)
        """
        success, result, error_msg = safe_execute(
            self.get_ppt_notes_dict,
            context="PowerPoint文档解析"
        )
        
        if not success:
            return False, error_msg
        
        logger.info(f"Successfully parsed PowerPoint document with {len(self.notes)} pages")
        return True, ""
    def init_general_play(self) -> None:
        """通用初始化流程"""
        logger.info("Starting general initialization")
        
        try:
            # Parse notes with delimiter
            success, error_msg = self._split_notes_by_mark()
            if not success:
                self.create_error_info_bar('讲稿解析错误', error_msg)
                self.getFileButton.setEnabled(True)
                return

            # Clean previous state
            self.clean_and_reset()

            # Clean temporary audio files
            success, error_msg = self._clean_temp_audio_files()
            if not success:
                self.create_error_info_bar('缓存清理错误', error_msg)
                self.getFileButton.setEnabled(True)
                return

            # Start TTS conversion in background thread
            try:
                self.save_thread.start()
                self.IndeterminateProgressBar.setVisible(True)
                logger.info("Started TTS conversion thread")
            except Exception as e:
                error_msg = handle_exception(e, "语音转换启动")
                self.create_error_info_bar('语音转换错误', error_msg)
                self.getFileButton.setEnabled(True)
                
        except Exception as e:
            error_msg = handle_exception(e, "初始化")
            self.create_error_info_bar('初始化错误', error_msg)
            self.getFileButton.setEnabled(True)

    def _split_notes_by_mark(self) -> tuple[bool, str]:
        """按标记符分割讲稿
        
        Returns:
            Tuple of (success, error_message)
        """
        success, result, error_msg = safe_execute(
            self.mark_split,
            context="讲稿分割"
        )
        
        if not success:
            return False, error_msg
        
        logger.info(f"Successfully split notes into {len(self.notes_list)} segments")
        return True, ""

    def _clean_temp_audio_files(self) -> tuple[bool, str]:
        """清理临时音频文件
        
        Returns:
            Tuple of (success, error_message)
        """
        # Clean main temp directory
        success1, msg1 = clean_audio_files(self.wav_temp_path)
        if not success1:
            return False, msg1
        
        # Clean countdown temp directory  
        success2, msg2 = clean_audio_files(self.countdown_wav_temp_path)
        if not success2:
            return False, msg2
        
        logger.info("Cleaned temporary audio files")
        return True, ""

    def clean_and_reset(self) -> None:
        """清理已导入的内容并重置状态"""
        logger.debug("Cleaning and resetting application state")
        
        try:
            self.stop_audio()
            self.player.setSource('')
            self.media_list = []
            self.current_index = 0
            self.is_import = False
            self.check_import()
            logger.debug("Application state reset successfully")
        except Exception as e:
            logger.error(f"Error during clean and reset: {e}")

    def set_filename(self) -> None:
        """根据文件路径生成文件名"""
        try:
            filename = os.path.basename(self.note_file_path)
            self.note_file_name = os.path.splitext(filename)[0]
            logger.debug(f"Set filename to: {self.note_file_name}")
        except Exception as e:
            logger.error(f"Error setting filename: {e}")
            self.note_file_name = "untitled"

    def get_word_notes_dict(self):
        """从word获取讲稿字典"""
        doc = Document(self.note_file_path)
        page_data = {}  # 用于存储页数和内容的字典
        current_page = None  # 当前页码
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            # 检查段落中是否包含“第x页”标记
            if text.startswith("第") and text.endswith("页"):
                # 提取页码
                page_text = text[1:-1]  # 去掉“第”和“页”
                try:
                    current_page = int(page_text)
                except ValueError:
                    current_page = None
            elif current_page is not None:
                if current_page in page_data:
                    page_data[current_page] += '\n' + text
                else:
                    page_data[current_page] = text
        self.notes = page_data

    def get_ppt_notes_dict(self):
        """从PPT备注获取讲稿字典"""
        presentation = Presentation(self.note_file_path)
        i = 1
        notes_dict = {}
        # 遍历 PPT 中的每一页
        for slide in presentation.slides:
            # 遍历每一页中的每一个备注
            text = ''
            for note in slide.notes_slide.notes_text_frame.paragraphs:
                text += note.text + '\n'
            notes_dict[i] = text
            i += 1
        self.notes = notes_dict

    def mark_split(self):
        """按标记符分割讲稿"""
        notes_list = []
        for page in range(1, len(self.notes) + 1):
            note_text = re.sub(r'[\x00-\x1f\x7f]+', '', self.notes[page])
            if note_text:
                if note_text[-1] == self.mark:
                    note_text = note_text[:-1]
            note_list = note_text.split(self.mark)  # 按分隔符分割
            for one_note in note_list:
                notes_list.append({'page': page, 'text': one_note.strip()})
        self.notes_list = notes_list
        print('讲稿分割完毕')

    @staticmethod
    def clean_temp_folder(path):
        """清理缓存wav"""
        for filename in os.listdir(path):
            if filename.endswith(".wav"):
                file_path = os.path.join(path, filename)
                os.remove(file_path)
                print(f"已清理: {filename}")
        print('清理完成')

    def thread_print_index(self, import_index):
        """信号，生成数据更新"""
        text = f'已生成：{import_index}/{len(self.notes_list)}'
        print(text)
        self.statusLabel.setText(text)

    def thread_save_finish(self):
        """信号，转换完成"""
        print('转换完成！')
        self.IndeterminateProgressBar.setVisible(False)
        self.create_success_info_bar('转换完成', '语音播放功能已准备就绪。')
        self.getFileButton.setEnabled(True)
        self.is_import = True
        self.check_import()
        self.set_current_label_text()
        self.pageJumpSpinBox.setMaximum(len(self.notes))

    def init_play(self):
        """准备播放"""
        if self.currentSwitch.isChecked():
            # 载入倒计时，播放
            self.play_wait()
        else:
            # 载入音频，播放
            self.play_notes()

    def play_wait(self):
        """播放倒计时"""
        self.is_play_notes = False
        self.wait_current_index = 0
        self.load_wait_audio_files()
        print('已导入倒计时')
        self.play_audio()

    def play_notes(self):
        """播放讲稿"""
        self.is_play_notes = True
        if not self.media_list or 'countdown' in self.media_list[0]:  # 还未载入，必须载入；载入的为倒计时，需重新载入
            self.load_audio_files()
        self.play_audio()

    def load_audio_files(self):
        """查找所有.wav，添加到media_list中"""
        audio_files = [f for f in os.listdir(self.wav_temp_path) if f.endswith(".wav")]
        audio_files = sorted(audio_files, key=lambda x: [int(part) if part.isdigit() else part
                                                         for part in os.path.splitext(x)[0].split("_")])
        self.media_list = [f'{self.wav_temp_path}/{f}' for f in audio_files]
        print('音频列表载入完成')

    def load_wait_audio_files(self):
        """获取指定数量的倒计时wav，添加到media_list中"""
        audio_files = [f for f in os.listdir(self.countdown_wav_temp_path)
                       if f.endswith(".wav") and int(f.split('.')[0]) <= self.currentSpinBox.value()]
        audio_files = sorted(audio_files, key=lambda x: int(os.path.splitext(x)[0]), reverse=True)
        self.media_list = [f'{self.countdown_wav_temp_path}/{f}' for f in audio_files]
        print('倒计时列表载入完成')

    def jump_page(self):
        self.stop_audio()
        index = self.get_index_from_page(self.pageJumpSpinBox.value())
        if index > -1:
            self.current_index = index
        self.set_current_label_text()

    def create_success_info_bar(self, title: str, text: str) -> None:
        """创建成功消息框
        
        Args:
            title: 消息标题
            text: 消息内容
        """
        try:
            InfoBar.success(
                title=title,
                content=text,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=INFO_BAR_DURATIONS['success'],
                parent=self
            )
            logger.info(f"Success message: {title} - {text}")
        except Exception as e:
            logger.error(f"Failed to show success info bar: {e}")

    def create_warning_info_bar(self, title: str, text: str) -> None:
        """创建警告消息框
        
        Args:
            title: 消息标题
            text: 消息内容
        """
        try:
            InfoBar.warning(
                title=title,
                content=text,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=INFO_BAR_DURATIONS['warning'],
                parent=self
            )
            logger.warning(f"Warning message: {title} - {text}")
        except Exception as e:
            logger.error(f"Failed to show warning info bar: {e}")

    def create_error_info_bar(self, title: str, text: str) -> None:
        """创建错误消息框
        
        Args:
            title: 消息标题
            text: 消息内容
        """
        try:
            InfoBar.error(
                title=title,
                content=text,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=INFO_BAR_DURATIONS['error'],
                parent=self
            )
            logger.error(f"Error message: {title} - {text}")
        except Exception as e:
            logger.error(f"Failed to show error info bar: {e}")

    def show_edit_mark_dialog(self):
        """编辑分隔符弹窗"""
        box = EditMarkMessageBox(self)
        if box.exec():
            text = box.urlLineEdit.text()
            print(f'分隔符：{text}')
            self.mark = text

    def count_words(self) -> int:
        """字数统计
        
        Returns:
            讲稿总字数
        """
        try:
            text = ''
            for page in self.notes:
                text += self.notes[page]
            return count_chinese_characters(text)
        except Exception as e:
            logger.error(f"Error counting words: {e}")
            return 0

    def show_info_dialog(self) -> None:
        """显示统计弹窗"""
        try:
            if not self.notes_duration_list or not self.notes_list:
                self.create_warning_info_bar('统计信息', '请先导入演讲稿后查看统计信息')
                return
            
            title = '统计信息'
            words_count = self.count_words()
            
            # Safely get duration statistics
            durations = [d for d in self.notes_duration_list if d > 0]
            if not durations:
                self.create_warning_info_bar('统计信息', '音频时长信息不可用')
                return
                
            max_duration = max(durations)
            max_duration_index = self.notes_duration_list.index(max_duration)
            min_duration = min(durations)
            min_duration_index = self.notes_duration_list.index(min_duration)
            
            content_list = [
                ['页码总计', f'{len(self.notes)} 页'],
                ['音频总计', f'{len(self.notes_list)} 条'],
                ['演讲稿字数总计', f'{words_count} 字'],
                ['音频总时长', f'{format_duration(sum(self.notes_duration_list))}\n'],
                ['最长音频时长', f'{format_duration(max_duration)}'],
                ['最长音频索引', f'{max_duration_index + 1}'],
                ['最长音频所属页码', f'第 {self.notes_list[max_duration_index]["page"]} 页'],
                ['最短音频时长', f'{format_duration(min_duration)}'],
                ['最短音频索引', f'{min_duration_index + 1}'],
                ['最短音频所属页码', f'第 {self.notes_list[min_duration_index]["page"]} 页']
            ]

            content = ''
            for item in content_list:
                content += f'{item[0]}：{item[1]}\n'
                
            dialog = MessageBox(title, content, self)
            dialog.yesButton.setText('确定')
            dialog.cancelButton.setVisible(False)
            dialog.exec()
            
            logger.info("Displayed statistics dialog")
            
        except Exception as e:
            error_msg = handle_exception(e, "统计信息显示")
            self.create_error_info_bar('统计错误', error_msg)


class SaveThread(QThread):
    """生成语音线程"""
    signal_import_index = Signal(int)
    signal_finish = Signal()

    def run(self):
        """运行TTS转换"""
        try:
            logger.info("Starting TTS conversion thread")
            self.save_countdown_wav()
            self.save_wav()
            self.signal_finish.emit()
            logger.info("TTS conversion completed successfully")
        except Exception as e:
            logger.error(f"TTS conversion failed: {e}")
            # Note: In a production app, we'd want to emit an error signal

    def save_wav(self) -> None:
        """调用TTS保存文字为wav"""
        info_list = []
        
        for index, note_dict in enumerate(w.ppt_r.notes_list):
            try:
                # Sanitize text before TTS conversion
                text = sanitize_text_for_tts(note_dict['text'])
                if not text:
                    logger.warning(f"Empty text after sanitization for note {index + 1}")
                    info_list.append(0.0)
                    continue
                
                path = f'{w.ppt_r.wav_temp_path}/{note_dict["page"]}_{index + 1}.wav'
                
                # Save TTS file
                tts.save_file(text, path)
                
                # Get duration safely
                duration = get_audio_duration(path)
                info_list.append(duration)
                
                self.signal_import_index.emit(index + 1)
                logger.debug(f"Generated TTS for note {index + 1}, duration: {duration:.2f}s")
                
            except Exception as e:
                logger.error(f"Failed to generate TTS for note {index + 1}: {e}")
                info_list.append(0.0)  # Add placeholder duration
        
        w.ppt_r.notes_duration_list = info_list
        logger.info(f"Generated {len(info_list)} TTS files")

    @staticmethod
    def save_countdown_wav() -> None:
        """生成倒计时语音"""
        try:
            countdown_max = w.ppt_r.currentSpinBox.maximum()
            for time_num in range(countdown_max, 0, -1):
                path = f'{w.ppt_r.countdown_wav_temp_path}/{time_num}.wav'
                tts.save_file_local(f'{time_num}', path)
            
            logger.info(f"Generated {countdown_max} countdown files")
        except Exception as e:
            logger.error(f"Failed to generate countdown files: {e}")
            raise


class UpdateThread(QThread):
    """检查更新线程"""
    signal_finish = Signal(list)

    def run(self):
        """运行更新检查"""
        logger.info("Starting update check")
        self.get_update()

    def get_update(self) -> None:
        """HTTP从gitee获取更新"""
        try:
            response = requests.get(UPDATE_API_URL, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            error_msg = handle_exception(e, "获取更新")
            logger.error(f"Update check failed: {e}")
            self.signal_finish.emit([2, '获取更新失败', error_msg])
            return

        try:
            res = response.json()
            latest_version = res['tag_name']
            if latest_version.startswith('v'):
                latest_version = latest_version[1:]
            
            latest_version_name = res['name']
            latest_version_time = res['created_at']
            latest_version_download_url = res['assets'][0]['browser_download_url']

            if self.compare_versions(VERSION, latest_version):
                update_msg = f'当前版本为最新版\n服务器版本：{latest_version}\n更新时间：{latest_version_time}'
                self.signal_finish.emit([0, '获取更新成功', update_msg])
                logger.info("Already using latest version")
            else:
                update_msg = (f"发现新版本！{VERSION} --> {latest_version}\n"
                             f"更新内容：{latest_version_name}\n更新时间：{latest_version_time}")
                self.signal_finish.emit([1, '获取更新成功', update_msg, latest_version_download_url])
                logger.info(f"New version available: {latest_version}")
                
        except (KeyError, IndexError, ValueError) as e:
            error_msg = f"HTTP 解析失败: {str(e)}"
            logger.error(error_msg)
            self.signal_finish.emit([2, 'HTTP 解析失败', error_msg])

    @staticmethod
    def compare_versions(version1, version2):
        """版本号比较，1>=2 -> True"""
        parts1 = version1.split('.')
        parts2 = version2.split('.')

        # 找到较短版本号的长度
        min_length = min(len(parts1), len(parts2))

        # 逐一比较各个部分
        for i in range(min_length):
            if int(parts1[i]) < int(parts2[i]):
                return False  # version1 < version2
            elif int(parts1[i]) > int(parts2[i]):
                return True  # version1 > version2

        # 如果迄今为止所有部分都相等，检查较长版本号的余下部分
        if len(parts1) < len(parts2):
            return False  # version1 < version2
        elif len(parts1) > len(parts2):
            return True  # version1 > version2

        return True  # 两个版本号相等


class EditMarkMessageBox(MessageBoxBase):
    """编辑分隔符界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel('编辑分隔符', self)
        self.urlLineEdit = LineEdit(self)

        self.urlLineEdit.setPlaceholderText('请输入您的讲稿分隔符，如“●”')
        self.urlLineEdit.setClearButtonEnabled(True)
        self.urlLineEdit.setText(w.ppt_r.mark)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.urlLineEdit)

        self.yesButton.setText('保存')
        self.cancelButton.setText('取消')

        self.widget.setMinimumWidth(350)
        if not self.urlLineEdit.text():
            self.yesButton.setDisabled(True)
        self.urlLineEdit.textChanged.connect(self._validate_url)

    def _validate_url(self, text):
        self.yesButton.setEnabled(QUrl(text).isValid())


class ToolsInterface(QWidget, Ui_toolsInterface):
    """工具页"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setupUi(self)
        self.bgScrollArea.enableTransparentBackground()

        self.transformPPTPushButton.clicked.connect(self.write_to_ppt)
        self.transformWordPushButton.clicked.connect(self.write_to_word)
        self.transformJsonPushButton.clicked.connect(self.write_to_json)

    def write_to_ppt(self):
        """导入PPT备注"""
        notes_dict = w.ppt_r.notes

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
            ppt = Presentation(ppt_path)
            for slide in ppt.slides:
                slide_number = ppt.slides.index(slide) + 1  # PowerPoint中的幻灯片索引从0开始，所以需要加1
                if slide_number in notes_dict:
                    notes_slide = slide.notes_slide
                    notes_text = notes_dict[slide_number]
                    notes_slide.notes_text_frame.text = notes_text
            ppt_name = ppt_path.split('/')[-1][:-5]
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 读取错误', f'详情：{e}')
            return False

        path = f'{dir_path}/{ppt_name}_NEW.pptx'

        try:
            ppt.save(path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('PowerPoint 保存错误', f'详情：{e}')
            return False
        self.create_success_info_bar('生成成功', f'讲稿备注已导入完毕，文件路径：{path}')

    def write_to_word(self):
        """写入word"""
        notes = w.ppt_r.notes
        file_name = w.ppt_r.note_file_name

        if not notes:
            self.create_warning_info_bar('演讲稿未导入', '请先导入演讲稿。')
            return False

        dir_path = self.get_dir_path()
        file_name = f'{file_name}_Notes.docx'
        file_path = f'{dir_path}/{file_name}'
        if not dir_path:
            self.create_warning_info_bar('目录选择已取消', '请重新选择保存目录。')
            return False

        try:
            document = Document()
            for page_number, page_note in notes.items():
                # 写入页面编号
                p_page_number = document.add_paragraph()
                p_page_number.style = 'Heading 1'
                p_page_number.paragraph_format.space_before = Pt(0)  # 段前
                p_page_number.paragraph_format.space_after = Pt(0)  # 段后
                p_page_number.paragraph_format.line_spacing = 1.5
                run_page_number = p_page_number.add_run(f"第{page_number}页")
                run_page_number.font.name = 'Microsoft YaHei'
                run_page_number.font.size = Pt(14)  # 设置字号
                run_page_number.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
                run_page_number.bold = True
                run_page_number.font.color.rgb = RGBColor(0, 0, 0)
                # 写入页面备注
                p_page_note = document.add_paragraph()
                p_page_note.paragraph_format.space_before = Pt(0)  # 段前
                p_page_note.paragraph_format.space_after = Pt(0)  # 段后
                p_page_note.paragraph_format.line_spacing = 1.5
                page_note = re.sub(u"[\\x00-\\x08\\x0b\\x0e-\\x1f\\x7f]", "", page_note)
                run_page_note = p_page_note.add_run(page_note.strip("\n"))
                run_page_note.font.name = 'Microsoft YaHei'
                run_page_note.font.size = Pt(11)  # 设置字号
                run_page_note.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
            document.save(file_path)
        except Exception as e:
            print(e)
            self.create_error_info_bar('Word 保存错误', f'详情：{e}')
            return False
        self.create_success_info_bar('转换成功', f'讲稿备注已转换完毕，文件路径：{file_path}')

    def write_to_json(self):
        """写入json"""
        notes_dict = w.ppt_r.notes
        file_name = w.ppt_r.note_file_name
        if not notes_dict:
            self.create_warning_info_bar('演讲稿未导入', '请先导入演讲稿。')
            return False

        dir_path = self.get_dir_path()
        file_name = f'{file_name}_Notes.json'
        file_path = f'{dir_path}/{file_name}'
        if not dir_path:
            self.create_warning_info_bar('目录选择已取消', '请重新选择保存目录。')
            return False

        try:
            with open(file_path, 'w') as json_file:
                json.dump(notes_dict, json_file)
        except Exception as e:
            print(e)
            self.create_error_info_bar('JSON 保存错误', f'详情：{e}')
            return False
        self.create_success_info_bar('转换成功', f'讲稿备注已转换完毕，文件路径：{file_path}')

    def get_dir_path(self):
        """获取目录"""
        return QFileDialog.getExistingDirectory(self, '选择保存文件夹', '', QFileDialog.Option.ShowDirsOnly)

    def get_ppt_path(self):
        """获取ppt路径"""
        selected_files = QFileDialog.getOpenFileName(self, "选择PowerPoint文件", "", "PowerPoint Files (*.pptx)")
        return selected_files[0]

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


class SettingInterface(QWidget, Ui_settingInterface):
    """设置页"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setupUi(self)
        logger.info("Initializing SettingInterface")

        # Setup icons and version info
        self.versionIconWidget.setIcon(QIcon(':/image/image/update.svg'))
        self.copyrightIconWidget.setIcon(QIcon(':/image/image/info.svg'))
        self.githubButton.setIcon(FluentIcon.GITHUB)
        self.giteeButton.setIcon(QIcon(':/image/image/gitee.svg'))
        self.versionLabel.setText(VERSION)
        
        # Initialize TTS settings
        try:
            self.rateSpinBox.setValue(tts.get_rate())
            self.volumeDoubleSpinBox.setValue(tts.get_volume())
        except Exception as e:
            logger.warning(f"Failed to load TTS settings: {e}")
            
        self.bgScrollArea.enableTransparentBackground()

        # Connect event handlers
        self.savePushButton.clicked.connect(self.save_setting)
        self.versionPrimaryPushButton.clicked.connect(self.get_update)
        self.githubButton.clicked.connect(self.open_github_url)
        self.giteeButton.clicked.connect(self.open_gitee_url)

        # Setup TTS engine options
        self.engineSelectComboBox.addItem('本地 TTSx3 引擎')
        self.engineSelectComboBox.addItem('在线 edge-TTS 引擎')
        self.setup_voices_list()
        self.engineSelectComboBox.currentIndexChanged.connect(self.change_tts_engine)

        # Initialize update thread
        self.update_thread = UpdateThread()
        self.update_thread.signal_finish.connect(self.thread_get_update_finish)

    def setup_voices_list(self) -> None:
        """设置语音列表"""
        try:
            voices_list = tts.get_voices_list()
            self.engineComboBox.clear()
            for item in voices_list:
                self.engineComboBox.addItem(item)
            logger.debug(f"Loaded {len(voices_list)} voices")
        except Exception as e:
            logger.error(f"Failed to setup voices list: {e}")

    def change_tts_engine(self) -> None:
        """切换TTS引擎"""
        try:
            is_local = self.engineSelectComboBox.currentIndex() == 0
            tts.switch_mode(is_local)
            
            if not is_local:
                self.create_warning_info_bar('已选择在线引擎',
                                             '在线引擎受网络速度影响，导出速度较慢，稳定性较低，请谨慎使用！\n'
                                             '目前在线引擎不支持调节音量与语速！')
            
            # Update UI based on engine capabilities
            self.CardWidget_2.setEnabled(is_local)
            self.CardWidget_3.setEnabled(is_local)
            self.setup_voices_list()
            
        except Exception as e:
            error_msg = handle_exception(e, "TTS引擎切换")
            self.create_error_info_bar('引擎切换错误', error_msg)

    def save_setting(self) -> None:
        """保存TTS设置"""
        try:
            # Validate settings before saving
            rate = self.rateSpinBox.value()
            volume = self.volumeDoubleSpinBox.value()
            voice_index = self.engineComboBox.currentIndex()
            
            # Apply settings
            tts.set_rate(rate)
            tts.set_volume(volume)
            tts.set_voice(voice_index)
            
            self.create_success_info_bar('保存设置成功', '重新导入文件后生效。')
            logger.info(f"Saved TTS settings: rate={rate}, volume={volume}, voice_index={voice_index}")
            
        except TTSError as e:
            self.create_error_info_bar('TTS设置错误', str(e))
        except Exception as e:
            error_msg = handle_exception(e, "保存TTS设置")
            self.create_error_info_bar('保存设置错误', error_msg)

    def get_update(self):
        """获取版本更新"""
        self.versionPrimaryPushButton.setEnabled(False)
        self.update_thread.start()

    def thread_get_update_finish(self, data_list):
        self.versionPrimaryPushButton.setEnabled(True)
        if data_list[0] == 0:
            self.create_success_info_bar(data_list[1], data_list[2])
        elif data_list[0] == 1:
            self.show_update_dialog(data_list[1], data_list[2], data_list[3])
        else:
            self.create_error_info_bar(data_list[1], data_list[2])

    @staticmethod
    def open_github_url():
        """打开GitHub链接"""
        webbrowser.open(GITHUB_URL)

    @staticmethod
    def open_gitee_url():
        """打开Gitee链接"""
        webbrowser.open(GITEE_URL)

    @staticmethod
    def open_update_url(url):
        webbrowser.open(url)

    def create_success_info_bar(self, title: str, text: str) -> None:
        """成功消息框"""
        InfoBar.success(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=INFO_BAR_DURATIONS['update'],
            parent=self
        )

    def create_warning_info_bar(self, title: str, text: str) -> None:
        """警告消息框"""
        InfoBar.warning(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=INFO_BAR_DURATIONS['update'],
            parent=self
        )

    def create_error_info_bar(self, title: str, text: str) -> None:
        """错误消息框"""
        InfoBar.error(
            title=title,
            content=text,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=INFO_BAR_DURATIONS['error'],
            parent=self
        )

    def show_update_dialog(self, title, content, url):
        dialog = MessageBox(title, content, self)
        dialog.yesButton.setText('获取更新')
        dialog.cancelButton.setText('取消')
        if dialog.exec():
            self.open_update_url(url)


class Window(FluentWindow):
    """主窗体"""

    def __init__(self):
        super().__init__()
        logger.info(f"Initializing {APP_NAME} v{VERSION}")
        
        # Set theme and window properties
        setThemeColor(DEFAULT_THEME_COLOR)
        self.resize(*WINDOW_SIZE)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(':/image/image/ppt_ico.svg'))
        
        # Setup splash screen
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(*SPLASH_ICON_SIZE))
        self.show()
        
        # Initialize interfaces
        self.ppt_r = PPTReviewer(self)
        self.setting_interface = SettingInterface(self)
        self.tools_interface = ToolsInterface(self)
        
        # Add interfaces to navigation
        self.addSubInterface(self.ppt_r, FluentIcon.HOME, '主页')
        self.addSubInterface(self.tools_interface, FluentIcon.APPLICATION, '实用工具')
        self.addSubInterface(self.setting_interface, FluentIcon.SETTING, '设置', NavigationItemPosition.BOTTOM)
        
        # Hide splash screen
        self.splashScreen.finish()
        logger.info("Application initialized successfully")

    def click_test(self):
        """测试方法"""
        self.ppt_r.create_success_info_bar('稍安勿躁', '功能还在紧锣密鼓地开发中……')


if __name__ == '__main__':
    # Initialize application
    try:
        # Setup logging for debug mode
        setup_logger(console_output=True, level=logging.DEBUG)
        logger.info(f"Starting {APP_NAME} v{VERSION}")
        
        # Initialize TTS engine
        tts = TTSEngine()
        
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(VERSION)
        
        # Create main window
        w = Window()
        w.show()
        
        # Run application
        sys.exit(app.exec())
        
    except Exception as e:
        error_msg = handle_exception(e, "应用程序启动")
        print(f"Failed to start application: {error_msg}")
        sys.exit(1)
