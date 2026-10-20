"""AI 工作区的页面摘要、状态标记与紧凑切换控件。"""

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QStyle, QWidget
from qfluentwidgets import PlainTextEdit, SegmentedWidget, isDarkTheme, setCustomStyleSheet, themeColor
from qfluentwidgets.common.font import getFont
from qfluentwidgets.common.style_sheet import CustomStyleSheet, setStyleSheet
from qfluentwidgets.components.widgets.list_view import ListItemDelegate


PREVIEW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
STATUS_ROLE = PREVIEW_ROLE + 1


def metadata_label(label):
    label.setTextColor(QColor('#666666'), QColor('#BBBBBB'))
    return label


def workspace_frame(parent, name, light, dark):
    frame = QFrame(parent)
    frame.setObjectName(name)
    setStyleSheet(frame, CustomStyleSheet(frame).setCustomStyleSheet(light, dark))
    return frame


def text_editor(parent, *, reading=False, font_size=16):
    editor = PlainTextEdit(parent)
    editor.layer.hide()
    font = editor.font()
    font.setPixelSize(font_size)
    editor.setFont(font)
    editor.document().setDocumentMargin(10)
    if reading:
        style = 'QPlainTextEdit { border: 1px solid transparent; background: transparent; padding: 4px; }'
        setCustomStyleSheet(editor, style, style)
    else:
        base = 'QPlainTextEdit { border: 1px solid %s; border-radius: 8px; background: %s; padding: 4px; }'
        setCustomStyleSheet(editor, base % ('#E7E7EA', '#FFFFFF'), base % ('#454545', '#2B2B2B'))
    return editor


class WorkspaceSwitch(SegmentedWidget):
    """选中底板和文字保持相同的边距，复用原有滑动动画。"""

    def __init__(self, width, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, 36)

    def insertItem(self, index, routeKey, text, onClick=None, icon=None):
        item = super().insertItem(index, routeKey, text, onClick, icon)
        if item is not None:
            item.setFixedHeight(36)
            style = '''SegmentedItem[isSelected=true], SegmentedItem[isSelected=false] {
                padding: 6px 12px; margin: 0; border: none; background: transparent;
            }'''
            setCustomStyleSheet(item, style, style)
        return item

    def paintEvent(self, event):
        QWidget.paintEvent(self, event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#303030') if dark else QColor('#EDEDEF'))
        painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        item = self.currentItem()
        if item is not None:
            rect = QRectF(item.rect()).translated(float(self.slideAni.value()), 0).adjusted(2, 2, -2, -2)
            painter.setPen(QColor('#505050') if dark else QColor('#DFDFE2'))
            painter.setBrush(QColor('#454545') if dark else QColor('#FFFFFF'))
            painter.drawRoundedRect(rect, 5, 5)


class ManuscriptPageDelegate(ListItemDelegate):
    """页面标题和状态位于首行，正文摘要帮助定位，不为每页创建控件。"""

    def sizeHint(self, option, index):
        return QSize(0, 72)

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = isDarkTheme()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = QRectF(option.rect).adjusted(4, 3, -4, -3)
        accent = themeColor()
        fill = QColor(accent)
        fill.setAlpha(30 if dark else 17)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 7, 7)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(rect.left(), rect.center().y() - 10, 3, 20), 1.5, 1.5)
        elif index.row() == self.hoverRow:
            painter.setBrush(QColor(255, 255, 255, 12) if dark else QColor(0, 0, 0, 7))
            painter.drawRoundedRect(rect, 7, 7)
        if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            self._drawCheckBox(painter, option, index)
        left, right = option.rect.left() + 44, option.rect.right() - 14
        heading = QRectF(left, option.rect.top() + 11, right - left, 22)
        status = index.data(STATUS_ROLE) or ''
        painter.setFont(getFont(12))
        if status:
            width = painter.fontMetrics().horizontalAdvance(status) + 14
            badge = QRectF(right - width, heading.top(), width, 21)
            color = QColor('#BBBBBB' if dark else '#646464')
            if status == '已优化':
                color = accent.lighter(150) if dark else accent
            elif status == '需确认':
                color = QColor('#E7BC70' if dark else '#946000')
            elif status == '失败':
                color = QColor('#F4878B' if dark else '#C42B32')
            background = QColor(color)
            background.setAlpha(28 if dark else 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(color)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, status)
            heading.setRight(badge.left() - 8)
        painter.setFont(getFont(14, QFont.Weight.DemiBold if selected else QFont.Weight.Normal))
        painter.setPen(QColor('#F4F4F4' if dark else '#242424'))
        title = f'第 {index.data(Qt.ItemDataRole.UserRole)} 页'
        painter.drawText(heading, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(heading.width())))
        painter.setFont(getFont(12))
        painter.setPen(QColor('#B6B6B6' if dark else '#686868'))
        preview = str(index.data(PREVIEW_ROLE) or '本页没有讲稿')
        painter.drawText(QRectF(left, option.rect.top() + 37, right - left, 20),
                         Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(preview, Qt.TextElideMode.ElideRight, right - left))
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(accent)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                             QEvent.Type.MouseButtonDblClick)
                and event.button() == Qt.MouseButton.LeftButton
                and index.flags() & Qt.ItemFlag.ItemIsUserCheckable
                and index.flags() & Qt.ItemFlag.ItemIsEnabled):
            checkbox = QRectF(option.rect.left() + 11, option.rect.center().y() - 13, 27, 27)
            if checkbox.contains(event.position()):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
                    return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                                         Qt.ItemDataRole.CheckStateRole)
                return True
        return super().editorEvent(event, model, option, index)
