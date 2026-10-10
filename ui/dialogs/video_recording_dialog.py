"""通过放映录制或静态页面合成导出 PPT 演示视频。"""

from pathlib import Path
import tempfile
import sys

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox, DoubleSpinBox, FluentIcon,
    IconWidget, InfoBadge, InfoBarIcon, InfoLevel, LineEdit, PrimaryPushButton, ProgressBar,
    PushButton, SegmentedWidget, SpinBox, StrongBodyLabel, SubtitleLabel, ToolButton, ToolTipFilter,
    setCustomStyleSheet,
)

from app import paths, theme
from app.recording_presentation import list_presentations
from app.slide_images import SlideRenderTask
from app.video_composition import StaticVideoComposition
from app.video_mux import VideoMuxTask, build_video_captions, ensure_mux_available
from app.video_recording import RecordingAudioPreparation, VideoRecording, recording_format
from app.video_settings import AUDIO_BIT_RATES, RESOLUTIONS, VideoRecordingOptions
from app.video_timeline import SUPPORTED_FRAME_RATES, VideoTimeline
from ui.dialogs.base import ThemedDialog
from ui.dialogs.window_target_dialog import WindowTargetDialog


class _CaptureLabel(CaptionLabel):
    """Keep the full window title available while fitting it on one line."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, text)


class VideoRecordingDialog(ThemedDialog):
    """模态录制流程：准备音频、录制、保存，取消时等待编码器退出。"""

    def __init__(self, notes_list, media_paths, source_path=None, parent=None, ctx=None,
                 initial_mode=None):
        super().__init__(parent)
        self.ctx = ctx
        self.pages = [int(note['page']) for note in notes_list]
        self.notes = [dict(note) for note in notes_list]
        self.sources = [Path(path) for path in media_paths]
        self.source_path = source_path
        self.mode = 'recording'
        self.slide_source = (Path(source_path) if source_path and Path(source_path).suffix.lower()
                             in ('.ppt', '.pptx') else None)
        self.rendering = None
        self.prepared_audio = []
        self.preparation = None
        self.recording = None
        self.audio_directory = None
        self.output = None
        self.staging = None
        self.mux_staging = None
        self.muxing = None
        self.completed = ''
        self.busy = False
        self.closing = False
        self.presentations = []
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self._resize_to_contents)
        self.setWindowTitle('导出 PPT 视频')
        self.setMinimumWidth(640)
        self.resize(640, 450)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        row, self.help_icon = theme.make_title_row(
            SubtitleLabel('导出 PPT 视频', self),
            '自动播放讲稿配音并推进放映，保留 PPT 动画。\n'
            '仅录制讲稿配音，不包含麦克风、系统声音或 PPT 内嵌媒体的声音。\n'
            '录制期间请保持放映窗口正常显示，勿手动翻页。',
        )
        layout.addLayout(row)
        self.mode_selector = SegmentedWidget(self)
        self.mode_selector.addItem('recording', '放映录制')
        self.mode_selector.addItem('static', '静态合成')
        self.mode_selector.setCurrentItem('recording')
        layout.addWidget(self.mode_selector)

        source_card = CardWidget(self)
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(16, 16, 16, 16)
        source_layout.setSpacing(12)
        self.source_title = StrongBodyLabel('放映来源', source_card)
        row, self.source_help_icon = theme.make_title_row(
            self.source_title,
            '在 PowerPoint 或 WPS 中按 F5 开始全屏放映，再刷新列表。\n'
            '请确认所选文稿与当前讲稿对应。\n'
            '可通过“选择窗口”预览和更换录制画面，翻页与动画仍控制所选文稿。',
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
        self.presentation_combo = ComboBox(source_card)
        self.presentation_combo.setMinimumWidth(0)
        self.presentation_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.presentation_combo.setPlaceholderText('请先按 F5 开始全屏放映')
        self.presentation_combo.installEventFilter(self)
        row.addWidget(self.presentation_combo, 1)
        self.refresh_button = ToolButton(FluentIcon.SYNC, source_card)
        self.refresh_button.setToolTip('刷新放映列表')
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
        self.window_button.setToolTip('预览并选择录制画面；翻页和动画仍由上方所选文稿控制。')
        self.window_button.clicked.connect(self.choose_window)
        row.addWidget(self.window_button)
        self.auto_window_button = PushButton('恢复自动', source_card)
        self.auto_window_button.clicked.connect(self.restore_auto_window)
        row.addWidget(self.auto_window_button)
        live_layout.addLayout(row)
        source_layout.addWidget(self.live_source_panel)
        self.static_source_panel = QWidget(source_card)
        row = QHBoxLayout(self.static_source_panel)
        row.setContentsMargins(0, 0, 0, 0)
        self.slide_file_edit = LineEdit(self.static_source_panel)
        self.slide_file_edit.setReadOnly(True)
        self.slide_file_edit.setPlaceholderText('选择与当前讲稿对应的 PPT')
        row.addWidget(self.slide_file_edit, 1)
        self.slide_file_button = PushButton('选择 PPT', self.static_source_panel)
        self.slide_file_button.clicked.connect(self.choose_slide_file)
        row.addWidget(self.slide_file_button)
        self.static_source_panel.hide()
        source_layout.addWidget(self.static_source_panel)
        layout.addWidget(source_card)

        output_card = CardWidget(self)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(16, 16, 16, 16)
        output_layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(StrongBodyLabel('视频输出', output_card))
        row.addSpacing(4)
        self.output_summary = CaptionLabel(output_card)
        self.output_summary.setTextColor('#606060', '#b0b0b0')
        row.addWidget(self.output_summary)
        self.recording_badge = InfoBadge.info('', output_card)
        self.recording_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.recording_badge.installEventFilter(ToolTipFilter(self.recording_badge))
        self.recording_badge.hide()
        row.addWidget(self.recording_badge)
        row.addStretch(1)
        self.advanced_button = PushButton('高级设置', output_card)
        self.advanced_button.setCheckable(True)
        row.addWidget(self.advanced_button)
        output_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(BodyLabel('格式', output_card))
        self.container_combo = ComboBox(output_card)
        self.container_combo.addItem('MP4', userData='mp4')
        self.container_combo.addItem('MKV', userData='mkv')
        row.addWidget(self.container_combo)
        row.addSpacing(12)
        self.subtitle_checkbox = CheckBox('内嵌字幕', output_card)
        self.subtitle_checkbox.setToolTip('字幕与配音同步，可在支持的播放器中开关。')
        row.addWidget(self.subtitle_checkbox)
        row.addStretch(1)
        output_layout.addLayout(row)
        self._build_advanced(output_layout)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        layout.addWidget(output_card)

        self.progress = ProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
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
        self.status_panel.hide()
        layout.addWidget(self.status_panel)
        layout.addStretch(1)
        row = QHBoxLayout()
        self.open_button = PushButton('打开视频', self)
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.completed)))
        row.addWidget(self.open_button)
        row.addStretch(1)
        self.close_button = PushButton('关闭', self)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        self.start_button = PrimaryPushButton('开始录制', self)
        self.start_button.clicked.connect(self.start)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.presentation_combo.currentIndexChanged.connect(self._selection_changed)
        self.mode_selector.currentItemChanged.connect(self._change_mode)
        saved_mode = self.ctx.app_settings.get('video_export_mode') if self.ctx else 'recording'
        selected_mode = initial_mode if initial_mode in ('recording', 'static') else saved_mode
        self.mode_selector.setCurrentItem('static' if selected_mode == 'static' else 'recording')
        if self.mode == 'recording':
            self.reload()
        self._resize_to_contents()
        self.layout_timer.start(0)

    def _change_mode(self, mode):
        if self.busy:
            return
        self.mode = mode
        static = mode == 'static'
        self.live_source_panel.setVisible(not static)
        self.static_source_panel.setVisible(static)
        self.wait_panel.setVisible(not static)
        self.source_title.setText('PPT 文件' if static else '放映来源')
        self.start_button.setText('开始合成' if static else '开始录制')
        self.recording_badge.hide()
        self.progress.hide()
        theme.set_help_text(self.help_icon,
            '将 PPT 的逐页静态画面与讲稿配音制作为视频。\n'
            '同页配音依次播放，按讲稿页码切换画面。' if static else
            '自动播放讲稿配音并推进放映，保留 PPT 动画。\n'
            '仅录制讲稿配音，不包含麦克风、系统声音或 PPT 内嵌媒体的声音。\n'
            '录制期间请保持放映窗口正常显示，勿手动翻页。')
        theme.set_help_text(self.source_help_icon,
            '请安装 PowerPoint 或 WPS 演示。\n'
            '默认使用导入讲稿时的 PPT，也可选择与讲稿页码对应的其他 PPT。\n'
            '如有编辑，请先保存 PPT，以使用最新内容。' if static else
            '在 PowerPoint 或 WPS 中按 F5 开始全屏放映，再刷新列表。\n'
            '请确认所选文稿与当前讲稿对应。\n'
            '可通过“选择窗口”预览和更换录制画面，翻页与动画仍控制所选文稿。')
        if static:
            self._selection_changed()
        else:
            self.reload()
        self._resize_to_contents()
        self.layout_timer.start(0)

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
        text = self.presentation_combo.currentText() or '请先按 F5 开始全屏放映'
        self.presentation_combo.setToolTip(text)
        fitted = self.presentation_combo.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, self.presentation_combo.width() - 42))
        # ComboBox.setText() adjusts its geometry; only update the button caption here.
        QPushButton.setText(self.presentation_combo, fitted)

    def _build_advanced(self, layout):
        self.advanced_panel = QWidget(self)
        grid = QGridLayout(self.advanced_panel)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.resolution_combo = ComboBox(self.advanced_panel)
        self.resolution_combo.setToolTip('更高的分辨率画面更清晰，也会增加文件大小，对电脑性能要求更高。')
        for label, width, height in RESOLUTIONS:
            self.resolution_combo.addItem(label, userData=(width, height))
        self.fps_combo = ComboBox(self.advanced_panel)
        self.fps_combo.setToolTip('更高的帧率适合流畅呈现动画，也会增加文件大小，对电脑性能要求更高。')
        for fps in SUPPORTED_FRAME_RATES:
            self.fps_combo.addItem(f'{fps} fps', userData=fps)
        self.video_bitrate_spin = DoubleSpinBox(self.advanced_panel)
        self.video_bitrate_spin.setRange(1, 100)
        self.video_bitrate_spin.setDecimals(1)
        self.video_bitrate_spin.setSuffix(' Mbps')
        self.audio_bitrate_combo = ComboBox(self.advanced_panel)
        for bitrate in AUDIO_BIT_RATES:
            self.audio_bitrate_combo.addItem(f'{bitrate // 1000} kbps', userData=bitrate)
        for row, labels, controls in (
                (0, ('分辨率', '帧率'), (self.resolution_combo, self.fps_combo)),
                (1, ('视频码率', '音频码率'), (self.video_bitrate_spin, self.audio_bitrate_combo))):
            for column, (label, control) in enumerate(zip(labels, controls)):
                grid.addWidget(BodyLabel(label, self.advanced_panel), row, column * 2)
                grid.addWidget(control, row, column * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.wait_panel = QWidget(self.advanced_panel)
        wait_row = QHBoxLayout(self.wait_panel)
        wait_row.setContentsMargins(0, 0, 0, 0)
        self.wait_checkbox = CheckBox('换页或点击后额外等待', self.advanced_panel)
        self.wait_checkbox.setToolTip('如需让动画播放完再继续配音，可开启并调整等待时间。')
        wait_row.addWidget(self.wait_checkbox)
        self.wait_spin = SpinBox(self.advanced_panel)
        self.wait_spin.setRange(0, 10000)
        self.wait_spin.setSingleStep(100)
        self.wait_spin.setValue(700)
        self.wait_spin.setSuffix(' ms')
        self.wait_spin.setEnabled(False)
        self.wait_checkbox.toggled.connect(
            lambda checked: self.wait_spin.setEnabled(checked and not self.busy))
        wait_row.addStretch(1)
        wait_row.addWidget(self.wait_spin)
        grid.addWidget(self.wait_panel, 2, 0, 1, 4)
        self.reset_options_button = PushButton('恢复默认', self.advanced_panel)
        self.reset_options_button.clicked.connect(self._reset_options)
        grid.addWidget(self.reset_options_button, 3, 3, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        saved = self.ctx.app_settings.get('video_recording') if self.ctx else None
        self._apply_options(VideoRecordingOptions.from_dict(saved))
        self.resolution_combo.currentIndexChanged.connect(self._update_output_summary)
        self.fps_combo.currentIndexChanged.connect(self._update_output_summary)

    def _apply_options(self, options):
        self.resolution_combo.setCurrentIndex(self.resolution_combo.findData((options.width, options.height)))
        self.fps_combo.setCurrentIndex(self.fps_combo.findData(options.fps))
        self.video_bitrate_spin.setValue(options.video_bit_rate / 1000000)
        self.audio_bitrate_combo.setCurrentIndex(self.audio_bitrate_combo.findData(options.audio_bit_rate))
        self.container_combo.setCurrentIndex(self.container_combo.findData(options.container))
        self.subtitle_checkbox.setChecked(options.embed_subtitles)
        self._update_output_summary()

    def _update_output_summary(self):
        resolution = self.resolution_combo.currentText().split('（')[0]
        self.output_summary.setText(f'{resolution} · {self.fps_combo.currentData()} fps')

    def _reset_options(self):
        self._apply_options(VideoRecordingOptions())
        self.wait_checkbox.setChecked(False)
        self.wait_spin.setValue(700)

    def selected_options(self):
        width, height = self.resolution_combo.currentData()
        return VideoRecordingOptions(width, height, self.fps_combo.currentData(),
                                     round(self.video_bitrate_spin.value() * 1000000),
                                     self.audio_bitrate_combo.currentData(),
                                     self.container_combo.currentData(), self.subtitle_checkbox.isChecked())

    def _toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_button.setText('收起高级设置' if expanded else '高级设置')
        self.output_summary.setVisible(not expanded)
        self._resize_to_contents()
        self.layout_timer.start(0)

    def _resize_to_contents(self):
        self.layout().invalidate()
        self.layout().activate()
        # Calculate wrapped captions at the actual width instead of QLabel's narrow size hint.
        height = self.layout().totalHeightForWidth(self.width())
        self.resize(self.width(), height if height > 0 else self.layout().sizeHint().height())

    def reload(self):
        previous = self._selected_presentation()
        selection = None
        selection_error = ''
        self.presentation_combo.blockSignals(True)
        self.presentation_combo.clear()
        self.presentations = list_presentations()
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
        self.presentation_combo.blockSignals(False)
        self._selection_changed()
        if selection_error:
            self._set_status(selection_error, '窗口已失效', InfoLevel.WARNING, source=True)

    def _selected_presentation(self):
        index = self.presentation_combo.currentIndex()
        return self.presentations[index] if 0 <= index < len(self.presentations) else None

    def _selection_changed(self):
        if self.mode == 'static':
            ready = bool(self.slide_source and self.slide_source.is_file()
                         and self.slide_source.suffix.lower() in ('.ppt', '.pptx'))
            self.slide_file_edit.setText(self.slide_source.name if self.slide_source else '')
            self.slide_file_edit.setCursorPosition(0)
            self.slide_file_edit.setToolTip(str(self.slide_source or ''))
            self.start_button.setEnabled(not self.busy and ready and sys.platform == 'win32')
            if not self.busy:
                self._set_status('已选择 PPT，确认与当前讲稿对应后开始合成。' if ready else
                                 '请选择与当前讲稿页码对应的 PPT 文稿。',
                                 '已选择' if ready else '待选文件',
                                 InfoLevel.SUCCESS if ready else InfoLevel.INFOAMTION,
                                 detail=False, source=True)
            return
        presentation = self._selected_presentation()
        ready = presentation is not None and bool(getattr(presentation, 'hwnd', 1))
        manual = getattr(presentation, 'capture_mode', 'auto') == 'manual'
        self.auto_window_button.setVisible(manual)
        self.auto_window_button.setEnabled(not self.busy and manual)
        target = getattr(presentation, 'target', {})
        title = (target.get('title') or target.get('process') or '无标题窗口') if ready else '尚未识别'
        self.capture_label.setText(f'录制窗口（{"手动" if manual else "自动"}）：{title}'
                                   if presentation is not None else '录制窗口：尚未选择放映')
        self.capture_label.setToolTip(self.capture_label.text())
        self._fit_source_text()
        self.start_button.setEnabled(not self.busy and ready and sys.platform == 'win32')
        self.window_button.setEnabled(not self.busy and presentation is not None)
        if self.busy:
            return
        if presentation is None:
            self._set_status('请在 PowerPoint 或 WPS 中按 F5 开始全屏放映，再刷新列表。',
                             '等待放映', detail=False, source=True)
        elif not ready:
            self._set_status('已连接放映，请点击“选择窗口”指定录制画面。',
                             '待选窗口', InfoLevel.WARNING, detail=False, source=True)
        else:
            self._set_status('请确认所选放映与当前讲稿对应，然后开始录制。',
                             '已连接', InfoLevel.SUCCESS, detail=False, source=True)

    def choose_window(self):
        presentation = self._selected_presentation()
        if self.busy or presentation is None:
            return
        dialog = WindowTargetDialog(getattr(presentation, 'target', {}), self,
                                    recording=True, presentation=presentation)
        try:
            if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected:
                return
            presentation.bind_capture_window(dialog.selected)
            self._selection_changed()
            self._set_status('已选择录制画面，翻页和动画仍控制当前文稿。',
                             '已连接', InfoLevel.SUCCESS, detail=False, source=True)
        except Exception as exc:
            self._set_status(str(exc), '选窗失败', InfoLevel.ERROR, source=True)
        finally:
            dialog.deleteLater()

    def restore_auto_window(self):
        presentation = self._selected_presentation()
        if self.busy or presentation is None:
            return
        try:
            presentation.restore_auto_window()
            self._selection_changed()
            self._set_status('已恢复自动识别的放映窗口。', '已连接', InfoLevel.SUCCESS,
                             detail=False, source=True)
        except Exception as exc:
            self._set_status(f'恢复自动失败：{exc}', '识别失败', InfoLevel.ERROR, source=True)

    def _set_busy(self, busy):
        self.busy = busy
        self.progress.setVisible(busy)
        self.mode_selector.setEnabled(not busy)
        self.slide_file_button.setEnabled(not busy)
        self.presentation_combo.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.wait_checkbox.setEnabled(not busy)
        self.wait_spin.setEnabled(not busy and self.wait_checkbox.isChecked())
        self.advanced_button.setEnabled(not busy)
        self.advanced_panel.setEnabled(not busy)
        self.container_combo.setEnabled(not busy)
        self.subtitle_checkbox.setEnabled(not busy)
        self._selection_changed()
        self.close_button.setEnabled(True)
        self.close_button.setText(('取消合成' if self.mode == 'static' else '取消录制') if busy else '关闭')
        if busy:
            self._set_status('正在准备配音…', '准备中')
        self._resize_to_contents()

    def start(self):
        if self.busy:
            return
        try:
            recording_format()
            self.options = self.selected_options()
            if self.options.container == 'mkv' or self.options.embed_subtitles:
                ensure_mux_available()
            if not self.pages or len(self.pages) != len(self.sources):
                raise RuntimeError('讲稿音频尚未就绪，请先完成语音生成。')
            if self.mode == 'static':
                if not self.slide_source or not self.slide_source.is_file():
                    raise RuntimeError('请选择已保存的 PPT 文稿。')
                name = self.slide_source.name
            else:
                presentation = self._selected_presentation()
                if presentation is None:
                    raise RuntimeError('请先启动 PPT 全屏放映。')
                if (self.source_path and Path(self.source_path).suffix.lower() == '.pptx'
                        and not presentation.matches_source(self.source_path)):
                    raise RuntimeError('所选 PPT 与当前导入的文稿不同，请打开对应文稿并开始放映。')
                if max(self.pages) > presentation.slide_count:
                    raise RuntimeError('讲稿页码超出了所选 PPT 的页数，请选择对应的放映。')
                self.capture_window = presentation.capturable_window()
                self.selected_presentation = presentation
                name = presentation.name
            selected, _filter = QFileDialog.getSaveFileName(
                self, '保存演示视频',
                str(Path.home() / (Path(name).stem + '.' + self.options.container)),
                f'{self.options.container.upper()} 视频 (*.{self.options.container})')
            if not selected:
                return
            self.output = Path(selected)
            if self.output.suffix.lower() != '.' + self.options.container:
                self.output = self.output.with_name(self.output.name + '.' + self.options.container)
                if self.output.exists():
                    raise RuntimeError('目标视频已存在，请选择另一个文件名。')
            self.audio_directory = tempfile.TemporaryDirectory(prefix='video-audio-', dir=paths.TEMP_DIR)
            if self.ctx:
                self.ctx.app_settings.set('video_recording', self.options.to_dict())
                self.ctx.app_settings.set('video_export_mode', self.mode)
                self.ctx.config.save_later()
            self._set_busy(True)
            self.open_button.setVisible(False)
            self.progress.setValue(0)
            self.preparation = RecordingAudioPreparation(self.sources, self.audio_directory.name, self)
            self.preparation.progress.connect(self._preparation_progress)
            self.preparation.finished.connect(self._record)
            self.preparation.failed.connect(self._failed)
            self.preparation.start()
        except Exception as exc:
            self._failed(str(exc))

    def _preparation_progress(self, index, total):
        self._set_status(f'正在准备配音：{index + 1} / {total}', '准备中')
        self.progress.setValue(round(index * 100 / total))

    def _record(self, paths):
        if self.mode == 'static':
            if self.closing:
                self._cancelled()
                return
            try:
                self.prepared_audio = paths
                self.rendering = SlideRenderTask(self.slide_source, self.pages,
                                                Path(self.audio_directory.name) / 'slides',
                                                self.options.width, self.options.height, self)
                self.rendering.progress.connect(self._render_progress)
                self.rendering.finished.connect(self._slides_ready)
                self._set_status('正在读取 PPT 画面…', '准备画面')
                self.progress.setValue(0)
                self.rendering.start()
            except Exception as exc:
                self._failed(str(exc))
            return
        try:
            wait_ms = self.wait_spin.value() if self.wait_checkbox.isChecked() else 0
            timeline = VideoTimeline(self.pages, paths, wait_ms, fps=self.options.fps)
            self.captions = build_video_captions(self.notes, timeline) if self.options.embed_subtitles else ()
            # 编码器仅写入独占临时文件，完整结束后才替换用户指定的目标。
            with tempfile.NamedTemporaryFile(prefix='.ppt-recording-', suffix='.mp4',
                                             dir=self.output.parent, delete=False) as handle:
                self.staging = Path(handle.name)
            # 构造编码器前先验证放映，音频准备期间用户可能已关闭或最小化窗口。
            self.capture_window = self.selected_presentation.capturable_window()
            self.recording = VideoRecording(timeline, self.staging, self.selected_presentation,
                                             self.capture_window, self, options=self.options)
            self.recording.progress.connect(self._progress)
            self.recording.finished.connect(self._finished)
            self.recording.failed.connect(self._failed)
            self.recording.cancelled.connect(self._cancelled)
            self._set_status('正在启动录制…', '录制中')
            self.progress.setValue(0)
            self.recording.start()
        except Exception as exc:
            self._failed(str(exc))

    def _render_progress(self, index, total):
        if not self.closing:
            self._set_status(f'正在准备 PPT 画面：{index} / {total}', '准备画面')
            self.progress.setValue(round(index * 100 / total))

    def _slides_ready(self):
        if self.closing or self.rendering.was_cancelled:
            self._cancelled()
            return
        if self.rendering.error:
            self._failed(self.rendering.error)
            return
        try:
            timeline = VideoTimeline(self.pages, self.prepared_audio, fps=self.options.fps,
                                     lead_ms=0, tail_ms=0)
            self.captions = build_video_captions(self.notes, timeline) if self.options.embed_subtitles else ()
            with tempfile.NamedTemporaryFile(prefix='.ppt-composition-', suffix='.mp4',
                                             dir=self.output.parent, delete=False) as handle:
                self.staging = Path(handle.name)
            self.recording = StaticVideoComposition(timeline, self.staging, self.rendering.images,
                                                     self, options=self.options)
            self.recording.progress.connect(self._progress)
            self.recording.finished.connect(self._finished)
            self.recording.failed.connect(self._failed)
            self.recording.cancelled.connect(self._cancelled)
            self._set_status('正在合成视频…', '合成中')
            self.progress.setValue(0)
            self.recording.start()
        except Exception as exc:
            self._failed(str(exc))

    def _progress(self, elapsed, duration, page, segment):
        self.progress.setValue(round(elapsed * 100 / duration))
        self._set_status(f'第 {page} 页 · 第 {segment} 段 · '
                         f'{int(elapsed) // 60}:{int(elapsed) % 60:02d} / '
                         f'{int(duration) // 60}:{int(duration) % 60:02d}',
                         '合成中' if self.mode == 'static' else '录制中')

    def _finished(self, _path):
        if self.closing:
            self._cancelled()
            return
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
            self.progress.show()
            self._set_status(f'视频已保存：{self.output}', '已保存', InfoLevel.SUCCESS, detail=False)
            self.open_button.setVisible(True)
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
        self._set_busy(False)
        if self.preparation is not None:
            self.preparation.cancel()
            self.preparation.deleteLater()
            self.preparation = None
        if self.recording is not None:
            self.recording.deleteLater()
            self.recording = None
        if self.rendering is not None:
            self.rendering.deleteLater()
            self.rendering = None
        self.prepared_audio = []
        if self.muxing is not None:
            self.muxing.deleteLater()
            self.muxing = None
        if self.audio_directory is not None:
            try:
                self.audio_directory.cleanup()
            except OSError as exc:
                print(f'[录制] 临时配音清理失败：{exc}')
            self.audio_directory = None
        if self.staging is not None:
            try:
                self.staging.unlink(missing_ok=True)
            except OSError:
                pass
            self.staging = None
        if self.mux_staging is not None:
            try:
                self.mux_staging.unlink(missing_ok=True)
            except OSError:
                pass
            self.mux_staging = None

    def _failed(self, message):
        self._cleanup()
        self._set_status('视频导出失败：' + message, '导出失败', InfoLevel.ERROR)
        if self.closing:
            super().reject()

    def _cancelled(self):
        self._cleanup()
        self._set_status('视频导出已取消', '已取消', detail=False)
        if self.closing:
            super().reject()

    def reject(self):
        if self.busy:
            self.closing = True
            self.close_button.setEnabled(False)
            self._set_status('正在取消视频导出…', '取消中')
            if self.muxing is not None:
                self.muxing.cancel()
            elif self.recording is not None:
                self.recording.cancel()
            elif self.rendering is not None:
                # finished may already be queued even when isRunning() is false.
                self.rendering.cancel()
            else:
                self._cancelled()
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
