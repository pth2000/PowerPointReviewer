"""入门与进阶引导：在真实控件旁展示可跳过的动画提示。"""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, PrimaryPushButton, SubtitleLabel,
    TransparentPushButton, isDarkTheme, themeColor,
)

from ui.widget_state import active_modal_dialog


class GuideCard(CardWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self.progress = CaptionLabel(self)
        self.title = SubtitleLabel(self)
        self.title.setWordWrap(True)
        self.body = BodyLabel(self)
        self.body.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.back_button = TransparentPushButton('上一步', self)
        self.skip_button = TransparentPushButton('跳过', self)
        self.next_button = PrimaryPushButton(self)
        self.next_button.setMinimumWidth(112)
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.skip_button)
        actions.addWidget(self.next_button)
        layout.addLayout(actions)
        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        painter.setBrush(QColor('#292929' if dark else '#FFFFFF'))
        painter.setPen(QPen(QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 24), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)


class GuideOverlay(QWidget):
    """绘制镂空遮罩；说明卡是同级控件，底下的页面始终可操作。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.card = GuideCard(parent)
        self.hole = QRectF()
        self._old_hole = QRectF()
        self._target_hole = QRectF()
        self._card_rect = QRect()
        self._dim = 0.0
        self.extra_targets = ()
        self._fade_from = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(220)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.hide()

    def present(self, target, title, body, action, *, progress='', back=False, skip='跳过', extras=()):
        self.extra_targets = extras
        self.card.progress.setText(progress)
        self.card.progress.setVisible(bool(progress))
        self.card.title.setText(title)
        self.card.body.setText(body)
        self.card.next_button.setText(action)
        self.card.back_button.setVisible(back)
        self.card.skip_button.setText(skip)
        self._fade_from = self._dim if self.isVisible() else 0.0
        self._old_hole = QRectF(self.hole)
        self.reposition(target)
        if self._old_hole.isEmpty() or self._target_hole.isEmpty():
            self._old_hole = QRectF(self._target_hole)
        self.show()
        self.raise_()
        self.card.show()
        self.card.raise_()
        self.animation.stop()
        self.animation.start()

    def reposition(self, target):
        window = self.parentWidget()
        top = window.titleBar.height() if hasattr(window, 'titleBar') else 0
        self.setGeometry(0, top, window.width(), max(0, window.height() - top))
        area = self.geometry().adjusted(20, 18, -20, -18)
        hole = QRectF()
        for widget in (target, *self.extra_targets):
            if widget is None or not widget.isVisible():
                continue
            rect = QRect(widget.mapTo(window, QPoint()), widget.size())
            # 控件可能只露出一部分：高亮只绘制实际可见的区域。
            ancestor = widget.parentWidget()
            while ancestor is not None and ancestor is not window:
                rect = rect.intersected(QRect(ancestor.mapTo(window, QPoint()), ancestor.size()))
                ancestor = ancestor.parentWidget()
            if not rect.isEmpty():
                hole = hole.united(QRectF(rect.adjusted(-5, -5, 5, 5).intersected(self.geometry())))
        self._target_hole = hole.translated(0, -top)
        self.card.setFixedWidth(min(390, max(280, area.width())))
        self.card.adjustSize()
        size = self.card.size()
        x = area.center().x() - size.width() // 2
        y = area.center().y() - size.height() // 2
        if not hole.isEmpty():
            if hole.bottom() + 16 + size.height() <= area.bottom():
                y = int(hole.bottom()) + 16
            elif hole.top() - 16 - size.height() >= area.top():
                y = int(hole.top()) - 16 - size.height()
            else:
                y = area.bottom() - size.height()
            x = int(hole.right()) - size.width()
        x = max(area.left(), min(x, area.right() - size.width()))
        y = max(area.top(), min(y, area.bottom() - size.height()))
        self._card_rect = QRect(QPoint(x, y), size)
        if self.animation.state() != QVariantAnimation.State.Running:
            self.hole = QRectF(self._target_hole)
            self.card.setGeometry(self._card_rect)
        self.update()

    def _animate(self, value):
        value = float(value)
        self._dim = self._fade_from + (1.0 - self._fade_from) * value
        a, b = self._old_hole, self._target_hole
        self.hole = QRectF(a.x() + (b.x() - a.x()) * value,
                           a.y() + (b.y() - a.y()) * value,
                           a.width() + (b.width() - a.width()) * value,
                           a.height() + (b.height() - a.height()) * value)
        self.card.opacity.setOpacity(value)
        self.card.setGeometry(self._card_rect.translated(0, round(8 * (1 - value))))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        if not self.hole.isEmpty():
            path.addRoundedRect(self.hole, 10, 10)
        painter.fillPath(path, QColor(0, 0, 0, round((115 if isDarkTheme() else 72) * self._dim)))
        if not self.hole.isEmpty():
            accent = QColor(themeColor())
            accent.setAlpha(round(210 * self._dim))
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.hole, 10, 10)
        card_rect = QRectF(self.card.geometry().translated(0, -self.y()))
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(8, 0, -1):
            painter.setBrush(QColor(0, 0, 0, round(3 * self._dim)))
            painter.drawRoundedRect(card_rect.adjusted(-spread, 3 - spread, spread, 3 + spread), 14, 14)

    def dismiss(self):
        self.animation.stop()
        self.hide()
        self.card.hide()


class UsageGuide(QObject):
    """由业务信号推进引导，滚动、弹窗和布局变化通过事件更新。"""

    ADVANCED_STEPS = (
        'update_script', 'script_options', 'slideshow_sync', 'hotkeys', 'audio_review',
        'ai_rewrite', 'script_roundtrip', 'audio_export', 'video_export', 'project_package',
    )

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window.context.app_settings
        self.overlay = GuideOverlay(window)
        self.step = None
        self.target = None
        self._queued = False
        self._waiting_dialog = False
        self._generated = False
        self._dragging = False
        self.settings_button = window.navigationInterface.widget(window.setting_interface.objectName())
        self.settings_button.clicked.connect(self._settings_clicked)
        self.overlay.card.next_button.clicked.connect(self.next)
        self.overlay.card.back_button.clicked.connect(self.back)
        self.overlay.card.skip_button.clicked.connect(self.skip)
        window.setting_interface.usage_guide_requested.connect(self.start)
        window.setting_interface.advanced_guide_requested.connect(self.start_advanced)
        task = window.ppt_r.save_thread
        task.signal_finish.connect(self._on_generated)
        task.started.connect(self._refresh)
        task.finished.connect(self._after_generation)
        window.ppt_r.audio_changed.connect(self._on_audio_changed)
        QApplication.instance().installEventFilter(self)

    def _save(self, key):
        if not self.settings.get(key):
            self.settings.set(key, True)
            self.window.context.config.save_later()

    def maybe_offer(self):
        if self.settings.get('onboarding_seen') or self.step is not None:
            return
        # 已有历史的用户仍从原来的恢复流程进入，可随时在设置中查看引导。
        if self.settings.get('last_session') or self.window.ppt_r.notes:
            self._save('onboarding_seen')
            return
        if self.window.ppt_r.is_busy() or not self.window.isVisible():
            return
        self.step = 'welcome'
        self._present()

    def start(self):
        self._start('welcome')

    def start_advanced(self):
        self._start(self.ADVANCED_STEPS[0])

    def _start(self, step):
        if self.window.ppt_r.is_busy():
            self.window.ppt_r.create_warning_info_bar('请稍后查看', '当前任务完成后即可开始使用指引。')
            return
        self._go_to(step)

    def _go_to(self, step):
        previous = self.step
        self.step = step
        if self._present() is False:
            self.step = previous
            self._present()

    def _settings_clicked(self, *_):
        if self.step == 'settings' and not self.window.ppt_r.is_busy():
            self.step = 'engine'
            self._present()

    def _present(self):
        if self.step is None or not self.window.isVisible():
            return
        dialog = active_modal_dialog(self.window)
        if dialog is not None:
            self.overlay.dismiss()
            if not self._waiting_dialog:
                self._waiting_dialog = True
                dialog.finished.connect(self._after_dialog, Qt.ConnectionType.SingleShotConnection)
            return
        page, settings = self.window.ppt_r, self.window.setting_interface
        steps = {
            'welcome': (page, None, '欢迎使用 PowerPointReviewer',
                        '将 PPT 备注转为配音，边听边完善讲稿。用几个简单步骤，了解声音设置、讲稿分段、导入和播放。',
                        '开始引导', '', False, '稍后'),
            'settings': (page, self.settings_button, '先打开设置',
                         '点击左侧导航栏底部的设置图标，选择声音并试听。以后也可以从这里调整声音和播放偏好。',
                         '打开设置', '开始使用 · 1 / 6', False, '跳过'),
            'engine': (settings, settings.CardWidget_5, '选择语音引擎',
                       '选择语音引擎，再在下方选择发音人、调整语速。第一次使用可以先保留默认设置。',
                       '下一步', '开始使用 · 2 / 6', True, '跳过'),
            'preview': (settings, getattr(settings, '_preview_text_card', settings.CardWidget_4), '听听配音效果',
                        '点击“试听”听一段示例。若调整了声音，记得保存设置，再导入你的讲稿。',
                        '下一步', '开始使用 · 3 / 6', True, '跳过'),
            'separator': (page, page.editMarkPushButton.parentWidget(), '确认页内分隔符',
                          '一页内有点击动画时，用分隔符将备注分段。点击“编辑分隔符”查看示例，默认使用 ●。没有点击动画可直接继续。',
                          '下一步', '开始使用 · 4 / 6', True, '跳过'),
            'import': (page, page.getFileButton.parentWidget(), '导入你的讲稿',
                       '这里可导入 PPT，也支持拖入文件。软件会读取备注并生成配音。点击“下一步”继续了解播放功能。',
                       '下一步', '开始使用 · 5 / 6', True, '跳过'),
            'play': (page, page.playButton.parentWidget(), '播放与审听',
                     '生成配音后，点击“播放”审听。启用同步翻页后，可配合 PPT 放映。实用工具中还可逐段调整配音，或导出音频与视频。',
                     '完成引导', '开始使用 · 6 / 6', True, '关闭'),
            'basics_complete': (page, None, '入门指引已完成',
                                '是否继续查看“进阶技巧”？了解讲稿更新、配音调整和视频导出等用法。也可稍后从设置中查看。',
                                '查看进阶技巧', '', False, '暂时结束'),
            'generated': (page, page.playButton.parentWidget(), '配音已准备好',
                          '可以在这里开始播放。在“实用工具”的“配音审听”中可逐段调整声音，也可以导出字幕、音频与视频。',
                          '前往实用工具', '', False, '知道了'),
        }
        extras = (settings.CardWidget_4,) if self.step == 'preview' else ()
        if self.step in self.ADVANCED_STEPS:
            tools = self.window.tools_interface
            tips = {
                'update_script': (page, page.getFileButton.parentWidget(), '让讲稿跟上 PPT 的修改',
                                  '修改 PowerPoint 或 WPS 的备注后，先保存文稿。回到这里点击更新提醒，也可从导入按钮的下拉菜单选择“更新 PPT 讲稿”。更新后保留所在页，从本页第一段重新播放。'),
                'script_options': (settings, settings.script_comments_switch.parentWidget(), '将注释与讲稿写在一起',
                                   '开启“识别讲稿注释”后，以 // 开头的整行文字会保留在备注中，但不参与朗读。下方的“生成前校验点击动画”可提醒空讲稿，以及点击动画与分段不一致的页面。'),
                'slideshow_sync': (settings, settings.page_turn_card, '按习惯设置放映联动',
                                   '启用同步翻页后，可选择翻页方式。使用“演示软件接口（COM）”时，还可分别设置自动开始放映、朗读跟随手动翻页，以及退出放映时是否停止朗读。'),
                'hotkeys': (settings, settings.hotkey_card, '在放映时用快捷键控制朗读',
                            '点击“配置”设置全局快捷键。配音生成后，即使当前在 PowerPoint 或其他程序中，也可控制播放、停止、上一段、下一段和重播当前段。保存时会检查组合键是否被占用。'),
                'audio_review': (tools, tools.audio_review_card, '逐段打磨配音',
                                 '生成配音后，在“配音审听”中按页或关键词找到片段，调整语速、音量与头尾留白。对比当前配音和新配音，满意后点击“采用新配音”。调整会用于播放和导出，也可撤销、重做。'),
                'ai_rewrite': (tools, tools.ai_rewrite_card, '逐页优化讲稿表达',
                               '在“AI 优化”中设置处理页面与场景。“朗读纠错”侧重保留正文、修正读法。可连接 AI 接口，也可导出任务后导回结果。在结果页对照原文审查，确认应用后才会更新讲稿并生成配音。'),
                'script_roundtrip': (tools, tools.script_export_card, '导出讲稿，编辑后再导回',
                                     '将讲稿导出为 Word 或 JSON，在外部编辑后重新导入，可按页更新当前讲稿。希望保留 PPT 关联时，先加载对应讲稿。也可通过“PPT 备注”另存一份包含当前讲稿的文稿。'),
                'audio_export': (tools, tools.audio_export_card, '按用途导出字幕和音频',
                                 '“逐条音频”适合逐段使用配音，“合并音频”会将整篇讲稿连成一个文件。SRT 字幕按配音时长生成时间轴，可导入视频剪辑软件继续制作。'),
                'video_export': (tools, tools.video_export_card, '选择适合的演示视频',
                                 '需要展示点击动画时，选择“放映录制”。逐页画面配合讲解时，可选择“静态合成”。打开导出窗口后，可设置字幕，并在高级设置中调整分辨率和帧率。'),
                'project_package': (tools, tools.project_package_card, '把讲稿与配音带到其他设备',
                                    '工程包可保留讲稿、配音及已采用的调整。将 PPT 文稿另行发送，接收方导入工程包后，可在“历史记录列表”中点击“关联 PPT”。文稿移动或改名后，也可从这里重新关联。'),
            }
            index = self.ADVANCED_STEPS.index(self.step)
            steps[self.step] = (*tips[self.step],
                                '完成引导' if index == len(self.ADVANCED_STEPS) - 1 else '下一步',
                                f'进阶技巧 · {index + 1} / {len(self.ADVANCED_STEPS)}', index > 0, '关闭')
            if self.step == 'script_options':
                extras = (settings.animation_check_switch.parentWidget(),)
        destination, target, title, body, action, progress, back, skip = steps[self.step]
        if destination is not settings and settings.is_dirty():
            self.overlay.dismiss()
            if not settings.prompt_unsaved_changes(allow_regenerate=False):
                return False
        self.target = target
        if destination is not None:
            self.window.switchTo(destination)
            if self.target is not None and destination.isAncestorOf(self.target):
                destination.bgScrollArea.ensureWidgetVisible(self.target, 20, 100)
        self.overlay.present(self.target, title, body, action, progress=progress, back=back, skip=skip, extras=extras)
        if self.step == 'play':
            self._save('onboarding_tools_seen')

    def next(self):
        if self.step in self.ADVANCED_STEPS:
            index = self.ADVANCED_STEPS.index(self.step) + 1
            if index == len(self.ADVANCED_STEPS):
                self.skip()
                return
            next_step = self.ADVANCED_STEPS[index]
        elif self.step == 'welcome':
            self._save('onboarding_seen')
            next_step = 'settings'
        elif self.step == 'settings':
            self.settings_button.click()
            return
        elif self.step == 'engine':
            next_step = 'preview'
        elif self.step == 'preview':
            self.overlay.dismiss()
            if not self.window.setting_interface.prompt_unsaved_changes(allow_regenerate=False):
                self._present()
                return
            next_step = 'separator'
        elif self.step == 'separator':
            next_step = 'import'
        elif self.step == 'import':
            next_step = 'play'
        elif self.step == 'play':
            next_step = 'basics_complete'
        elif self.step == 'basics_complete':
            self.start_advanced()
            return
        elif self.step == 'generated':
            self.skip()
            self.window.switchTo(self.window.tools_interface)
            return
        else:
            self.skip()
            return
        self._go_to(next_step)

    def back(self):
        if self.step in self.ADVANCED_STEPS:
            index = self.ADVANCED_STEPS.index(self.step)
            if index > 0:
                self._go_to(self.ADVANCED_STEPS[index - 1])
            return
        previous = {'engine': 'settings', 'preview': 'engine', 'separator': 'preview',
                    'import': 'separator', 'play': 'import'}
        if self.step in previous:
            self._go_to(previous[self.step])

    def skip(self):
        if self.step == 'generated':
            self._save('onboarding_tools_seen')
        elif self.step not in self.ADVANCED_STEPS:
            self._save('onboarding_seen')
        self.step = None
        self.target = None
        self.overlay.dismiss()

    def _on_generated(self, *_):
        self._generated = True

    def _on_audio_changed(self):
        page = self.window.ppt_r
        if self.step == 'import' and page.is_import and page.media_list and not page.is_busy():
            self.step = 'play'
            self._present()

    def _after_generation(self):
        if self.step in self.ADVANCED_STEPS:
            self._generated = False
            self._present()
            return
        if not self._generated:
            if self.step in ('import', 'separator', 'basics_complete'):
                self._present()
            return
        self._generated = False
        if not self.window.ppt_r.is_import:
            return
        if self.step in ('separator', 'basics_complete'):
            self._present()
            return
        if self.step in ('welcome', 'settings', 'engine', 'preview', 'import'):
            self.step = 'play'
        elif self.step is None and not self.settings.get('onboarding_tools_seen'):
            self.step = 'generated'
        else:
            return
        self._present()

    def _after_dialog(self, *_):
        self._waiting_dialog = False
        self._queue_refresh()

    def _queue_refresh(self):
        if not self._queued:
            self._queued = True
            QTimer.singleShot(0, self, self._refresh)

    def _refresh(self):
        self._queued = False
        if self.step is None:
            return
        if (not self.window.isVisible() or self.window.ppt_r.is_busy() or self._dragging
                or active_modal_dialog(self.window) is not None):
            self.overlay.dismiss()
            return
        if self.target is not None and not self.target.isVisible():
            self.overlay.dismiss()
            return
        if not self.overlay.isVisible():
            self._present()
        else:
            self.overlay.reposition(self.target)

    def eventFilter(self, obj, event):
        if self.step is None:
            return False
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if active_modal_dialog(self.window) is None and isinstance(obj, QWidget) and obj.window() is self.window:
                self.skip()
                return True
        if isinstance(obj, QWidget) and (obj is self.overlay or obj is self.overlay.card
                                        or self.overlay.card.isAncestorOf(obj)):
            return False
        if kind == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isModal():
            self.overlay.dismiss()
            self._present()
            return False
        if isinstance(obj, QWidget) and obj.window() is self.window:
            if kind == QEvent.Type.DragEnter:
                self._dragging = True
                self.overlay.dismiss()
            elif kind in (QEvent.Type.DragLeave, QEvent.Type.Drop):
                self._dragging = False
                self._queue_refresh()
            elif kind in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
                           QEvent.Type.LayoutRequest):
                if obj is self.window or obj is self.target or (self.target is not None and obj.isAncestorOf(self.target)):
                    self._queue_refresh()
        return False
