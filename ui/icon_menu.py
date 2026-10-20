"""在菜单的固定图标槽内直接绘制原始图标。"""

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
from qfluentwidgets import RoundMenu
from qfluentwidgets.components.widgets.menu import ShortcutMenuItemDelegate


class _IconMenuDelegate(ShortcutMenuItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # 保留图标槽和原生文字布局，图案由 paint 使用动作的原始图标绘制。
        option.icon = QIcon()

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if self._isSeparator(index):
            return
        action = index.data(Qt.ItemDataRole.UserRole)
        if action is None or action.icon().isNull():
            return
        view = self.parent()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        slot = view.style().subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, opt, view)
        size = max(1, min(slot.width(), slot.height()) - 2)
        origin = QPointF(slot.x() + (slot.width() - size) / 2,
                         slot.y() + (slot.height() - size) / 2)
        transform = painter.deviceTransform()
        inverse, valid = transform.inverted()
        if valid:
            physical = transform.map(origin)
            origin = inverse.map(QPointF(round(physical.x()), round(physical.y())))
        painter.save()
        painter.translate(origin)
        mode = QIcon.Mode.Normal if action.isEnabled() else QIcon.Mode.Disabled
        state = QIcon.State.On if action.isChecked() else QIcon.State.Off
        action.icon().paint(painter, QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, mode, state)
        painter.restore()


class IconMenu(RoundMenu):
    """保留原生菜单操作，悬停和按下只改变条目反馈，不重缩放图标。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view.setIconSize(QSize(16, 16))
        self.view.setItemDelegate(_IconMenuDelegate(self.view))
