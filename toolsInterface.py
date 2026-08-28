# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'toolsInterface.ui'
##
## Created by: Qt User Interface Compiler version 6.10.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QSizePolicy, QSpacerItem,
    QVBoxLayout, QWidget)

from qfluentwidgets import (CaptionLabel, ScrollArea, SubtitleLabel)
import resource_rc

class Ui_toolsInterface(object):
    def setupUi(self, toolsInterface):
        if not toolsInterface.objectName():
            toolsInterface.setObjectName(u"toolsInterface")
        toolsInterface.resize(850, 720)
        self.horizontalLayout = QHBoxLayout(toolsInterface)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.bgScrollArea = ScrollArea(toolsInterface)
        self.bgScrollArea.setObjectName(u"bgScrollArea")
        self.bgScrollArea.setWidgetResizable(True)
        self.scrollAreaWidgetContents = QWidget()
        self.scrollAreaWidgetContents.setObjectName(u"scrollAreaWidgetContents")
        self.scrollAreaWidgetContents.setGeometry(QRect(0, 0, 830, 700))
        self.verticalLayout = QVBoxLayout(self.scrollAreaWidgetContents)
        self.verticalLayout.setSpacing(6)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(20, 20, 20, 20)
        self.importWidget = QWidget(self.scrollAreaWidgetContents)
        self.importWidget.setObjectName(u"importWidget")
        self.verticalLayout_2 = QVBoxLayout(self.importWidget)
        self.verticalLayout_2.setSpacing(5)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.SubtitleLabel = SubtitleLabel(self.importWidget)
        self.SubtitleLabel.setObjectName(u"SubtitleLabel")
        self.SubtitleLabel.setMinimumSize(QSize(0, 30))
        self.SubtitleLabel.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_2.addWidget(self.SubtitleLabel)

        self.CaptionLabel_4 = CaptionLabel(self.importWidget)
        self.CaptionLabel_4.setObjectName(u"CaptionLabel_4")

        self.verticalLayout_2.addWidget(self.CaptionLabel_4)


        self.verticalLayout.addWidget(self.importWidget)

        self.verticalSpacer = QSpacerItem(20, 353, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout.addItem(self.verticalSpacer)

        self.bgScrollArea.setWidget(self.scrollAreaWidgetContents)

        self.horizontalLayout.addWidget(self.bgScrollArea)


        self.retranslateUi(toolsInterface)

        QMetaObject.connectSlotsByName(toolsInterface)
    # setupUi

    def retranslateUi(self, toolsInterface):
        toolsInterface.setWindowTitle(QCoreApplication.translate("toolsInterface", u"Form", None))
        self.SubtitleLabel.setText(QCoreApplication.translate("toolsInterface", u"\u6587\u4ef6\u8f6c\u6362", None))
        self.CaptionLabel_4.setText(QCoreApplication.translate("toolsInterface", u"\u5c06\u6f14\u8bb2\u7a3f\u8f6c\u6362\u4e3a\u4e0d\u540c\u683c\u5f0f\uff0c\u5bfc\u5165\u6f14\u8bb2\u7a3f\u540e\u53ef\u7528", None))
    # retranslateUi

