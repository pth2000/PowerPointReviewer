# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'toolsInterface.ui'
##
## Created by: Qt User Interface Compiler version 6.3.1
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
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QSizePolicy,
    QSpacerItem, QVBoxLayout, QWidget)

from qfluentwidgets import (CaptionLabel, CardWidget, PushButton, SubtitleLabel)
import resource_rc

class Ui_toolsInterface(object):
    def setupUi(self, toolsInterface):
        if not toolsInterface.objectName():
            toolsInterface.setObjectName(u"toolsInterface")
        toolsInterface.resize(850, 412)
        self.horizontalLayout = QHBoxLayout(toolsInterface)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.bgWidget = QWidget(toolsInterface)
        self.bgWidget.setObjectName(u"bgWidget")
        self.verticalLayout_8 = QVBoxLayout(self.bgWidget)
        self.verticalLayout_8.setObjectName(u"verticalLayout_8")
        self.verticalLayout_8.setContentsMargins(20, 50, 20, 20)
        self.importWidget = QWidget(self.bgWidget)
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

        self.CardWidget = CardWidget(self.importWidget)
        self.CardWidget.setObjectName(u"CardWidget")
        self.horizontalLayout_11 = QHBoxLayout(self.CardWidget)
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.horizontalLayout_11.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_7 = QVBoxLayout()
        self.verticalLayout_7.setSpacing(0)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.pptPathLabel = QLabel(self.CardWidget)
        self.pptPathLabel.setObjectName(u"pptPathLabel")
        self.pptPathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_7.addWidget(self.pptPathLabel)

        self.pptCaptionLabel = CaptionLabel(self.CardWidget)
        self.pptCaptionLabel.setObjectName(u"pptCaptionLabel")

        self.verticalLayout_7.addWidget(self.pptCaptionLabel)


        self.horizontalLayout_11.addLayout(self.verticalLayout_7)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_11.addItem(self.horizontalSpacer)

        self.transformPPTPushButton = PushButton(self.CardWidget)
        self.transformPPTPushButton.setObjectName(u"transformPPTPushButton")
        self.transformPPTPushButton.setMinimumSize(QSize(180, 0))
        self.transformPPTPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_11.addWidget(self.transformPPTPushButton)


        self.verticalLayout_2.addWidget(self.CardWidget)

        self.CardWidget_2 = CardWidget(self.importWidget)
        self.CardWidget_2.setObjectName(u"CardWidget_2")
        self.horizontalLayout_12 = QHBoxLayout(self.CardWidget_2)
        self.horizontalLayout_12.setObjectName(u"horizontalLayout_12")
        self.horizontalLayout_12.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_9 = QVBoxLayout()
        self.verticalLayout_9.setSpacing(0)
        self.verticalLayout_9.setObjectName(u"verticalLayout_9")
        self.wordPathLabel = QLabel(self.CardWidget_2)
        self.wordPathLabel.setObjectName(u"wordPathLabel")
        self.wordPathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_9.addWidget(self.wordPathLabel)

        self.wordCaptionLabel = CaptionLabel(self.CardWidget_2)
        self.wordCaptionLabel.setObjectName(u"wordCaptionLabel")

        self.verticalLayout_9.addWidget(self.wordCaptionLabel)


        self.horizontalLayout_12.addLayout(self.verticalLayout_9)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_12.addItem(self.horizontalSpacer_2)

        self.transformWordPushButton = PushButton(self.CardWidget_2)
        self.transformWordPushButton.setObjectName(u"transformWordPushButton")
        self.transformWordPushButton.setMinimumSize(QSize(180, 0))
        self.transformWordPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_12.addWidget(self.transformWordPushButton)


        self.verticalLayout_2.addWidget(self.CardWidget_2)

        self.CardWidget_3 = CardWidget(self.importWidget)
        self.CardWidget_3.setObjectName(u"CardWidget_3")
        self.horizontalLayout_13 = QHBoxLayout(self.CardWidget_3)
        self.horizontalLayout_13.setObjectName(u"horizontalLayout_13")
        self.horizontalLayout_13.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_10 = QVBoxLayout()
        self.verticalLayout_10.setSpacing(0)
        self.verticalLayout_10.setObjectName(u"verticalLayout_10")
        self.jsonPathLabel = QLabel(self.CardWidget_3)
        self.jsonPathLabel.setObjectName(u"jsonPathLabel")
        self.jsonPathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_10.addWidget(self.jsonPathLabel)

        self.jsonCaptionLabel = CaptionLabel(self.CardWidget_3)
        self.jsonCaptionLabel.setObjectName(u"jsonCaptionLabel")

        self.verticalLayout_10.addWidget(self.jsonCaptionLabel)


        self.horizontalLayout_13.addLayout(self.verticalLayout_10)

        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_13.addItem(self.horizontalSpacer_3)

        self.transformJsonPushButton = PushButton(self.CardWidget_3)
        self.transformJsonPushButton.setObjectName(u"transformJsonPushButton")
        self.transformJsonPushButton.setMinimumSize(QSize(180, 0))
        self.transformJsonPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_13.addWidget(self.transformJsonPushButton)


        self.verticalLayout_2.addWidget(self.CardWidget_3)


        self.verticalLayout_8.addWidget(self.importWidget)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.verticalLayout_8.addItem(self.verticalSpacer)


        self.horizontalLayout.addWidget(self.bgWidget)


        self.retranslateUi(toolsInterface)

        QMetaObject.connectSlotsByName(toolsInterface)
    # setupUi

    def retranslateUi(self, toolsInterface):
        toolsInterface.setWindowTitle(QCoreApplication.translate("toolsInterface", u"Form", None))
        self.SubtitleLabel.setText(QCoreApplication.translate("toolsInterface", u"\u6587\u4ef6\u8f6c\u6362", None))
        self.CaptionLabel_4.setText(QCoreApplication.translate("toolsInterface", u"\u5c06\u6f14\u8bb2\u7a3f\u8f6c\u6362\u4e3a\u4e0d\u540c\u683c\u5f0f\uff0c\u5bfc\u5165\u6f14\u8bb2\u7a3f\u540e\u53ef\u7528", None))
        self.pptPathLabel.setText(QCoreApplication.translate("toolsInterface", u"\u5199\u5165 PowerPoint", None))
        self.pptCaptionLabel.setText(QCoreApplication.translate("toolsInterface", u"\u5c06\u8bb2\u7a3f\u5185\u5bb9\u5199\u5165 PowerPoint \u5907\u6ce8", None))
        self.transformPPTPushButton.setText(QCoreApplication.translate("toolsInterface", u"\u9009\u62e9 PowerPoint", None))
        self.wordPathLabel.setText(QCoreApplication.translate("toolsInterface", u"\u8f6c\u4e3a Word", None))
        self.wordCaptionLabel.setText(QCoreApplication.translate("toolsInterface", u"\u5c06\u8bb2\u7a3f\u5185\u5bb9\u5bfc\u51fa\u4e3a Word", None))
        self.transformWordPushButton.setText(QCoreApplication.translate("toolsInterface", u"\u8f6c\u6362", None))
        self.jsonPathLabel.setText(QCoreApplication.translate("toolsInterface", u"\u8f6c\u4e3a JSON", None))
        self.jsonCaptionLabel.setText(QCoreApplication.translate("toolsInterface", u"\u5c06\u8bb2\u7a3f\u5185\u5bb9\u5bfc\u51fa\u4e3a JSON", None))
        self.transformJsonPushButton.setText(QCoreApplication.translate("toolsInterface", u"\u8f6c\u6362", None))
    # retranslateUi

