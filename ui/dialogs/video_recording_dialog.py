"""共用视频导出流程，为各制作方式呈现独立的设置窗口。"""

from pathlib import Path
import logging
import tempfile
import sys

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLayout, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox, DoubleSpinBox,
    IconWidget, IndeterminateProgressBar, InfoBadge, InfoBarIcon, InfoLevel, LineEdit, PrimaryPushButton, ProgressBar,
    PlainTextEdit, PushButton, ScrollArea, SubtitleLabel, ToolButton, ToolTipFilter,
    TransparentPushButton,
    setCustomStyleSheet,
)

from app import paths
from ui import icons, theme
from app.presentation.control import list_presentations
from app.presentation.media import contains_embedded_media
from app.presentation.session import PresentationSession as RecordingSession
from app.video.audio import RecordingAudioPreparation
from app.video.mux import VideoMuxTask, build_video_captions, ensure_mux_available
from app.video.native_media import ensure_native_media_available
from app.video.powerpoint import PowerPointExportTask
from app.video.obs import ObsVideoRecording
from app.video.obs_runtime import check_obs_version, find_obs
from app.video.settings import (
    OBS_QUALITY_PRESETS, RESOLUTIONS, VideoRecordingOptions,
    load_video_options, save_video_options,
)
from app.video.timeline import SUPPORTED_FRAME_RATES, VideoTimeline
from ui.dialogs.base import ThemedDialog
from ui.dialogs.window_target_dialog import WindowTargetDialog


logger = logging.getLogger('pptreviewer.video.export')


_NATIVE_STAGES = {
    'audio_preparation': ('准备配音', '正在准备配音…'),
    'preparing': ('准备文稿', '正在准备 PPT…'),
    'rendering': ('生成画面', '正在生成视频画面…'),
    'picture_save': ('保存画面', '正在保存视频画面…'),
    'narration': ('合成配音', '正在合成讲稿配音…'),
    'joining': ('合成视频', '正在合并画面和配音…'),
    'saving': ('保存视频', '正在保存视频…'),
}


