"""阅读区域的滚轮距离不随段落高度或快速连滚而放大。"""

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidgetItem, QTableWidgetItem
from qfluentwidgets import ListWidget, TableWidget
from qt_test_case import QtTestCase

from ui.dialogs.rewrite_widgets import text_editor
from ui.scrolling import configure_reading_scroll


class ReadingScrollTests(QtTestCase):
    def scroll(self, widget, notches=1, delta=-120):
        point = widget.viewport().rect().center()
        for _ in range(notches):
            event = QWheelEvent(
                QPointF(point), QPointF(widget.viewport().mapToGlobal(point)),
                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
            )
            self.qt.sendEvent(widget.viewport(), event)
        QTest.qWait(300)
        return widget.verticalScrollBar().value()

    def table(self, height):
        widget = TableWidget()
        configure_reading_scroll(widget)
        widget.setColumnCount(1)
        widget.setRowCount(30)
        for row in range(30):
            widget.setItem(row, 0, QTableWidgetItem('讲稿'))
            widget.setRowHeight(row, height)
        widget.resize(500, 350)
        widget.show()
        self.qt.processEvents()
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def test_long_and_short_rows_move_the_same_distance(self):
        short = self.scroll(self.table(40))
        long = self.scroll(self.table(1500))
        self.assertGreater(short, 0)
        self.assertLessEqual(short, 80)
        self.assertAlmostEqual(short, long, delta=2)

    def test_repeated_notches_do_not_accelerate(self):
        widget = self.table(300)
        single = self.scroll(widget)
        widget.verticalScrollBar().setValue(0)
        repeated = self.scroll(widget, notches=5)
        self.assertAlmostEqual(repeated, single * 5, delta=5)
        self.assertLess(self.scroll(widget, delta=120), repeated)

    def test_page_picker_and_text_editor_keep_their_scroll_units(self):
        listing = ListWidget()
        configure_reading_scroll(listing)
        for index in range(40):
            item = QListWidgetItem(str(index))
            item.setSizeHint(QSize(100, 72))
            listing.addItem(item)
        editor = text_editor(None)
        editor.setPlainText('讲稿正文\n' * 500)
        for widget in (listing, editor):
            widget.resize(500, 350)
            widget.show()
            self.addCleanup(widget.deleteLater)
            self.addCleanup(widget.close)
        self.qt.processEvents()
        self.assertLessEqual(self.scroll(listing), 80)
        self.assertLessEqual(self.scroll(editor), 3)
        self.assertGreater(editor.verticalScrollBar().value(), 0)
