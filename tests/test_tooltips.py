"""统一提示策略覆盖动态控件和模型条目，并保留已有 Fluent 交互。"""

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QHelpEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)
from qfluentwidgets import Action, RoundMenu, ToolTip, ToolTipFilter
from qfluentwidgets.components.widgets.tool_tip import ItemViewToolTipDelegate, ItemViewToolTipType
from qt_test_case import QtTestCase

from ui.tooltips import install_fluent_tooltips


class FluentToolTipTests(QtTestCase):
    def setUp(self):
        self.manager = install_fluent_tooltips(self.qt)
        self.window = QWidget()
        self.window.resize(700, 400)
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        self.qt.removeEventFilter(self.manager)
        self.manager.hide()
        del self.qt._fluent_tooltips
        self.window.close()
        self.window.deleteLater()
        self.manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def label(self, text='提示'):
        label = QLabel('字段', self.window)
        label.setGeometry(80, 100, 150, 40)
        label.setToolTip(text)
        label.show()
        self.qt.processEvents()
        return label

    def help(self, widget, point=None):
        point = point or widget.rect().center()
        event = QHelpEvent(QEvent.Type.ToolTip, point, widget.mapToGlobal(point))
        self.qt.sendEvent(widget, event)

    def enter(self, widget):
        point = QPointF(widget.rect().center())
        event = QEnterEvent(point, point, QPointF(widget.mapToGlobal(point.toPoint())))
        self.qt.sendEvent(widget, event)

    def move(self, widget, point):
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(widget.mapToGlobal(point)),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.qt.sendEvent(widget, event)

    def test_dynamic_widgets_use_delayed_wrapped_plain_fluent_tooltip(self):
        self.assertIs(install_fluent_tooltips(self.qt), self.manager)
        label = self.label('<原始响应> ' + '长文本' * 100)
        self.enter(label)
        self.assertTrue(self.manager.timer.isActive())
        self.assertIsNone(self.manager._tooltip)
        QTest.qWait(350)
        tooltip = self.manager._tooltip
        self.assertIsInstance(tooltip, ToolTip)
        self.assertTrue(tooltip.isVisible())
        self.assertEqual(tooltip.text(), label.toolTip())
        self.assertEqual(tooltip.label.textFormat(), Qt.TextFormat.PlainText)
        self.assertTrue(tooltip.label.wordWrap())
        self.assertLessEqual(tooltip.label.width(), 560)

    def test_existing_fluent_filter_is_not_duplicated(self):
        label = self.label()
        handler = ToolTipFilter(label)
        label.installEventFilter(handler)
        self.enter(label)
        self.help(label)
        QTest.qWait(350)
        self.assertTrue(handler._tooltip.isVisible())
        self.assertIsNone(self.manager._tooltip)
        label.setToolTip('新提示')
        self.assertEqual(handler._tooltip.text(), '新提示')

    def test_disabled_explanation_and_dynamic_text(self):
        label = self.label('关联 PPT 后可用')
        label.setEnabled(False)
        self.help(label)
        tooltip = self.manager._tooltip
        self.assertTrue(tooltip.isVisible())
        label.setToolTip('重新关联 PPT')
        self.assertIs(self.manager._tooltip, tooltip)
        self.assertEqual(tooltip.text(), '重新关联 PPT')
        label.setToolTip('')
        self.assertFalse(tooltip.isVisible())

    def test_composite_child_uses_parent_tooltip(self):
        parent = self.label('整体说明')
        child = QLabel('内部', parent)
        child.show()
        self.help(child)
        self.assertEqual(self.manager._tooltip.text(), '整体说明')
        self.assertIs(self.manager._target.widget, parent)

    def table(self):
        table = QTableWidget(2, 2, self.window)
        table.setGeometry(30, 60, 400, 200)
        delegate = QStyledItemDelegate(table)
        table.setItemDelegate(delegate)
        item = QTableWidgetItem('讲稿正文')
        item.setToolTip('播放这一段')
        table.setItem(0, 0, item)
        table.setItem(0, 1, QTableWidgetItem('不应重复显示正文'))
        table.show()
        self.qt.processEvents()
        return table, delegate

    def test_table_item_tooltip_keeps_custom_delegate_and_does_not_repeat_body(self):
        table, delegate = self.table()
        viewport = table.viewport()
        self.help(viewport, table.visualItemRect(table.item(0, 0)).center())
        tooltip = self.manager._tooltip
        self.assertEqual(tooltip.text(), '播放这一段')
        self.assertIs(table.itemDelegate(), delegate)
        self.assertTrue(viewport.hasMouseTracking())
        self.move(viewport, table.visualItemRect(table.item(0, 1)).center())
        self.assertFalse(tooltip.isVisible())

    def test_headers_and_native_menu_actions(self):
        table, _ = self.table()
        header = QTableWidgetItem('讲稿')
        header.setToolTip('按页组织讲稿')
        table.setHorizontalHeaderItem(0, header)
        self.help(table.horizontalHeader().viewport(), QPoint(30, 5))
        self.assertEqual(self.manager._tooltip.text(), '按页组织讲稿')
        menu = QMenu(self.window)
        menu.setToolTipsVisible(True)
        action = menu.addAction('操作')
        action.setToolTip('操作说明')
        menu.popup(self.window.mapToGlobal(QPoint(100, 100)))
        self.qt.processEvents()
        self.help(menu, menu.actionGeometry(action).center())
        self.assertEqual(self.manager._tooltip.text(), '操作说明')
        menu.close()

    def test_fluent_menu_keeps_its_item_tooltip(self):
        menu = RoundMenu(parent=self.window)
        action = Action('操作', menu)
        action.setToolTip('菜单说明')
        menu.addAction(action)
        handler = ItemViewToolTipDelegate(menu.view, 100, ItemViewToolTipType.LIST)
        menu.view.itemDelegate().tooltipDelegate = handler
        menu.show()
        self.qt.processEvents()
        viewport = menu.view.viewport()
        self.enter(viewport)
        self.help(viewport, menu.view.visualItemRect(menu.view.item(0)).center())
        QTest.qWait(150)
        self.assertTrue(handler._tooltip.isVisible())
        self.assertEqual(handler._tooltip.text(), '菜单说明')
        self.assertIsNone(self.manager._tooltip)
        menu.close()

    def test_fluent_menu_with_lazy_delegate_uses_shared_tooltip(self):
        menu = RoundMenu(parent=self.window)
        action = Action('操作', menu)
        action.setToolTip('菜单说明')
        menu.addAction(action)
        menu.show()
        self.qt.processEvents()
        self.help(menu.view.viewport(), menu.view.visualItemRect(menu.view.item(0)).center())
        self.assertEqual(self.manager._tooltip.text(), '菜单说明')
        self.assertIsNone(menu.view.itemDelegate().tooltipDelegate)
        menu.close()

    def test_scroll_and_leave_hide_tooltip(self):
        table, _ = self.table()
        self.help(table.viewport(), table.visualItemRect(table.item(0, 0)).center())
        tooltip = self.manager._tooltip
        table.verticalScrollBar().valueChanged.emit(1)
        self.assertFalse(tooltip.isVisible())
        self.help(table.viewport(), table.visualItemRect(table.item(0, 0)).center())
        self.qt.sendEvent(table.viewport(), QEvent(QEvent.Type.Leave))
        self.assertFalse(tooltip.isVisible())

    def test_deleted_source_before_delay_is_safe_and_focus_is_preserved(self):
        label = self.label()
        self.enter(label)
        label.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QTest.qWait(350)
        self.assertTrue(self.manager._tooltip is None or not self.manager._tooltip.isVisible())
        edit = QLineEdit(self.window)
        edit.setToolTip('输入内容')
        edit.show()
        edit.setFocus()
        self.help(edit)
        self.assertTrue(edit.hasFocus())
        QTest.keyClicks(edit, 'text')
        self.assertEqual(edit.text(), 'text')
        self.assertFalse(self.manager._tooltip.isVisible())
