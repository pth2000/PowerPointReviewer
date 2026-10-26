"""文件拖放期间显示的主题化投放提示层。"""

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme, qconfig, themeColor
from qfluentwidgets.common.font import getFont
from qfluentwidgets.common.icon import drawIcon

from app.documents import package as project_package, io as script_io
from ui.icons import SystemIcon


class FileDropOverlay(QWidget):
    """覆盖内容区而不截获鼠标，文件仍交给统一导入入口处理。"""

    FORMATS = '支持 PPT 演示文稿、Word 讲稿、JSON 讲稿及工程包导入'

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.state = 'ready'
        self.title = '松开鼠标即可导入'
        self.filename = ''
        self.caption = self.FORMATS
        self._progress = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._animate)
        qconfig.themeChanged.connect(self._refresh_theme)
        qconfig.themeColorChanged.connect(self._refresh_theme)
        self.hide()

    def _animate(self, value):
        self._progress = float(value)
        self.update()

    def _refresh_theme(self, *_):
        self.update()

    def show_files(self, files, *, busy=False, updating=False):
        if busy:
            state, title, caption = 'busy', '请稍后再导入', '当前任务完成后即可导入'
        elif len(files) != 1:
            state, title, caption = 'multiple', '每次导入一个文件', '请保留一个讲稿或工程包文件'
        elif Path(files[0]).suffix.lower() not in (*script_io.SCRIPT_SUFFIXES, project_package.PACKAGE_SUFFIX):
            state, title, caption = 'unsupported', '暂不支持这个文件', self.FORMATS
        else:
            state, title, caption = 'ready', '松开鼠标即可导入', self.FORMATS
            if updating and Path(files[0]).suffix.lower() in ('.docx', '.json'):
                title = '松开鼠标即可更新讲稿'
        filename = Path(files[0]).name if len(files) == 1 else f'已拖入 {len(files)} 个文件'
        changed = (state, title, filename, caption) != (self.state, self.title, self.filename, self.caption)
        self.state, self.title, self.filename, self.caption = state, title, filename, caption
        self.setAccessibleName(f'{title}，{filename}，{caption}')
        self.setGeometry(self.parentWidget().rect())
        if not self.isVisible():
            self._progress = 0.0
            self.show()
            self.raise_()
            self._animation.start()
        elif changed:
            self.update()

    def dismiss(self):
        self._animation.stop()
        self.hide()

    def _text(self, painter, rect, text, size, color, *, bold=False, elide=False):
        font = getFont(size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        if elide:
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, int(rect.width()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._progress)
        dark = isDarkTheme()
        accent = themeColor() if self.state == 'ready' else QColor('#F3BB65' if dark else '#96600B')
        area = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        if area.width() <= 0 or area.height() <= 0:
            return
        # 柔和底色压低原页面的视觉干扰，边框明确标出可投放范围。
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(27, 29, 32, 238) if dark else QColor(249, 250, 252, 239))
        painter.drawRoundedRect(area, 18, 18)
        tint = QColor(accent)
        tint.setAlpha(12 if dark else 8)
        painter.setBrush(tint)
        painter.drawRoundedRect(area, 18, 18)
        border = QColor(accent)
        border.setAlpha(150)
        pen = QPen(border, 1.5, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(area.adjusted(1, 1, -1, -1), 17, 17)

        width = min(420.0, max(0.0, area.width() - 48))
        height = min(250.0, max(0.0, area.height() - 32))
        card = QRectF(area.center().x() - width / 2,
                      area.center().y() - height / 2 + 8 * (1 - self._progress), width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(12, 0, -1):
            painter.setBrush(QColor(0, 0, 0, 2))
            painter.drawRoundedRect(card.adjusted(-spread, 3 - spread, spread, 3 + spread), 20, 20)
        painter.setPen(QPen(QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 16), 1))
        painter.setBrush(QColor('#26292E' if dark else '#FFFFFF'))
        painter.drawRoundedRect(card, 18, 18)

        icon_box = QRectF(card.center().x() - 28, card.top() + 28, 56, 56)
        painter.setPen(Qt.PenStyle.NoPen)
        tile = QColor(accent)
        tile.setAlpha(28 if dark else 18)
        painter.setBrush(tile)
        painter.drawRoundedRect(icon_box, 15, 15)
        drawIcon(SystemIcon.DOCUMENT if self.state == 'ready' else SystemIcon.INFO,
                 painter, icon_box.adjusted(14, 14, -14, -14), fill=accent.name())
        if self.state == 'ready':
            badge = QRectF(icon_box.right() - 17, icon_box.bottom() - 17, 24, 24)
            painter.setBrush(accent)
            painter.setPen(QPen(QColor('#26292E' if dark else '#FFFFFF'), 3))
            painter.drawEllipse(badge)
            drawIcon(SystemIcon.DOWNLOAD, painter, badge.adjusted(6, 6, -6, -6), fill='#FFFFFF')

        text = QColor('#F4F5F7' if dark else '#202124')
        secondary = QColor('#CFD3D9' if dark else '#545B66')
        caption = QColor('#AEB5BF' if dark else '#69727F')
        self._text(painter, QRectF(card.left() + 20, card.top() + 102, width - 40, 34),
                   self.title, 21, text, bold=True)
        self._text(painter, QRectF(card.left() + 28, card.top() + 147, width - 56, 26),
                   self.filename, 15, secondary, elide=True)
        self._text(painter, QRectF(card.left() + 20, card.bottom() - 46, width - 40, 24),
                   self.caption, 13, caption)
