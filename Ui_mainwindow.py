# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'Ui_mainwindow.ui'
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

from qfluentwidgets import (CaptionLabel, CardWidget, CheckBox, IconInfoBadge,
    IconWidget, IndeterminateProgressBar, InfoBadge, PrimarySplitPushButton,
    PushButton, ScrollArea, SpinBox, SplitPushButton,
    SubtitleLabel, SwitchButton, ToolButton)
import resource_rc

class Ui_mainwindow(object):
    def setupUi(self, mainwindow):
        if not mainwindow.objectName():
            mainwindow.setObjectName(u"mainwindow")
        mainwindow.resize(850, 720)
        font = QFont()
        font.setFamilies([u"Microsoft YaHei UI"])
        mainwindow.setFont(font)
        self.horizontalLayout_13 = QHBoxLayout(mainwindow)
        self.horizontalLayout_13.setObjectName(u"horizontalLayout_13")
        self.bgScrollArea = ScrollArea(mainwindow)
        self.bgScrollArea.setObjectName(u"bgScrollArea")
        self.bgScrollArea.setWidgetResizable(True)
        self.scrollAreaWidgetContents_3 = QWidget()
        self.scrollAreaWidgetContents_3.setObjectName(u"scrollAreaWidgetContents_3")
        self.scrollAreaWidgetContents_3.setGeometry(QRect(0, 0, 830, 700))
        self.scrollAreaWidgetContents_3.setStyleSheet(u"")
        self.verticalLayout_11 = QVBoxLayout(self.scrollAreaWidgetContents_3)
        self.verticalLayout_11.setSpacing(6)
        self.verticalLayout_11.setObjectName(u"verticalLayout_11")
        self.verticalLayout_11.setContentsMargins(20, 20, 20, 20)
        self.importWidget = QWidget(self.scrollAreaWidgetContents_3)
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

        self.widget_5 = QWidget(self.importWidget)
        self.widget_5.setObjectName(u"widget_5")
        self.widget_5.setMinimumSize(QSize(0, 20))
        self.widget_5.setMaximumSize(QSize(16777215, 20))
        self.horizontalLayout_2 = QHBoxLayout(self.widget_5)
        self.horizontalLayout_2.setSpacing(5)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.horizontalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.statusShowLabel = CaptionLabel(self.widget_5)
        self.statusShowLabel.setObjectName(u"statusShowLabel")
        self.statusShowLabel.setMinimumSize(QSize(65, 0))
        self.statusShowLabel.setMaximumSize(QSize(65, 16777215))

        self.horizontalLayout_2.addWidget(self.statusShowLabel)

        self.IconInfoBadge = IconInfoBadge(self.widget_5)
        self.IconInfoBadge.setObjectName(u"IconInfoBadge")

        self.horizontalLayout_2.addWidget(self.IconInfoBadge)

        self.statusLabel = CaptionLabel(self.widget_5)
        self.statusLabel.setObjectName(u"statusLabel")

        self.horizontalLayout_2.addWidget(self.statusLabel)


        self.verticalLayout_2.addWidget(self.widget_5)

        self.IndeterminateProgressBar = IndeterminateProgressBar(self.importWidget)
        self.IndeterminateProgressBar.setObjectName(u"IndeterminateProgressBar")

        self.verticalLayout_2.addWidget(self.IndeterminateProgressBar)

        self.CardWidget = CardWidget(self.importWidget)
        self.CardWidget.setObjectName(u"CardWidget")
        self.horizontalLayout_11 = QHBoxLayout(self.CardWidget)
        self.horizontalLayout_11.setSpacing(20)
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.horizontalLayout_11.setContentsMargins(20, 20, 20, 20)
        self.partingIconWidget = IconWidget(self.CardWidget)
        self.partingIconWidget.setObjectName(u"partingIconWidget")
        self.partingIconWidget.setMinimumSize(QSize(20, 20))
        self.partingIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_11.addWidget(self.partingIconWidget)

        self.verticalLayout_7 = QVBoxLayout()
        self.verticalLayout_7.setSpacing(0)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.notesPathLabel_5 = QLabel(self.CardWidget)
        self.notesPathLabel_5.setObjectName(u"notesPathLabel_5")
        self.notesPathLabel_5.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_7.addWidget(self.notesPathLabel_5)

        self.CaptionLabel_5 = CaptionLabel(self.CardWidget)
        self.CaptionLabel_5.setObjectName(u"CaptionLabel_5")

        self.verticalLayout_7.addWidget(self.CaptionLabel_5)


        self.horizontalLayout_11.addLayout(self.verticalLayout_7)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_11.addItem(self.horizontalSpacer)

        self.editMarkPushButton = PushButton(self.CardWidget)
        self.editMarkPushButton.setObjectName(u"editMarkPushButton")
        self.editMarkPushButton.setMinimumSize(QSize(180, 0))
        self.editMarkPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_11.addWidget(self.editMarkPushButton)


        self.verticalLayout_2.addWidget(self.CardWidget)

        self.importCardWidget = CardWidget(self.importWidget)
        self.importCardWidget.setObjectName(u"importCardWidget")
        self.importCardWidget.setMinimumSize(QSize(0, 80))
        self.importCardWidget.setMaximumSize(QSize(16777215, 80))
        self.horizontalLayout = QHBoxLayout(self.importCardWidget)
        self.horizontalLayout.setSpacing(20)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(20, 20, 20, 20)
        self.fileIconWidget = IconWidget(self.importCardWidget)
        self.fileIconWidget.setObjectName(u"fileIconWidget")
        self.fileIconWidget.setMinimumSize(QSize(20, 20))
        self.fileIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout.addWidget(self.fileIconWidget)

        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.notesPathShowLabel = QLabel(self.importCardWidget)
        self.notesPathShowLabel.setObjectName(u"notesPathShowLabel")
        self.notesPathShowLabel.setStyleSheet(u"font: 700 9pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout.addWidget(self.notesPathShowLabel)

        self.notesPathLabel = CaptionLabel(self.importCardWidget)
        self.notesPathLabel.setObjectName(u"notesPathLabel")

        self.verticalLayout.addWidget(self.notesPathLabel)


        self.horizontalLayout.addLayout(self.verticalLayout)

        self.getFileButton = PrimarySplitPushButton(self.importCardWidget)
        self.getFileButton.setObjectName(u"getFileButton")
        self.getFileButton.setMinimumSize(QSize(180, 0))
        self.getFileButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout.addWidget(self.getFileButton)


        self.verticalLayout_2.addWidget(self.importCardWidget)


        self.verticalLayout_11.addWidget(self.importWidget)

        self.verticalLayout_6 = QVBoxLayout()
        self.verticalLayout_6.setSpacing(5)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.SubtitleLabel_2 = SubtitleLabel(self.scrollAreaWidgetContents_3)
        self.SubtitleLabel_2.setObjectName(u"SubtitleLabel_2")
        self.SubtitleLabel_2.setMinimumSize(QSize(0, 30))
        self.SubtitleLabel_2.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_6.addWidget(self.SubtitleLabel_2)

        self.horizontalLayout_9 = QHBoxLayout()
        self.horizontalLayout_9.setObjectName(u"horizontalLayout_9")
        self.playCardWidget = CardWidget(self.scrollAreaWidgetContents_3)
        self.playCardWidget.setObjectName(u"playCardWidget")
        self.playCardWidget.setMinimumSize(QSize(0, 120))
        self.playCardWidget.setMaximumSize(QSize(16777215, 120))
        self.verticalLayout_5 = QVBoxLayout(self.playCardWidget)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.verticalLayout_5.setContentsMargins(20, 20, 20, 20)
        self.horizontalLayout_8 = QHBoxLayout()
        self.horizontalLayout_8.setObjectName(u"horizontalLayout_8")
        self.currentStatusShowLabel = QLabel(self.playCardWidget)
        self.currentStatusShowLabel.setObjectName(u"currentStatusShowLabel")
        self.currentStatusShowLabel.setMinimumSize(QSize(50, 0))
        self.currentStatusShowLabel.setMaximumSize(QSize(50, 16777215))

        self.horizontalLayout_8.addWidget(self.currentStatusShowLabel)

        self.currentStatusLabel = QLabel(self.playCardWidget)
        self.currentStatusLabel.setObjectName(u"currentStatusLabel")

        self.horizontalLayout_8.addWidget(self.currentStatusLabel)


        self.verticalLayout_5.addLayout(self.horizontalLayout_8)

        self.horizontalLayout_7 = QHBoxLayout()
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.currentPageShowLabel = QLabel(self.playCardWidget)
        self.currentPageShowLabel.setObjectName(u"currentPageShowLabel")
        self.currentPageShowLabel.setMinimumSize(QSize(50, 0))
        self.currentPageShowLabel.setMaximumSize(QSize(50, 16777215))

        self.horizontalLayout_7.addWidget(self.currentPageShowLabel)

        self.currentPageLabel = QLabel(self.playCardWidget)
        self.currentPageLabel.setObjectName(u"currentPageLabel")

        self.horizontalLayout_7.addWidget(self.currentPageLabel)


        self.verticalLayout_5.addLayout(self.horizontalLayout_7)

        self.horizontalLayout_6 = QHBoxLayout()
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.currentIndexShowLabel = QLabel(self.playCardWidget)
        self.currentIndexShowLabel.setObjectName(u"currentIndexShowLabel")
        self.currentIndexShowLabel.setMinimumSize(QSize(50, 0))
        self.currentIndexShowLabel.setMaximumSize(QSize(50, 16777215))

        self.horizontalLayout_6.addWidget(self.currentIndexShowLabel)

        self.currentIndexLabel = QLabel(self.playCardWidget)
        self.currentIndexLabel.setObjectName(u"currentIndexLabel")

        self.horizontalLayout_6.addWidget(self.currentIndexLabel)


        self.verticalLayout_5.addLayout(self.horizontalLayout_6)


        self.horizontalLayout_9.addWidget(self.playCardWidget)

        self.playCardWidget_2 = CardWidget(self.scrollAreaWidgetContents_3)
        self.playCardWidget_2.setObjectName(u"playCardWidget_2")
        self.playCardWidget_2.setMinimumSize(QSize(0, 120))
        self.playCardWidget_2.setMaximumSize(QSize(16777215, 120))
        self.verticalLayout_10 = QVBoxLayout(self.playCardWidget_2)
        self.verticalLayout_10.setObjectName(u"verticalLayout_10")
        self.verticalLayout_10.setContentsMargins(20, 20, 20, 20)
        self.horizontalLayout_10 = QHBoxLayout()
        self.horizontalLayout_10.setSpacing(5)
        self.horizontalLayout_10.setObjectName(u"horizontalLayout_10")
        self.pageJumpShowLabel = QLabel(self.playCardWidget_2)
        self.pageJumpShowLabel.setObjectName(u"pageJumpShowLabel")
        self.pageJumpShowLabel.setMinimumSize(QSize(90, 0))
        self.pageJumpShowLabel.setMaximumSize(QSize(90, 16777215))

        self.horizontalLayout_10.addWidget(self.pageJumpShowLabel)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_10.addItem(self.horizontalSpacer_2)

        self.pageJumpSpinBox = SpinBox(self.playCardWidget_2)
        self.pageJumpSpinBox.setObjectName(u"pageJumpSpinBox")
        self.pageJumpSpinBox.setMinimumSize(QSize(135, 33))
        self.pageJumpSpinBox.setMaximumSize(QSize(135, 33))
        self.pageJumpSpinBox.setMinimum(1)

        self.horizontalLayout_10.addWidget(self.pageJumpSpinBox)

        self.pageJumpToolButton = ToolButton(self.playCardWidget_2)
        self.pageJumpToolButton.setObjectName(u"pageJumpToolButton")
        self.pageJumpToolButton.setMinimumSize(QSize(40, 33))
        self.pageJumpToolButton.setMaximumSize(QSize(40, 33))
        self.pageJumpToolButton.setIconSize(QSize(10, 10))

        self.horizontalLayout_10.addWidget(self.pageJumpToolButton)


        self.verticalLayout_10.addLayout(self.horizontalLayout_10)

        self.horizontalLayout_12 = QHBoxLayout()
        self.horizontalLayout_12.setSpacing(10)
        self.horizontalLayout_12.setObjectName(u"horizontalLayout_12")
        self.infoShowLabel = QLabel(self.playCardWidget_2)
        self.infoShowLabel.setObjectName(u"infoShowLabel")
        self.infoShowLabel.setMinimumSize(QSize(90, 0))
        self.infoShowLabel.setMaximumSize(QSize(90, 16777215))

        self.horizontalLayout_12.addWidget(self.infoShowLabel)

        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_12.addItem(self.horizontalSpacer_3)

        self.infoPushButton = PushButton(self.playCardWidget_2)
        self.infoPushButton.setObjectName(u"infoPushButton")
        self.infoPushButton.setMinimumSize(QSize(180, 0))
        self.infoPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_12.addWidget(self.infoPushButton)


        self.verticalLayout_10.addLayout(self.horizontalLayout_12)


        self.horizontalLayout_9.addWidget(self.playCardWidget_2)

        self.horizontalLayout_9.setStretch(0, 1)
        self.horizontalLayout_9.setStretch(1, 1)

        self.verticalLayout_6.addLayout(self.horizontalLayout_9)

        self.playCardWidget_3 = CardWidget(self.scrollAreaWidgetContents_3)
        self.playCardWidget_3.setObjectName(u"playCardWidget_3")
        self.playCardWidget_3.setMinimumSize(QSize(0, 80))
        self.playCardWidget_3.setMaximumSize(QSize(16777215, 80))
        self.horizontalLayout_15 = QHBoxLayout(self.playCardWidget_3)
        self.horizontalLayout_15.setSpacing(20)
        self.horizontalLayout_15.setObjectName(u"horizontalLayout_15")
        self.horizontalLayout_15.setContentsMargins(20, 18, 20, 18)
        self.horizontalLayout_5 = QHBoxLayout()
        self.horizontalLayout_5.setSpacing(10)
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.playButton = PushButton(self.playCardWidget_3)
        self.playButton.setObjectName(u"playButton")
        self.playButton.setMinimumSize(QSize(80, 40))
        self.playButton.setMaximumSize(QSize(80, 40))

        self.horizontalLayout_5.addWidget(self.playButton)

        self.stopButton = PushButton(self.playCardWidget_3)
        self.stopButton.setObjectName(u"stopButton")
        self.stopButton.setMinimumSize(QSize(80, 40))
        self.stopButton.setMaximumSize(QSize(80, 40))

        self.horizontalLayout_5.addWidget(self.stopButton)

        self.resetButton = PushButton(self.playCardWidget_3)
        self.resetButton.setObjectName(u"resetButton")
        self.resetButton.setMinimumSize(QSize(80, 40))
        self.resetButton.setMaximumSize(QSize(80, 40))

        self.horizontalLayout_5.addWidget(self.resetButton)

        self.scrollEnableSwitch = CheckBox(self.playCardWidget_3)
        self.scrollEnableSwitch.setObjectName(u"scrollEnableSwitch")

        self.horizontalLayout_5.addWidget(self.scrollEnableSwitch)


        self.horizontalLayout_15.addLayout(self.horizontalLayout_5)


        self.verticalLayout_6.addWidget(self.playCardWidget_3)

        self.currentCardWidget = CardWidget(self.scrollAreaWidgetContents_3)
        self.currentCardWidget.setObjectName(u"currentCardWidget")
        self.currentCardWidget.setMinimumSize(QSize(0, 130))
        self.currentCardWidget.setMaximumSize(QSize(16777215, 130))
        self.verticalLayout_4 = QVBoxLayout(self.currentCardWidget)
        self.verticalLayout_4.setSpacing(20)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.verticalLayout_4.setContentsMargins(20, 20, 20, 20)
        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.currentIconWidget = IconWidget(self.currentCardWidget)
        self.currentIconWidget.setObjectName(u"currentIconWidget")
        self.currentIconWidget.setMinimumSize(QSize(20, 20))
        self.currentIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_4.addWidget(self.currentIconWidget)

        self.verticalLayout_3 = QVBoxLayout()
        self.verticalLayout_3.setSpacing(0)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.currentLabel = QLabel(self.currentCardWidget)
        self.currentLabel.setObjectName(u"currentLabel")
        self.currentLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_3.addWidget(self.currentLabel)

        self.CaptionLabel_4 = CaptionLabel(self.currentCardWidget)
        self.CaptionLabel_4.setObjectName(u"CaptionLabel_4")

        self.verticalLayout_3.addWidget(self.CaptionLabel_4)


        self.horizontalLayout_4.addLayout(self.verticalLayout_3)

        self.currentSwitch = SwitchButton(self.currentCardWidget)
        self.currentSwitch.setObjectName(u"currentSwitch")
        self.currentSwitch.setMinimumSize(QSize(100, 22))
        self.currentSwitch.setMaximumSize(QSize(100, 22))
        self.currentSwitch.setChecked(True)

        self.horizontalLayout_4.addWidget(self.currentSwitch)


        self.verticalLayout_4.addLayout(self.horizontalLayout_4)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.currentTimeIconWidget = IconWidget(self.currentCardWidget)
        self.currentTimeIconWidget.setObjectName(u"currentTimeIconWidget")
        self.currentTimeIconWidget.setMinimumSize(QSize(20, 20))
        self.currentTimeIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_3.addWidget(self.currentTimeIconWidget)

        self.verticalLayout_9 = QVBoxLayout()
        self.verticalLayout_9.setSpacing(0)
        self.verticalLayout_9.setObjectName(u"verticalLayout_9")
        self.currentLabel_2 = QLabel(self.currentCardWidget)
        self.currentLabel_2.setObjectName(u"currentLabel_2")
        self.currentLabel_2.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_9.addWidget(self.currentLabel_2)

        self.CaptionLabel_6 = CaptionLabel(self.currentCardWidget)
        self.CaptionLabel_6.setObjectName(u"CaptionLabel_6")

        self.verticalLayout_9.addWidget(self.CaptionLabel_6)


        self.horizontalLayout_3.addLayout(self.verticalLayout_9)

        self.currentSpinBox = SpinBox(self.currentCardWidget)
        self.currentSpinBox.setObjectName(u"currentSpinBox")
        self.currentSpinBox.setMaximumSize(QSize(120, 33))
        self.currentSpinBox.setMaximum(30)
        self.currentSpinBox.setValue(5)

        self.horizontalLayout_3.addWidget(self.currentSpinBox)


        self.verticalLayout_4.addLayout(self.horizontalLayout_3)


        self.verticalLayout_6.addWidget(self.currentCardWidget)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.verticalLayout_6.addItem(self.verticalSpacer)


        self.verticalLayout_11.addLayout(self.verticalLayout_6)

        self.bgScrollArea.setWidget(self.scrollAreaWidgetContents_3)

        self.horizontalLayout_13.addWidget(self.bgScrollArea)


        self.retranslateUi(mainwindow)
        self.currentSwitch.checkedChanged.connect(self.currentSpinBox.setEnabled)

        QMetaObject.connectSlotsByName(mainwindow)
    # setupUi

    def retranslateUi(self, mainwindow):
        mainwindow.setWindowTitle(QCoreApplication.translate("mainwindow", u"Form", None))
        self.SubtitleLabel.setText(QCoreApplication.translate("mainwindow", u"\u6f14\u8bb2\u7a3f\u5bfc\u5165", None))
        self.statusShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u5f53\u524d\u72b6\u6001\uff1a", None))
        self.statusLabel.setText(QCoreApplication.translate("mainwindow", u"\u672a\u5bfc\u5165", None))
        self.notesPathLabel_5.setText(QCoreApplication.translate("mainwindow", u"\u9875\u5185\u5206\u9694\u7b26\u8bbe\u7f6e", None))
        self.CaptionLabel_5.setText(QCoreApplication.translate("mainwindow", u"\u5f53\u4e00\u9875\u5185\u542b\u6709\u70b9\u51fb\u7279\u6548\u65f6\uff0c\u4f7f\u7528\u7b26\u53f7\u5206\u9694\u6f14\u8bb2\u7a3f", None))
        self.editMarkPushButton.setText(QCoreApplication.translate("mainwindow", u"\u7f16\u8f91\u5206\u9694\u7b26", None))
        self.notesPathShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u6587\u4ef6\u8def\u5f84", None))
        self.notesPathLabel.setText(QCoreApplication.translate("mainwindow", u"\u6682\u65e0", None))
        self.getFileButton.setProperty("text_", QCoreApplication.translate("mainwindow", u"\u5bfc\u5165 PowerPoint", None))
        self.SubtitleLabel_2.setText(QCoreApplication.translate("mainwindow", u"\u64ad\u653e\u63a7\u5236", None))
        self.currentStatusShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u72b6\u6001\uff1a", None))
        self.currentStatusLabel.setText(QCoreApplication.translate("mainwindow", u"\u505c\u6b62", None))
        self.currentPageShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u9875\u7801\uff1a", None))
        self.currentPageLabel.setText(QCoreApplication.translate("mainwindow", u"1 / 1", None))
        self.currentIndexShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u5e8f\u53f7\uff1a", None))
        self.currentIndexLabel.setText(QCoreApplication.translate("mainwindow", u"1 / 1", None))
        self.pageJumpShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u64ad\u653e\u9875\u7801\u8df3\u8f6c", None))
        self.infoShowLabel.setText(QCoreApplication.translate("mainwindow", u"\u8bb2\u7a3f\u4fe1\u606f\u7edf\u8ba1", None))
        self.infoPushButton.setText(QCoreApplication.translate("mainwindow", u"\u4fe1\u606f\u7edf\u8ba1", None))
        self.playButton.setText(QCoreApplication.translate("mainwindow", u"\u64ad\u653e", None))
        self.stopButton.setText(QCoreApplication.translate("mainwindow", u"\u505c\u6b62", None))
        self.resetButton.setText(QCoreApplication.translate("mainwindow", u"\u91cd\u7f6e", None))
        self.scrollEnableSwitch.setText(QCoreApplication.translate("mainwindow", u"\u542f\u7528 PowerPoint \u540c\u6b65\u7ffb\u9875", None))
        self.currentLabel.setText(QCoreApplication.translate("mainwindow", u"\u5012\u8ba1\u65f6\u64ad\u653e", None))
        self.CaptionLabel_4.setText(QCoreApplication.translate("mainwindow", u"\u5728\u64ad\u653e\u524d\u63d2\u5165\u4e00\u6bb5\u5012\u8ba1\u65f6", None))
        self.currentSwitch.setOnText(QCoreApplication.translate("mainwindow", u"\u542f\u7528", None))
        self.currentSwitch.setOffText(QCoreApplication.translate("mainwindow", u"\u505c\u7528", None))
        self.currentLabel_2.setText(QCoreApplication.translate("mainwindow", u"\u65f6\u957f\u8bbe\u7f6e", None))
        self.CaptionLabel_6.setText(QCoreApplication.translate("mainwindow", u"\u8c03\u6574\u5012\u8ba1\u65f6\u65f6\u957f\u79d2\u6570", None))
    # retranslateUi