class _CaptureLabel(CaptionLabel):
    """Keep the full window title available while fitting it on one line."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, text)


class VideoRecordingDialog(ThemedDialog):
    """各方式共享任务管理，取消时等待活动任务退出再清理文件。"""

    def __init__(self, notes_list, media_paths, source_path=None, parent=None, ctx=None,
                 initial_mode=None):
        super().__init__(parent)
        self.ctx = ctx
        self.pages = [int(note['page']) for note in notes_list]
        self.notes = [dict(note) for note in notes_list]
        self.sources = [Path(path) for path in media_paths]
        self.source_path = source_path
        saved_mode = self.ctx.app_settings.get('video_export_mode') if self.ctx else 'native'
        if saved_mode not in ('native', 'obs'):
            # 已分发配置可能仍记录旧的录制/合成模式，统一回到默认导出入口。
            saved_options = self.ctx.app_settings.get('video_recording') if self.ctx else None
            saved_mode = ('obs' if saved_mode == 'recording' and isinstance(saved_options, dict)
                          and saved_options.get('recording_backend') == 'obs' else 'native')
        selected_mode = initial_mode or saved_mode
        if selected_mode not in ('native', 'obs'):
            raise ValueError('请选择 PowerPoint 导出或 OBS 录制。')
        settings = self.ctx.app_settings if self.ctx else None
        self._mode_options = {mode: load_video_options(settings, mode) for mode in ('native', 'obs')}
        self.mode = selected_mode
        self.slide_source = (Path(source_path) if source_path and Path(source_path).suffix.lower()
                             in ('.ppt', '.pptx') else None)
        self.prepared_audio = []
        self.preparation = None
        self.recording = None
        self.audio_directory = None
        self.output = None
        self.staging = None
        self.mux_staging = None
        self.muxing = None
        self.native_export = None
        self.error_details = ''
        self.completed = ''
        self._native_stage_name = None
        self.busy = False
        self.closing = False
        self.presentations = []
        self.live_session = None
        self.window_picker_open = False
        self.waiting_manual = False
        self.show_attempts = 0
        self.show_timer = QTimer(self)
        self.show_timer.setInterval(150)
        self.show_timer.timeout.connect(self._await_show)
        self.source_timer = QTimer(self)
        self.source_timer.setInterval(1000)
        self.source_timer.timeout.connect(self._poll_source)
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self._resize_to_contents)
        self.setMinimumWidth(700)
        self.resize(700, 500)
        root = self.create_form_layout(700, (24, 20, 24, 20), 16, fixed_height=False)
        self.heading = SubtitleLabel(self)
        heading_font = self.heading.font()
        heading_font.setPointSize(12)
        heading_font.setWeight(QFont.Weight.DemiBold)
        self.heading.setFont(heading_font)
        row, self.help_icon = theme.make_title_row(
            self.heading,
            '自动播放讲稿配音并推进放映，保留 PPT 动画。\n'
            '仅录制讲稿配音，不包含麦克风、系统声音或 PPT 内嵌媒体的声音。\n'
            '录制期间请保持放映窗口正常显示，勿手动翻页。',
        )
        root.addLayout(row)
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget(self.scroll_area)
        self.content.setObjectName('videoExportContent')
        self.content.setStyleSheet('#videoExportContent { background: transparent; }')
        self.scroll_area.setWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.scroll_area, 1)
        form = CardWidget(self.content)
        form.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(20, 18, 20, 18)
        form_layout.setSpacing(20)
        layout.addWidget(form)

        source_card = QWidget(form)
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(12)
        self.source_title = theme.make_card_title('PPT 文稿', source_card)
        section_font = self.source_title.font()
        section_font.setWeight(QFont.Weight.DemiBold)
        self.source_title.setFont(section_font)
        row, self.source_help_icon = theme.make_title_row(
            self.source_title,
            '使用关联的 PPT，开始录制时自动准备放映。\n'
            '如有编辑，请先保存 PPT，以使用最新内容。\n'
            '可通过“预览画面”确认或更换录制窗口。',
        )
        self.status_badge = InfoBadge.info('等待放映', source_card)
        self.status_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.status_badge.installEventFilter(ToolTipFilter(self.status_badge))
        row.addWidget(self.status_badge)
        source_layout.addLayout(row)
        self.live_source_panel = QWidget(source_card)
        live_layout = QVBoxLayout(self.live_source_panel)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(12)
        row = QHBoxLayout()
        bound_source = self._recording_source()
        self.presentation_file_icon = IconWidget(icons.SystemIcon.DOCUMENT, source_card)
        self.presentation_file_icon.setFixedSize(16, 16)
        self.presentation_file_icon.setVisible(bound_source is not None)
        row.addWidget(self.presentation_file_icon)
        self.presentation_file_label = _CaptureLabel(source_card)
        self.presentation_file_label.setText(bound_source.name if bound_source else '')
        self.presentation_file_label.setToolTip(str(bound_source or ''))
        self.presentation_file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.presentation_file_label.setVisible(bound_source is not None)
        row.addWidget(self.presentation_file_label, 1 if bound_source is not None else 0)
        self.presentation_combo = ComboBox(source_card)
        self.presentation_file_label.setFont(self.presentation_combo.font())
        self.presentation_combo.setMinimumWidth(0)
        self.presentation_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.presentation_combo.setPlaceholderText('选择当前讲稿对应的放映')
        self.presentation_combo.installEventFilter(self)
        self.presentation_combo.setVisible(bound_source is None)
        row.addWidget(self.presentation_combo, 1)
        self.refresh_button = ToolButton(icons.SystemIcon.UPDATE, source_card)
        self.refresh_button.setToolTip('刷新放映状态')
        self.refresh_button.setAccessibleName('刷新放映')
        self.refresh_button.clicked.connect(self.reload)
        row.addWidget(self.refresh_button)
        live_layout.addLayout(row)
        row = QHBoxLayout()
        self.capture_label = _CaptureLabel(source_card)
        self.capture_label.setTextColor('#606060', '#b0b0b0')
        self.capture_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.capture_label, 1)
        self.window_button = PushButton('选择窗口', source_card)
        self.window_button.setMinimumWidth(104)
        self.window_button.setToolTip('预览并选择录制画面，使用上方所选文稿控制翻页和动画。')
        self.window_button.clicked.connect(self.choose_window)
        row.addWidget(self.window_button)
        self.auto_window_button = PushButton('恢复自动', source_card)
        self.auto_window_button.clicked.connect(self.restore_auto_window)
        row.addWidget(self.auto_window_button)
        live_layout.addLayout(row)
        source_layout.addWidget(self.live_source_panel)
        self.file_source_panel = QWidget(source_card)
        row = QHBoxLayout(self.file_source_panel)
        row.setContentsMargins(0, 0, 0, 0)
        self.slide_file_edit = LineEdit(self.file_source_panel)
        self.slide_file_edit.setReadOnly(True)
        self.slide_file_edit.setPlaceholderText('选择与当前讲稿对应的 PPT')
        row.addWidget(self.slide_file_edit, 1)
        self.slide_file_button = PushButton('选择 PPT', self.file_source_panel)
        self.slide_file_button.setMinimumWidth(104)
        self.slide_file_button.clicked.connect(self.choose_slide_file)
        row.addWidget(self.slide_file_button)
        self.file_source_panel.hide()
        source_layout.addWidget(self.file_source_panel)
        self.media_notice = CaptionLabel('视频仅包含讲稿配音，PPT 内音视频的原声不包含在内。', source_card)
        self.media_notice.setWordWrap(True)
        self.media_notice.hide()
        source_layout.addWidget(self.media_notice)
        self._build_obs_card(source_layout)
        form_layout.addWidget(source_card)
        separator = QFrame(form)
        separator.setFixedHeight(1)
        separator.setStyleSheet('background: rgba(128,128,128,0.16); border: none;')
        form_layout.addWidget(separator)

        output_card = QWidget(form)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(12)
        row = QHBoxLayout()
        output_title = theme.make_card_title('输出设置', output_card)
        output_title.setFont(section_font)
        row.addWidget(output_title)
        self.recording_badge = InfoBadge.info('', output_card)
        self.recording_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.recording_badge.installEventFilter(ToolTipFilter(self.recording_badge))
        self.recording_badge.hide()
        row.addWidget(self.recording_badge)
        row.addStretch(1)
        output_layout.addLayout(row)
        self.container_combo = ComboBox(output_card)
        self.container_combo.addItem('MP4', userData='mp4')
        self.container_combo.addItem('MKV', userData='mkv')
        self.subtitle_checkbox = CheckBox('内嵌字幕', output_card)
        self.subtitle_checkbox.setToolTip('字幕与配音同步，可在支持的播放器中开关。')
        self._build_output_settings(output_layout)
        form_layout.addWidget(output_card)

        self.progress_stack = QStackedWidget(self)
        self.progress = ProgressBar(self.progress_stack)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.busy_progress = IndeterminateProgressBar(self.progress_stack, start=False)
        self.progress_stack.addWidget(self.progress)
        self.progress_stack.addWidget(self.busy_progress)
        self.progress_stack.setFixedHeight(self.progress.height())
        self.progress_stack.hide()
        root.addWidget(self.progress_stack)
        self.status_panel = QWidget(self)
        status_layout = QHBoxLayout(self.status_panel)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self.status_icon = IconWidget(InfoBarIcon.INFORMATION, self.status_panel)
        self.status_icon.setFixedSize(16, 16)
        status_layout.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.status = BodyLabel(self.status_panel)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        status_layout.addWidget(self.status, 1)
        self.estimate_caption = CaptionLabel(self.status_panel)
        self.estimate_caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.estimate_caption.setFixedWidth(
            self.estimate_caption.fontMetrics().horizontalAdvance('预计剩余约 999 小时') + 8)
        self.estimate_caption.setToolTip('根据本次画面处理的实际速度动态估算，不包含后续保存和配音处理时间。')
        self.estimate_caption.hide()
        status_layout.addWidget(self.estimate_caption)
        self.progress_caption = CaptionLabel(self.status_panel)
        self.progress_caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_caption.setFixedWidth(self.progress_caption.fontMetrics().horizontalAdvance('100.0%') + 8)
        self.progress_caption.hide()
        status_layout.addWidget(self.progress_caption)
        self.details_button = PushButton('查看详情', self.status_panel)
        self.details_button.clicked.connect(self._show_error_details)
        self.details_button.hide()
        status_layout.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignTop)
        self.status_panel.hide()
        root.addWidget(self.status_panel)
        row = QHBoxLayout()
        self.open_button = PushButton('打开视频', self)
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.completed)))
        row.addWidget(self.open_button)
        self.folder_button = PushButton('打开文件夹', self)
        self.folder_button.hide()
        self.folder_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(Path(self.completed).parent))))
        row.addWidget(self.folder_button)
        row.addStretch(1)
        self.close_button = PushButton('关闭', self)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        self.start_button = PrimaryPushButton('开始录制', self)
        self.start_button.setMinimumWidth(112)
        self.close_button.setMinimumWidth(88)
        self.start_button.clicked.connect(self.start)
        row.addWidget(self.start_button)
        root.addLayout(row)
        self.presentation_combo.currentIndexChanged.connect(self._selection_changed)
        self._change_mode(self.mode)
        self._resize_to_contents()
        self.layout_timer.start(0)

    @property
    def is_live(self):
        return self.mode == 'obs'

    def _update_source_timer(self):
        if self.isVisible() and self.is_live and not self.busy and not self.closing:
            self.source_timer.start()
        else:
            self.source_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.busy:
            self._refresh_obs_choice()
        self._update_source_timer()

    def hideEvent(self, event):
        self.source_timer.stop()
        self.layout_timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and not self.busy
                and hasattr(self, 'obs_panel')):
            self._refresh_obs_choice()

    def _change_mode(self, mode):
        if self.busy:
            return
        if mode not in ('native', 'obs'):
            raise ValueError('请选择 PowerPoint 导出或 OBS 录制。')
        if self.live_session is not None:
            self._release_show()
        if mode != self.mode:
            self._mode_options[self.mode] = self.selected_options()
            self.mode = mode
            self._apply_options(self._mode_options[mode])
        self.mode = mode
        title = {'native': 'PowerPoint 导出', 'obs': 'OBS 录制'}[mode]
        self.heading.setText(title)
        self.setWindowTitle(title)
        self._clear_estimate()
        self.progress_caption.clear()
        self.progress_caption.hide()
        self.error_details = ''
        self.details_button.hide()
        native = mode == 'native'
        self.live_source_panel.setVisible(not native)
        self.file_source_panel.setVisible(native)
        self.show_mode_panel.setVisible(not native)
        self.obs_panel.setVisible(mode == 'obs')
        if mode == 'obs':
            self._refresh_obs_choice()
        self.obs_quality_label.setVisible(not native)
        self.obs_quality_combo.setVisible(not native)
        self._update_quality_controls()
        for index, (label, _width, _height) in enumerate(RESOLUTIONS):
            self.resolution_combo.setItemText(index, label.split('（')[0] if native else label)
        self.resolution_combo.setToolTip(
            '按所选清晰度导出，画面尺寸随 PPT 的页面比例确定。' if native else
            '更高的分辨率画面更清晰，也会增加文件大小，对电脑性能要求更高。')
        self.source_title.setText('PPT 文稿')
        self.start_button.setText('开始导出' if native else '开始录制')
        self.recording_badge.hide()
        self._set_indeterminate_progress(False)
        self.progress.setRange(0, 100)
        self.progress_stack.hide()
        theme.set_help_text(self.help_icon,
            '使用 Microsoft PowerPoint 2016 或更新版本，将 PPT 动画与当前讲稿配音制作为视频。\n'
            '可在后台生成，耗时取决于视频长度、画面复杂度和导出设置。\n'
            '特殊动画可尝试 OBS 录制。\n'
            '仅包含讲稿配音，不包含 PPT 内嵌媒体的声音。' if native else
            '通过 OBS Studio 录制 PowerPoint 或 WPS 的实际放映画面。\n'
            '通常需要与成片时长接近的时间，放映卡顿也会体现在视频中。\n'
            '仅录制讲稿配音，不包含麦克风、系统声音或 PPT 内嵌媒体的声音。\n'
            '录制期间请保持放映窗口正常显示，不要最小化或手动翻页。')
        theme.set_help_text(self.source_help_icon,
            '需要 Microsoft PowerPoint 2016 或更新版本。使用 WPS 演示时，请选择“OBS 录制”。\n'
            '使用已保存的 PPT 文稿，如有编辑，请先保存。' if native else
            '使用关联的 PPT，开始录制时自动准备放映。\n'
            '如有编辑，请先保存 PPT，以使用最新内容。\n'
            '可通过“预览画面”确认或更换录制窗口。')
        if native:
            self._selection_changed()
        else:
            self.reload()
        self._resize_to_contents()
        self.layout_timer.start(0)

        self._update_source_timer()

    def choose_slide_file(self):
        if self.busy:
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self, '选择 PPT 文稿', str(self.slide_source or Path.home()), 'PowerPoint 文稿 (*.pptx *.ppt)')
        if selected:
            self.slide_source = Path(selected)
            self._selection_changed()

    def _set_status(self, message, badge, level=InfoLevel.INFOAMTION, *, detail=True, source=False):
        was_visible = not self.status_panel.isHidden()
        self.status.setText(message)
        self.status_panel.setVisible(detail)
        target = self.status_badge if source else self.recording_badge
        self._update_badge(target, badge, level, message)
        icon = {InfoLevel.SUCCESS: InfoBarIcon.SUCCESS, InfoLevel.WARNING: InfoBarIcon.WARNING,
                InfoLevel.ERROR: InfoBarIcon.ERROR}.get(level, InfoBarIcon.INFORMATION)
        self.status_icon.setIcon(icon)
        self.status_icon.setVisible(level in (InfoLevel.WARNING, InfoLevel.ERROR))
        if was_visible != detail or not self.busy:
            self._resize_to_contents()

    def _update_badge(self, target, text, level, message):
        target.setText(text)
        target.setLevel(level)
        target.show()
        if getattr(target, '_display_level', None) != level:
            light_bg, light_text, dark_bg, dark_text = {
                InfoLevel.SUCCESS: ('#e8f3ea', '#276b38', '#273c2c', '#a6d7ad'),
                InfoLevel.WARNING: ('#fff1d6', '#86590b', '#44381f', '#efcd8e'),
                InfoLevel.ERROR: ('#fbe9e7', '#a52b26', '#472c2c', '#f0aba7'),
            }.get(level, ('#e9e9e9', '#606060', '#353535', '#bcbcbc'))
            target.setCustomBackgroundColor(light_bg, dark_bg)
            setCustomStyleSheet(
                target,
                f'InfoBadge {{ color: {light_text}; padding: 3px 9px; }}',
                f'InfoBadge {{ color: {dark_text}; padding: 3px 9px; }}',
            )
            target._display_level = level
        target.setToolTip(message)
        target.setAccessibleDescription(message)

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'presentation_combo', None) and event.type() == QEvent.Type.Resize:
            self._fit_source_text()
        return super().eventFilter(obj, event)

    def _fit_source_text(self):
        text = self.presentation_combo.currentText() or '请先开始 PPT 放映'
        self.presentation_combo.setToolTip(text)
        fitted = self.presentation_combo.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, self.presentation_combo.width() - 42))
        # ComboBox.setText() adjusts its geometry; only update the button caption here.
        QPushButton.setText(self.presentation_combo, fitted)

    def _build_output_settings(self, layout):
        self.settings_panel = QWidget(self)
        grid = QGridLayout(self.settings_panel)
        self.settings_grid = grid
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        for column in (0, 2):
            grid.setColumnMinimumWidth(column, 64)
        self.resolution_combo = ComboBox(self.settings_panel)
        self.resolution_combo.setToolTip('更高的分辨率画面更清晰，也会增加文件大小，对电脑性能要求更高。')
        for label, width, height in RESOLUTIONS:
            self.resolution_combo.addItem(label, userData=(width, height))
        self.fps_combo = ComboBox(self.settings_panel)
        self.fps_combo.setToolTip('更高的帧率适合流畅呈现动画，也会增加文件大小，对电脑性能要求更高。')
        for fps in SUPPORTED_FRAME_RATES:
            self.fps_combo.addItem(f'{fps} fps', userData=fps)
        self.video_bitrate_spin = DoubleSpinBox(self.settings_panel)
        self.video_bitrate_spin.setRange(1, 100)
        self.video_bitrate_spin.setDecimals(1)
        self.video_bitrate_spin.setSuffix(' Mbps')
        for row, labels, controls in (
                (0, ('分辨率', '帧率'), (self.resolution_combo, self.fps_combo)),
                (3, ('视频码率',), (self.video_bitrate_spin,))):
            for column, (label, control) in enumerate(zip(labels, controls)):
                label_widget = BodyLabel(label, self.settings_panel)
                if control is self.video_bitrate_spin:
                    self.video_bitrate_label = label_widget
                grid.addWidget(label_widget, row, column * 2)
                grid.addWidget(control, row, column * 2 + 1)
                control.setMinimumWidth(160)
                control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(BodyLabel('格式', self.settings_panel), 1, 0)
        grid.addWidget(self.container_combo, 1, 1)
        grid.addWidget(self.subtitle_checkbox, 1, 2, 1, 2)
        self.obs_quality_label = BodyLabel('录制画质', self.settings_panel)
        self.obs_quality_combo = ComboBox(self.settings_panel)
        for label, value, _hint in OBS_QUALITY_PRESETS:
            self.obs_quality_combo.addItem(label, userData=value)
        grid.addWidget(self.obs_quality_label, 2, 0)
        grid.addWidget(self.obs_quality_combo, 2, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.show_mode_panel = QWidget(self.settings_panel)
        show_row = QHBoxLayout(self.show_mode_panel)
        show_row.setContentsMargins(0, 0, 0, 0)
        show_row.addWidget(BodyLabel('放映方式', self.show_mode_panel))
        show_row.addStretch(1)
        self.show_mode_combo = ComboBox(self.show_mode_panel)
        self.show_mode_combo.addItem('窗口放映', userData=True)
        self.show_mode_combo.addItem('全屏放映', userData=False)
        self.show_mode_combo.setToolTip('选择开始录制时的放映方式。已开始的放映沿用当前方式。\n'
                                       'PowerPoint 可选择窗口或全屏放映，WPS 使用全屏放映。')
        show_row.addWidget(self.show_mode_combo)
        grid.addWidget(self.show_mode_panel, 4, 0, 1, 4)
        self.reset_options_button = TransparentPushButton('恢复默认', self.settings_panel)
        self.reset_options_button.setToolTip('恢复当前方式的推荐设置')
        self.reset_options_button.clicked.connect(self._reset_options)
        grid.addWidget(self.reset_options_button, 5, 3, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.settings_panel)
        self._apply_options(self._mode_options[self.mode])
        self.obs_quality_combo.currentIndexChanged.connect(self._update_quality_controls)

    def _build_obs_card(self, layout):
        self.obs_path = ''
        self.obs_available = False
        self.obs_panel = QWidget(self)
        content = QVBoxLayout(self.obs_panel)
        content.setContentsMargins(0, 4, 0, 0)
        content.setSpacing(4)
        row, _help = theme.make_title_row(
            BodyLabel('OBS Studio', self.obs_panel),
            '需要 OBS Studio 28 或更新版本，录制由软件自动开始和结束。')
        self.obs_status_badge = InfoBadge.info('', self.obs_panel)
        row.insertWidget(2, self.obs_status_badge)
        self.obs_download_button = TransparentPushButton('获取 OBS Studio', self.obs_panel)
        self.obs_download_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl('https://obsproject.com/download')))
        row.addWidget(self.obs_download_button)
        self.obs_path_button = TransparentPushButton('选择程序', self.obs_panel)
        self.obs_path_button.setMinimumWidth(104)
        self.obs_path_button.clicked.connect(self._choose_obs)
        row.addWidget(self.obs_path_button)
        content.addLayout(row)
        self.obs_caption = _CaptureLabel(self.obs_panel)
        self.obs_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content.addWidget(self.obs_caption)
        layout.addWidget(self.obs_panel)

    def _apply_options(self, options):
        self.resolution_combo.setCurrentIndex(self.resolution_combo.findData((options.width, options.height)))
        self.fps_combo.setCurrentIndex(self.fps_combo.findData(options.fps))
        self.video_bitrate_spin.setValue(options.video_bit_rate / 1000000)
        self.container_combo.setCurrentIndex(self.container_combo.findData(options.container))
        self.subtitle_checkbox.setChecked(options.embed_subtitles)
        self.show_mode_combo.setCurrentIndex(self.show_mode_combo.findData(options.windowed))
        self.obs_path = options.obs_path
        self.obs_quality_combo.setCurrentIndex(self.obs_quality_combo.findData(options.obs_quality))
        self._update_quality_controls()
        self._refresh_obs_choice()

    def _update_quality_controls(self, *_):
        custom = self.mode == 'obs' and self.obs_quality_combo.currentData() == 'Stream'
        self.video_bitrate_label.setVisible(custom)
        self.video_bitrate_spin.setVisible(custom)
        hint = next((hint for _label, value, hint in OBS_QUALITY_PRESETS
                     if value == self.obs_quality_combo.currentData()), '')
        self.obs_quality_combo.setToolTip(hint)
        self.layout_timer.start(0)

    def _remember_options(self):
        options = self.selected_options()
        self._mode_options[self.mode] = options
        if self.ctx:
            save_video_options(self.ctx.app_settings, self.mode, options)
            self.ctx.app_settings.set('video_export_mode', self.mode)
            self.ctx.config.save_later()

    def _refresh_obs_choice(self):
        if self.mode != 'obs':
            return
        executable = find_obs(self.obs_path)
        version_error = ''
        if executable:
            try:
                check_obs_version(executable)
            except RuntimeError as exc:
                version_error = str(exc)
        self.obs_available = executable is not None and not version_error
        self.obs_problem = version_error or ('OBS 程序位置已失效，请重新选择。' if self.obs_path else
                                             '请先安装 OBS Studio，或选择已安装的程序。')
        label = '需更新' if version_error else '已就绪' if executable else '位置失效' if self.obs_path else '待安装'
        self._update_badge(self.obs_status_badge, label,
                           InfoLevel.SUCCESS if self.obs_available else InfoLevel.WARNING,
                           str(executable) if self.obs_available else self.obs_problem)
        self.obs_download_button.setVisible(not self.obs_available)
        self.obs_caption.setText(str(executable) if self.obs_available else self.obs_problem)
        self.obs_caption.setToolTip(self.obs_caption.text())
        self.obs_caption.setVisible(not self.obs_available)
        self.obs_path_button.setText('更改位置' if executable else '重新选择' if self.obs_path else '选择 OBS')
        self.obs_path_button.setToolTip(version_error or (str(executable) if executable else
            '已安装 OBS Studio 时，可选择其 obs64.exe 程序。尚未安装时，请前往 obsproject.com 下载。'))
        if hasattr(self, 'start_button'):
            self._selection_changed()
            self._resize_to_contents()

    def _choose_obs(self):
        selected, _filter = QFileDialog.getOpenFileName(
            self, '选择 OBS Studio', str(find_obs(self.obs_path) or Path.home()), 'OBS Studio (obs64.exe)')
        if selected:
            if find_obs(selected) is None:
                self._set_status('请选择安装完整的 OBS Studio 程序。', '请选择程序', InfoLevel.WARNING)
                return
            try:
                check_obs_version(selected)
            except RuntimeError as exc:
                self._set_status(str(exc), '请更新 OBS', InfoLevel.WARNING)
                return
            self.obs_path = selected
            self._refresh_obs_choice()

    def _reset_options(self):
        self._apply_options(VideoRecordingOptions(obs_path=self.obs_path))

    def selected_options(self):
        width, height = self.resolution_combo.currentData()
        return VideoRecordingOptions(width, height, fps=self.fps_combo.currentData(),
                                     video_bit_rate=round(self.video_bitrate_spin.value() * 1000000),
                                     container=self.container_combo.currentData(),
                                     embed_subtitles=self.subtitle_checkbox.isChecked(),
                                     windowed=bool(self.show_mode_combo.currentData()),
                                     obs_path=self.obs_path,
                                     obs_quality=self.obs_quality_combo.currentData())


    def _resize_to_contents(self):
        self.ensurePolished()
        if not self.isVisible():
            # 隐藏期间切换模式不会立即向上传递所有布局变化。
            # 首次测量前更新嵌套表单，避免按旧模式的高度打开窗口。
            for child_layout in reversed(self.content.findChildren(QLayout)):
                child_layout.invalidate()
                child_layout.activate()
        self.layout().invalidate()
        self.layout().activate()
        column_layout = self.form_column.layout()
        column_layout.activate()
        content_layout = self.content.layout()
        margins = column_layout.contentsMargins()
        width = self.form_column.width() - margins.left() - margins.right()
        content_layout.invalidate()
        content_layout.activate()
        self.settings_panel.layout().activate()
        field_width = max(160, self.fps_combo.width())
        self.show_mode_combo.setFixedWidth(field_width)
        # 打开时适配内容和屏幕，显示后保持尺寸，通过中间区域滚动展示内容。
        if self.isVisible():
            return
        height = content_layout.totalHeightForWidth(width)
        if height < 0:
            height = content_layout.sizeHint().height()
        # 内容超出屏幕时滚动中间区域，操作按钮始终留在窗口底部。
        extra = margins.top() + margins.bottom() + self.heading.sizeHint().height() + 32 + 48
        if not self.progress_stack.isHidden():
            extra += self.progress_stack.height() + 16
        if not self.status_panel.isHidden():
            status_height = self.status_panel.layout().totalHeightForWidth(width)
            extra += max(self.status_panel.sizeHint().height(), status_height) + 16
        available = self.screen().availableGeometry().height() - 80
        self.setFixedSize(700, min(height + extra, max(320, available)))

    def _poll_source(self):
        if (self.isVisible() and self.is_live and not self.busy
                and not self.closing and not self.window_picker_open):
            self.reload()

    def reload(self):
        if self.busy or self.window_picker_open:
            return
        previous = self._selected_presentation()
        selection = None
        selection_error = ''
        self.presentation_combo.blockSignals(True)
        self.presentation_combo.clear()
        self.presentations = list_presentations(fullscreen_only=False, capture_window=True)
        if self._recording_source() is not None:
            self.presentations = [show for show in self.presentations if show.matches_source(self.source_path)]
        if self.live_session is not None and self.live_session.presentation is not None:
            owned = self.live_session.presentation
            if getattr(owned, 'is_active', lambda: True)():
                self.presentations = [owned] + [show for show in self.presentations
                                               if not show.same_show(owned)]
            else:
                discovered = self.presentations
                self._release_show()
                self.presentations = discovered
        for presentation in self.presentations:
            if previous is not None and getattr(presentation, 'same_show', lambda other: False)(previous):
                selection = presentation
                if getattr(previous, 'capture_mode', 'auto') == 'manual':
                    try:
                        presentation.bind_capture_window(previous.target)
                    except RuntimeError as exc:
                        # A lost manual target must not silently revert to another window.
                        presentation.hwnd = 0
                        presentation.target = dict(previous.target)
                        presentation.capture_mode = 'manual'
                        selection_error = f'原手动窗口已失效：{exc} 请重新选择窗口或恢复自动。'
            provider = getattr(presentation, 'provider', 'PowerPoint')
            self.presentation_combo.addItem(f'{provider} · {presentation.name}（{presentation.slide_count} 页）')
        for index, presentation in enumerate(self.presentations):
            if ((selection is not None and presentation is selection)
                    or (selection is None and presentation.matches_source(self.source_path))):
                self.presentation_combo.setCurrentIndex(index)
                break
        if not self.presentations and self._recording_source() is not None:
            self.presentation_combo.addItem(self._recording_source().name)
        self.presentation_combo.blockSignals(False)
        self._selection_changed()
        if selection_error:
            self._set_status(selection_error, '窗口已失效', InfoLevel.WARNING, source=True)

    def _selected_presentation(self):
        index = self.presentation_combo.currentIndex()
        return self.presentations[index] if 0 <= index < len(self.presentations) else None

    def _recording_source(self):
        source = Path(self.source_path) if self.source_path else None
        return source if source and source.suffix.lower() in ('.ppt', '.pptx') else None

    def _selection_changed(self):
        self.media_notice.setVisible(contains_embedded_media(
            self.slide_source if self.mode == 'native' else self._recording_source()))
        if self.mode == 'native':
            ready = bool(self.slide_source and self.slide_source.is_file()
                         and self.slide_source.suffix.lower() in ('.ppt', '.pptx'))
            self.slide_file_edit.setText(self.slide_source.name if self.slide_source else '')
            self.slide_file_edit.setCursorPosition(0)
            self.slide_file_edit.setToolTip(str(self.slide_source or ''))
            self.start_button.setEnabled(not self.busy and ready and sys.platform == 'win32')
            if not self.busy:
                message = '已选择 PPT，可以开始导出。'
                self._set_status(message if ready else
                                 '请选择与当前讲稿页码对应的 PPT 文稿。',
                                 '已选择' if ready else '待选文件',
                                 InfoLevel.SUCCESS if ready else InfoLevel.INFOAMTION,
                                 detail=False, source=True)
            return
        presentation = self._selected_presentation()
        source = self._recording_source()
        self.presentation_file_icon.setVisible(source is not None)
        self.presentation_file_label.setVisible(source is not None)
        self.presentation_file_label.setText(source.name if source else '')
        self.presentation_file_label.setToolTip(str(source or ''))
        self.presentation_combo.setVisible(source is None)
        ready = presentation is not None and bool(getattr(presentation, 'hwnd', 1))
        if ready:
            ready = getattr(presentation, 'capture_ready', lambda: True)()
        manual = getattr(presentation, 'capture_mode', 'auto') == 'manual'
        self.auto_window_button.setVisible(manual)
        self.auto_window_button.setEnabled(not self.busy and manual)
        target = getattr(presentation, 'target', {})
        title = (target.get('title') or target.get('process') or '无标题窗口') if ready else '尚未识别'
        automatic = source is not None and source.is_file() and presentation is None
        provider = getattr(presentation, 'provider', 'PowerPoint')
        if ready:
            label = f'录制窗口（{"手动" if manual else "自动"}）：{title}'
            message = ('已打开当前 PPT 的放映，可以开始录制。' if source else
                       '请确认所选放映与当前讲稿对应，然后开始录制。')
            badge, level = '放映中', InfoLevel.SUCCESS
        elif presentation is not None:
            label = f'{provider} · 请选择录制画面'
            message = '放映已打开，请预览并选择录制画面。'
            badge, level = '待选画面', InfoLevel.WARNING
        elif automatic:
            label = '开始录制时自动打开并放映'
            message = '录制当前 PPT，程序会自动准备放映。'
            badge, level = '自动放映', InfoLevel.INFOAMTION
        else:
            if source is not None:
                label = '未找到 PPT，请重新关联文稿'
                message = '关联对应 PPT 后即可自动准备放映。'
                badge, level = '未找到 PPT', InfoLevel.WARNING
            else:
                label = '关联 PPT，或选择已打开的放映'
                message = '请选择与当前讲稿对应的 PPT 或放映。'
                badge, level = '未关联 PPT', InfoLevel.INFOAMTION
        self.capture_label.setText(label)
        self.capture_label.setToolTip(f'{provider}\n{title}' if ready else message)
        self._fit_source_text()
        # 自动刷新只更新来源状态，保留导出成功、失败或取消的结果提示。
        self._update_badge(self.status_badge, badge, level, message)
        self.window_button.setText('预览画面' if source is not None else '选择窗口')
        self.window_button.setToolTip('预览并选择录制画面。' if presentation is not None else
                                     '准备当前 PPT 的放映，预览并选择录制画面。')
        tool_ready = self.mode != 'obs' or self.obs_available
        self.start_button.setEnabled(not self.busy and (ready or automatic) and tool_ready and sys.platform == 'win32')
        self.start_button.setToolTip('' if tool_ready else self.obs_problem)
        self.window_button.setEnabled(not self.busy and (presentation is not None or automatic))
        self.presentation_combo.setEnabled(not self.busy and source is None)

    def choose_window(self):
        presentation = self._selected_presentation()
        if self.busy:
            return
        if presentation is None and self._recording_source() is not None:
            self._set_busy(True)
            self._prepare_show('pick')
            return
        if presentation is None:
            return
        dialog = WindowTargetDialog(getattr(presentation, 'target', {}), self,
                                    recording=True, presentation=presentation)
        self.window_picker_open = True
        try:
            if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected:
                return
            presentation.bind_capture_window(dialog.selected)
            self._selection_changed()
        except Exception as exc:
            self._set_status(str(exc), '选窗失败', InfoLevel.ERROR, source=True)
        finally:
            self.window_picker_open = False
            dialog.deleteLater()

    def restore_auto_window(self):
        presentation = self._selected_presentation()
        if self.busy or presentation is None:
            return
        try:
            presentation.restore_auto_window()
            self._selection_changed()
        except Exception as exc:
            self._set_status(f'恢复自动失败：{exc}', '识别失败', InfoLevel.ERROR, source=True)

    def _set_indeterminate_progress(self, active):
        self.progress_stack.setCurrentWidget(self.busy_progress if active else self.progress)
        self.progress_stack.setVisible(self.busy)
        if active:
            self._clear_estimate()
            self.progress_caption.clear()
        if self.busy and active:
            if not self.busy_progress.isStarted():
                self.busy_progress.start()
        else:
            self.busy_progress.stop()

    def _set_busy(self, busy):
        self.busy = busy
        self._native_stage_name = None
        self._clear_estimate()
        self._update_source_timer()
        self.progress_caption.clear()
        self.progress_caption.setVisible(busy and self.mode == 'native')
        self.progress.setRange(0, 100)
        self._set_indeterminate_progress(False)
        self.settings_panel.setEnabled(not busy)
        self.slide_file_button.setEnabled(not busy)
        self.presentation_combo.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.obs_panel.setEnabled(not busy)
        self.container_combo.setEnabled(not busy)
        self.subtitle_checkbox.setEnabled(not busy)
        self._selection_changed()
        self.close_button.setEnabled(True)
        cancel_text = '取消导出' if self.mode == 'native' else '取消录制'
        self.close_button.setText(cancel_text if busy else '关闭')
        if busy:
            self.error_details = ''
            self.details_button.hide()
            self._set_status('正在准备配音…', '准备中')
            if self.mode == 'native':
                self._native_stage('audio_preparation')
        self._resize_to_contents()

    def start(self):
        if self.busy:
            return
        if self.is_live:
            self.reload()
        try:
            self.options = self.selected_options()
            obs = self.mode == 'obs'
            if obs:
                executable = find_obs(self.options.obs_path)
                if executable is None:
                    raise RuntimeError('未找到 OBS Studio，请先安装或重新选择程序位置。')
                check_obs_version(executable)
            ensure_native_media_available()
            if obs and (self.options.container == 'mkv' or self.options.embed_subtitles):
                ensure_mux_available()
            if not self.pages or len(self.pages) != len(self.sources):
                raise RuntimeError('讲稿音频尚未就绪，请先完成语音生成。')
            if self.mode == 'native':
                if not self.slide_source or not self.slide_source.is_file():
                    raise RuntimeError('请选择已保存的 PPT 文稿。')
                name = self.slide_source.name
            else:
                presentation = self._selected_presentation()
                source = self._recording_source()
                if presentation is None and (source is None or not source.is_file()):
                    raise RuntimeError('请先启动 PPT 放映。')
                if (presentation is not None and source is not None
                        and not presentation.matches_source(self.source_path)):
                    raise RuntimeError('所选放映与已导入的 PPT 不同，请刷新后重试。')
                if presentation is not None and max(self.pages) > presentation.slide_count:
                    raise RuntimeError('讲稿页码超出了所选 PPT 的页数，请选择对应的放映。')
                if presentation is not None:
                    presentation._verify_window()
                self.selected_presentation = presentation
                name = source.name if source is not None else presentation.name
            output_dir = Path(self.ctx.app_settings.get('export_dir') or Path.home()) if self.ctx else Path.home()
            selected, _filter = QFileDialog.getSaveFileName(
                self, '保存演示视频',
                str(output_dir / (Path(name).stem + '.' + self.options.container)),
                f'{self.options.container.upper()} 视频 (*.{self.options.container})')
            if not selected:
                return
            self.output = Path(selected)
            if self.ctx:
                self.ctx.app_settings.set('export_dir', str(self.output.parent))
            if self.output.suffix.lower() != '.' + self.options.container:
                self.output = self.output.with_name(self.output.name + '.' + self.options.container)
                if self.output.exists():
                    raise RuntimeError('目标视频已存在，请选择另一个文件名。')
            self.audio_directory = tempfile.TemporaryDirectory(prefix='video-audio-', dir=paths.TEMP_DIR)
            self._remember_options()
            self._set_busy(True)
            self.open_button.setVisible(False)
            self.folder_button.hide()
            self.progress.setValue(0)
            self.preparation = RecordingAudioPreparation(self.sources, self.audio_directory.name, self)
            self.preparation.progress.connect(self._preparation_progress)
            self.preparation.finished.connect(self._record)
            self.preparation.failed.connect(self._failed)
            self.preparation.start()
        except Exception as exc:
            self._failed(str(exc))

    def _preparation_progress(self, index, total):
        if self.mode == 'native':
            self._native_progress(f'正在准备配音：{index + 1} / {total}', index * 100 / total)
            return
        self._set_status(f'正在准备配音：{index + 1} / {total}', '准备中')
        self.progress.setValue(round(index * 100 / total))

    def _record(self, paths):
        if self.mode == 'native':
            self._native_progress('配音已准备', 100)
            self._start_native_export(paths)
            return
        self.prepared_audio = paths
        self._prepare_show('record')

    def _start_native_export(self, paths):
        if self.closing:
            self._cancelled()
            return
        try:
            timeline = VideoTimeline(self.pages, paths, fps=self.options.fps, lead_ms=0, tail_ms=500)
            with tempfile.NamedTemporaryFile(prefix='.ppt-native-', suffix='.' + self.options.container,
                                             dir=self.output.parent, delete=False) as handle:
                self.staging = Path(handle.name)
            self.native_export = PowerPointExportTask(
                self.slide_source, timeline, self.notes, self.staging, self.options, self,
                preference=self.ctx.app_settings.get('presentation_preference') if self.ctx else 'auto')
            self.native_export.stage_changed.connect(self._native_stage)
            self.native_export.progress.connect(self._native_progress)
            self.native_export.estimated_time.connect(self._native_estimate)
            self.native_export.finished.connect(self._native_finished)
            self._native_stage('preparing')
            self.native_export.start()
        except Exception as exc:
            self._failed(str(exc))

    def _native_stage(self, name):
        if not self.busy or self.closing or self.mode != 'native' or name == self._native_stage_name:
            return
        self._native_stage_name = name
        self._clear_estimate()
        self.progress.setRange(0, 10000)
        self.progress.setValue(0)
        self.progress.ani.stop()
        self.progress.setVal(0)
        self.progress_caption.setText('0.0%')
        self._set_indeterminate_progress(name not in ('audio_preparation', 'narration', 'saving'))
        label, message = _NATIVE_STAGES[name]
        self._set_status(message, label)

    def _native_progress(self, message, percent):
        if self.busy and not self.closing:
            if self._native_stage_name != 'rendering':
                self._clear_estimate()
            # Fluent ProgressBar 的 0..0 只绘制底线，等待动画须用专用控件。
            self._set_indeterminate_progress(percent < 0)
            if percent >= 0:
                # 保留 0.01% 的绘制刻度，长文稿不必等到整数百分比才推进。
                percent = max(0., min(100., percent))
                self.progress.setRange(0, 10000)
                self.progress.setValue(round(percent * 100))
                self.progress_caption.setText(f'{percent:.1f}%')
            self._set_status(message, _NATIVE_STAGES.get(self._native_stage_name, ('导出中', ''))[0])

    def _clear_estimate(self):
        self.estimate_caption.clear()
        self.estimate_caption.hide()

    def _native_estimate(self, text):
        if (self.busy and not self.closing and self.mode == 'native'
                and self._native_stage_name == 'rendering'):
            self.estimate_caption.setText(text)
            self.estimate_caption.setVisible(bool(text))

    def _native_finished(self):
        if self.closing or self.native_export.was_cancelled:
            self._cancelled()
        elif self.native_export.error:
            self._failed(self.native_export.error)
        else:
            # 线程已完成画面、配音及字幕封装，直接提交完整文件。
            self._save_completed(self.staging)

    def _prepare_show(self, action):
        if self.closing:
            self._cancelled()
            return
        self.show_action = action
        self.waiting_manual = False
        try:
            self._set_status('正在准备 PPT 放映…', '准备放映')
            if self.live_session is None:
                presentation = self._selected_presentation()
                if presentation is not None:
                    self.live_session = RecordingSession()
                    self.live_session.presentation = presentation
                    self.live_session.remember_position()
                else:
                    self.live_session = RecordingSession.start(
                        self._recording_source(), windowed=bool(self.show_mode_combo.currentData()),
                        preference=self.ctx.app_settings.get('presentation_preference') if self.ctx else 'auto')
            self.selected_presentation = self.live_session.presentation
            self.presentations = [self.selected_presentation]
            self.presentation_combo.blockSignals(True)
            self.presentation_combo.clear()
            self.presentation_combo.addItem(self.selected_presentation.name)
            self.presentation_combo.blockSignals(False)
            self.show_attempts = 0
            self.show_timer.start()
        except Exception as exc:
            self._failed(str(exc))

    def _await_show(self):
        if self.closing:
            self._cancelled()
            return
        try:
            self.live_session.refresh_window()
        except Exception:
            self.show_attempts += 1
            if self.show_attempts < 40:
                return
            # 保留本次放映，便于自动识别超时后手动选择；关闭对话框时再释放。
            self.waiting_manual = True
            self._cleanup()
            button = self.window_button.text()
            self._set_status(f'放映已启动，请点击“{button}”确认录制画面。',
                             '待选画面', InfoLevel.WARNING, source=True)
            return
        self.show_timer.stop()
        self._selection_changed()
        if self.show_action == 'pick':
            self._set_busy(False)
            self.choose_window()
        else:
            self._start_live_recording(self.prepared_audio)

    def _start_live_recording(self, paths):
        try:
            if self.closing:
                self._cancelled()
                return
            if max(self.pages) > self.selected_presentation.slide_count:
                raise RuntimeError('讲稿页码超出了 PPT 的页数。')
            timeline = VideoTimeline(self.pages, paths, fps=self.options.fps)
            self.captions = build_video_captions(self.notes, timeline) if self.options.embed_subtitles else ()
            # 编码器仅写入独占临时文件，完整结束后才替换用户指定的目标。
            with tempfile.NamedTemporaryFile(prefix='.ppt-recording-', suffix='.mp4',
                                             dir=self.output.parent, delete=False) as handle:
                self.staging = Path(handle.name)
            # 构造编码器前先验证放映，音频准备期间用户可能已关闭或最小化窗口。
            self.selected_presentation._verify_window()
            self.recording = ObsVideoRecording(timeline, self.staging, self.selected_presentation,
                                               self, options=self.options)
            self.recording.timeline_ready.connect(self._obs_timeline_ready)
            self.recording.phase.connect(self._obs_phase)
            self.recording.progress.connect(self._progress)
            self.recording.finished.connect(self._finished)
            self.recording.failed.connect(self._failed)
            self.recording.cancelled.connect(self._cancelled)
            self._set_status('正在启动录制…', '录制中')
            self.progress.setValue(0)
            self.recording.start()
        except Exception as exc:
            self._failed(str(exc))

    def _obs_timeline_ready(self, timeline):
        self.captions = build_video_captions(self.notes, timeline) if self.options.embed_subtitles else ()

    def _obs_phase(self, text):
        if self.closing:
            return
        saving = text in ('正在准备视频配音…', '正在保存画面和配音…')
        recording = text == '正在录制放映…'
        self._set_indeterminate_progress(not recording)
        self._set_status(text, '保存中' if saving else '录制中' if recording else '准备中')

    def _progress(self, elapsed, duration, page, segment):
        if self.closing:
            return
        self.progress.setValue(round(elapsed * 100 / duration))
        self._set_status(f'第 {page} 页 · 第 {segment} 段 · '
                         f'{int(elapsed) // 60}:{int(elapsed) % 60:02d} / '
                         f'{int(duration) // 60}:{int(duration) % 60:02d}',
                         '录制中')

    def _finished(self, _path):
        if self.closing:
            self._cancelled()
            return
        self._set_indeterminate_progress(False)
        if self.options.container == 'mkv' or self.options.embed_subtitles:
            try:
                with tempfile.NamedTemporaryFile(prefix='.ppt-mux-', suffix='.' + self.options.container,
                                                 dir=self.output.parent, delete=False) as handle:
                    self.mux_staging = Path(handle.name)
                self.muxing = VideoMuxTask(self.staging, self.mux_staging, self.options.container,
                                          self.captions, self)
                self.muxing.progress.connect(self._mux_progress)
                self.muxing.finished.connect(self._mux_finished)
                self.progress.setValue(0)
                self._set_status('正在保存视频与字幕…' if self.options.embed_subtitles else '正在保存视频…',
                                 '保存中')
                self.close_button.setText('取消导出')
                self.muxing.start()
            except Exception as exc:
                self._mux_failed(str(exc))
            return
        self._save_completed(self.staging)

    def _mux_progress(self, percent):
        if not self.closing:
            self.progress.setValue(percent)
            self._set_status(f'正在保存视频：{percent}%', '保存中')

    def _mux_finished(self):
        if self.closing or self.muxing.was_cancelled:
            self._cancelled()
        elif self.muxing.error:
            self._mux_failed(self.muxing.error)
        else:
            self._save_completed(self.mux_staging)

    def _mux_failed(self, message):
        # 录制已经完整结束；封装失败时保留原始 MP4，避免用户重新录制。
        saved = self.staging
        self.staging = None
        self._failed(f'{message}。原始 MP4 视频保留在：{saved}')

    def _save_completed(self, completed_path):
        try:
            completed_path.replace(self.output)
            if completed_path == self.mux_staging:
                self.mux_staging = None
            else:
                self.staging = None
            self.completed = str(self.output)
            self._cleanup()
            self.progress.setValue(100)
            self.progress_stack.show()
            self._set_status(f'视频已保存：{self.output}', '已保存', InfoLevel.SUCCESS, detail=False)
            self.open_button.setVisible(True)
            self.folder_button.show()
            self.open_button.setToolTip(str(self.output))
            if self.closing:
                super().reject()
        except OSError as exc:
            # 保留完整编码结果供用户取回，不因最终重命名失败丢失视频。
            saved = str(completed_path)
            if completed_path == self.mux_staging:
                self.mux_staging = None
            else:
                self.staging = None
            self._failed(f'无法保存到目标位置：{exc}。完整视频保留在：{saved}')

    def _cleanup(self):
        self.show_timer.stop()
        if not self.waiting_manual:
            self._release_show()
        self._set_busy(False)
        if self.preparation is not None:
            self.preparation.cancel()
            self.preparation.deleteLater()
            self.preparation = None
        if self.recording is not None:
            self.recording.deleteLater()
            self.recording = None
        self.prepared_audio = []
        if self.muxing is not None:
            self.muxing.deleteLater()
            self.muxing = None
        if self.native_export is not None:
            self.native_export.deleteLater()
            self.native_export = None
        if self.audio_directory is not None:
            try:
                self.audio_directory.cleanup()
            except OSError:
                logger.warning('无法清理临时配音目录：%s', self.audio_directory.name, exc_info=True)
            self.audio_directory = None
        if self.staging is not None:
            try:
                self.staging.unlink(missing_ok=True)
            except OSError:
                logger.warning('无法移除视频暂存文件：%s', self.staging, exc_info=True)
            self.staging = None
        if self.mux_staging is not None:
            try:
                self.mux_staging.unlink(missing_ok=True)
            except OSError:
                logger.warning('无法移除封装暂存文件：%s', self.mux_staging, exc_info=True)
            self.mux_staging = None

    def _release_show(self):
        if self.live_session is not None:
            self.live_session.close()
            self.live_session = None
            self.presentations = []
        self.waiting_manual = False

    def _failed(self, message):
        from app.logging_setup import log_error
        log_error(f'视频导出失败（{self.mode}）', message)
        self._cleanup()
        self.error_details = message if '\n' in message or len(message) > 360 else ''
        summary = message.splitlines()[0].rstrip('：:') if self.error_details else message
        if len(summary) > 160:
            summary = summary[:160] + '…'
        self._set_status('视频导出失败：' + summary, '导出失败', InfoLevel.ERROR)
        self.details_button.setVisible(bool(self.error_details))
        if self.closing:
            super().reject()

    def _show_error_details(self):
        if not self.error_details:
            return
        dialog = ThemedDialog(self)
        dialog.setWindowTitle('导出检查结果')
        dialog.resize(680, 440)
        layout = QVBoxLayout(dialog)
        detail = PlainTextEdit(dialog)
        detail.setReadOnly(True)
        detail.setPlainText(self.error_details)
        layout.addWidget(detail)
        close_button = PushButton('关闭', dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
        dialog.deleteLater()

    def _cancelled(self):
        self._cleanup()
        self._set_status('视频导出已取消', '已取消', detail=False)
        if self.closing:
            super().reject()

    def reject(self):
        self._remember_options()
        if self.busy:
            self.closing = True
            self.close_button.setEnabled(False)
            self._set_status('正在取消视频导出…', '取消中')
            if self.native_export is not None:
                self.native_export.cancel()
            elif self.muxing is not None:
                self.muxing.cancel()
            elif self.recording is not None:
                self.recording.cancel()
            else:
                self._cancelled()
            return
        self._release_show()
        self.source_timer.stop()
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.reject()
        else:
            self._release_show()
            self.source_timer.stop()
            super().closeEvent(event)
