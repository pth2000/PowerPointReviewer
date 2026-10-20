"""菜单图标的矢量缩放、按下状态和动作触发回归。"""

from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
from qfluentwidgets import Action, FluentIcon

import resource_rc  # noqa: F401  注册项目 SVG 资源。
from qt_test_case import QtTestCase
from ui import icons, theme
from ui.icon_menu import IconMenu


class ImportIconTests(QtTestCase):
    def setUp(self):
        theme.apply_theme('light', theme.DEFAULT_COLOR)
        self.menu = IconMenu()
        for source, label in (('word', '导入 Word 讲稿'), (FluentIcon.DOCUMENT, '导入 JSON 讲稿'),
                              (FluentIcon.FOLDER, '导入工程包'), ('update', '历史记录列表'),
                              (FluentIcon.SYNC, '更新 PPT 讲稿')):
            action = icons.apply(Action(label), f':/image/image/{source}.svg') if isinstance(source, str) else Action(source, label)
            self.menu.addAction(action)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.menu.close()
        self.menu.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        icons.refresh_all()
        theme.apply_theme('light', theme.DEFAULT_COLOR)
        self.qt.processEvents()

    def test_resource_icons_render_at_target_resolution_including_large_requests(self):
        for name in ('word.svg', 'update.svg'):
            icon = icons.themed_icon(':/image/image/' + name)
            for size in (14, 80):
                for ratio in (1.0, 1.25, 1.5, 2.0):
                    with self.subTest(name=name, size=size, ratio=ratio):
                        pixmap = icon.pixmap(QSize(size, size), ratio)
                        self.assertEqual(pixmap.width(), round(size * ratio))
                        self.assertEqual(pixmap.height(), round(size * ratio))
                        self.assertEqual(pixmap.devicePixelRatio(), ratio)
                        self.assertFalse(pixmap.isNull())

    def test_theme_and_disabled_state_keep_resource_icons_visible(self):
        for appearance, expected in (('light', '#2c2c2c'), ('dark', '#ffffff')):
            theme.apply_theme(appearance, theme.DEFAULT_COLOR)
            icon = self.menu.actions()[0].icon()
            normal = icon.pixmap(QSize(24, 24), 1.5).toImage()
            colors = [normal.pixelColor(x, y) for y in range(normal.height()) for x in range(normal.width())]
            self.assertTrue(any(color.alpha() == 255 and color.name() == expected for color in colors))
            disabled = icon.pixmap(QSize(24, 24), 1.5, QIcon.Mode.Disabled).toImage()
            alpha = max(disabled.pixelColor(x, y).alpha() for y in range(disabled.height()) for x in range(disabled.width()))
            self.assertGreater(alpha, 100)
            self.assertLessEqual(alpha, 128)

    def _render_icon_bounds(self, row, ratio, state):
        view = self.menu.view
        delegate = view.itemDelegate()
        index = view.model().index(row, 0)
        option = QStyleOptionViewItem()
        option.initFrom(view)
        option.widget = view
        option.rect = QRect(0, 0, 240, 28)
        option.decorationSize = view.iconSize()
        option.state = state
        delegate.initStyleOption(option, index)
        slot = view.style().subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option, view)
        image = QImage(round(240 * ratio), round(28 * ratio), QImage.Format.Format_ARGB32)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        delegate.paint(painter, option, index)
        painter.end()
        points = [(x, y) for y in range(round(slot.top() * ratio), round((slot.bottom() + 1) * ratio))
                  for x in range(round(slot.left() * ratio), round((slot.right() + 1) * ratio))
                  if image.pixelColor(x, y).lightness() < 100]
        self.assertTrue(points)
        return (min(x for x, _ in points), min(y for _, y in points),
                max(x for x, _ in points), max(y for _, y in points)), slot

    def test_icon_geometry_stays_inside_its_slot_when_hovered_and_pressed(self):
        enabled = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active
        pressed = enabled | QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Sunken
        for row in range(self.menu.view.count()):
            for ratio in (1.0, 1.25, 1.5, 2.0):
                with self.subTest(row=row, ratio=ratio):
                    normal, slot = self._render_icon_bounds(row, ratio, enabled)
                    hover, _ = self._render_icon_bounds(row, ratio, enabled | QStyle.StateFlag.State_MouseOver)
                    held, _ = self._render_icon_bounds(row, ratio, pressed)
                    self.assertEqual(normal, hover)
                    self.assertEqual(normal, held)
                    self.assertGreaterEqual(normal[0], round(slot.left() * ratio) + 1)
                    self.assertGreaterEqual(normal[1], round(slot.top() * ratio) + 1)
                    self.assertLess(normal[2], round((slot.right() + 1) * ratio) - 1)
                    self.assertLess(normal[3], round((slot.bottom() + 1) * ratio) - 1)
                    self.assertGreaterEqual(normal[3] - normal[1] + 1, 10 * ratio)

    def test_press_does_not_trigger_until_release_and_disabled_action_stays_disabled(self):
        action = self.menu.actions()[0]
        callback = Mock()
        action.triggered.connect(callback)
        self.menu.show()
        self.qt.processEvents()
        rect = self.menu.view.visualItemRect(action.property('item'))
        QTest.mousePress(self.menu.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        callback.assert_not_called()
        QTest.mouseRelease(self.menu.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        callback.assert_called_once()
        action.setEnabled(False)
        self.menu.show()
        self.qt.processEvents()
        rect = self.menu.view.visualItemRect(action.property('item'))
        QTest.mouseClick(self.menu.view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        callback.assert_called_once()
