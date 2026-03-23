# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'settingInterface.ui'
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
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QSizePolicy,
    QSpacerItem, QVBoxLayout, QWidget)

from qfluentwidgets import (CaptionLabel, CardWidget, ComboBox, DoubleSpinBox,
    IconWidget, PrimaryPushButton, PushButton, ScrollArea,
    SpinBox, SubtitleLabel, ToolButton, TransparentToolButton)
import resource_rc

class Ui_settingInterface(object):
    def setupUi(self, settingInterface):
        if not settingInterface.objectName():
            settingInterface.setObjectName(u"settingInterface")
        settingInterface.resize(850, 720)
        self.horizontalLayout_3 = QHBoxLayout(settingInterface)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.bgScrollArea = ScrollArea(settingInterface)
        self.bgScrollArea.setObjectName(u"bgScrollArea")
        self.bgScrollArea.setWidgetResizable(True)
        self.scrollAreaWidgetContents = QWidget()
        self.scrollAreaWidgetContents.setObjectName(u"scrollAreaWidgetContents")
        self.scrollAreaWidgetContents.setGeometry(QRect(0, 0, 830, 700))
        self.scrollAreaWidgetContents.setStyleSheet(u"background: transparent;")
        self.verticalLayout_4 = QVBoxLayout(self.scrollAreaWidgetContents)
        self.verticalLayout_4.setSpacing(6)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.verticalLayout_4.setContentsMargins(20, 20, 20, 20)
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

        self.CardWidget_5 = CardWidget(self.importWidget)
        self.CardWidget_5.setObjectName(u"CardWidget_5")
        self.horizontalLayout_14 = QHBoxLayout(self.CardWidget_5)
        self.horizontalLayout_14.setObjectName(u"horizontalLayout_14")
        self.horizontalLayout_14.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_11 = QVBoxLayout()
        self.verticalLayout_11.setSpacing(0)
        self.verticalLayout_11.setObjectName(u"verticalLayout_11")
        self.engineSelectPathLabel = QLabel(self.CardWidget_5)
        self.engineSelectPathLabel.setObjectName(u"engineSelectPathLabel")
        self.engineSelectPathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_11.addWidget(self.engineSelectPathLabel)

        self.engineSelectCaptionLabel = CaptionLabel(self.CardWidget_5)
        self.engineSelectCaptionLabel.setObjectName(u"engineSelectCaptionLabel")

        self.verticalLayout_11.addWidget(self.engineSelectCaptionLabel)


        self.horizontalLayout_14.addLayout(self.verticalLayout_11)

        self.horizontalSpacer_4 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_14.addItem(self.horizontalSpacer_4)

        self.engineSelectComboBox = ComboBox(self.CardWidget_5)
        self.engineSelectComboBox.setObjectName(u"engineSelectComboBox")
        self.engineSelectComboBox.setMinimumSize(QSize(240, 0))
        self.engineSelectComboBox.setMaximumSize(QSize(240, 16777215))

        self.horizontalLayout_14.addWidget(self.engineSelectComboBox)


        self.verticalLayout_2.addWidget(self.CardWidget_5)

        self.CardWidget_4 = CardWidget(self.importWidget)
        self.CardWidget_4.setObjectName(u"CardWidget_4")
        self.horizontalLayout_15 = QHBoxLayout(self.CardWidget_4)
        self.horizontalLayout_15.setObjectName(u"horizontalLayout_15")
        self.horizontalLayout_15.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_12 = QVBoxLayout()
        self.verticalLayout_12.setSpacing(0)
        self.verticalLayout_12.setObjectName(u"verticalLayout_12")
        self.savePathLabel = QLabel(self.CardWidget_4)
        self.savePathLabel.setObjectName(u"savePathLabel")
        self.savePathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_12.addWidget(self.savePathLabel)

        self.saveCaptionLabel = CaptionLabel(self.CardWidget_4)
        self.saveCaptionLabel.setObjectName(u"saveCaptionLabel")

        self.verticalLayout_12.addWidget(self.saveCaptionLabel)


        self.horizontalLayout_15.addLayout(self.verticalLayout_12)

        self.horizontalSpacer_5 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_15.addItem(self.horizontalSpacer_5)

        self.previewButton = PushButton(self.CardWidget_4)
        self.previewButton.setObjectName(u"previewButton")
        self.previewButton.setMinimumSize(QSize(100, 0))
        self.previewButton.setMaximumSize(QSize(100, 16777215))

        self.horizontalLayout_15.addWidget(self.previewButton)

        self.savePushButton = PushButton(self.CardWidget_4)
        self.savePushButton.setObjectName(u"savePushButton")
        self.savePushButton.setMinimumSize(QSize(180, 0))
        self.savePushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_15.addWidget(self.savePushButton)


        self.verticalLayout_2.addWidget(self.CardWidget_4)


        self.verticalLayout_4.addWidget(self.importWidget)

        self.verticalLayout_6 = QVBoxLayout()
        self.verticalLayout_6.setSpacing(5)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.SubtitleLabel_2 = SubtitleLabel(self.scrollAreaWidgetContents)
        self.SubtitleLabel_2.setObjectName(u"SubtitleLabel_2")
        self.SubtitleLabel_2.setMinimumSize(QSize(0, 30))
        self.SubtitleLabel_2.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_6.addWidget(self.SubtitleLabel_2)

        self.importCardWidget_2 = CardWidget(self.scrollAreaWidgetContents)
        self.importCardWidget_2.setObjectName(u"importCardWidget_2")
        self.importCardWidget_2.setMinimumSize(QSize(0, 80))
        self.importCardWidget_2.setMaximumSize(QSize(16777215, 80))
        self.horizontalLayout_2 = QHBoxLayout(self.importCardWidget_2)
        self.horizontalLayout_2.setSpacing(20)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.horizontalLayout_2.setContentsMargins(20, 20, 20, 20)
        self.versionIconWidget = IconWidget(self.importCardWidget_2)
        self.versionIconWidget.setObjectName(u"versionIconWidget")
        self.versionIconWidget.setMinimumSize(QSize(20, 20))
        self.versionIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout_2.addWidget(self.versionIconWidget)

        self.verticalLayout_3 = QVBoxLayout()
        self.verticalLayout_3.setSpacing(0)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.versionShowLabel = QLabel(self.importCardWidget_2)
        self.versionShowLabel.setObjectName(u"versionShowLabel")
        self.versionShowLabel.setStyleSheet(u"font: 700 9pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_3.addWidget(self.versionShowLabel)

        self.versionLabel = CaptionLabel(self.importCardWidget_2)
        self.versionLabel.setObjectName(u"versionLabel")

        self.verticalLayout_3.addWidget(self.versionLabel)


        self.horizontalLayout_2.addLayout(self.verticalLayout_3)

        self.versionPrimaryPushButton = PrimaryPushButton(self.importCardWidget_2)
        self.versionPrimaryPushButton.setObjectName(u"versionPrimaryPushButton")
        self.versionPrimaryPushButton.setMinimumSize(QSize(180, 0))
        self.versionPrimaryPushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_2.addWidget(self.versionPrimaryPushButton)


        self.verticalLayout_6.addWidget(self.importCardWidget_2)

        self.importCardWidget = CardWidget(self.scrollAreaWidgetContents)
        self.importCardWidget.setObjectName(u"importCardWidget")
        self.importCardWidget.setMinimumSize(QSize(0, 80))
        self.importCardWidget.setMaximumSize(QSize(16777215, 80))
        self.horizontalLayout = QHBoxLayout(self.importCardWidget)
        self.horizontalLayout.setSpacing(20)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(20, 20, 20, 20)
        self.copyrightIconWidget = IconWidget(self.importCardWidget)
        self.copyrightIconWidget.setObjectName(u"copyrightIconWidget")
        self.copyrightIconWidget.setMinimumSize(QSize(20, 20))
        self.copyrightIconWidget.setMaximumSize(QSize(20, 20))

        self.horizontalLayout.addWidget(self.copyrightIconWidget)

        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.copyrightShowLabel = QLabel(self.importCardWidget)
        self.copyrightShowLabel.setObjectName(u"copyrightShowLabel")
        self.copyrightShowLabel.setStyleSheet(u"font: 700 9pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout.addWidget(self.copyrightShowLabel)

        self.copyrightLabel = CaptionLabel(self.importCardWidget)
        self.copyrightLabel.setObjectName(u"copyrightLabel")

        self.verticalLayout.addWidget(self.copyrightLabel)


        self.horizontalLayout.addLayout(self.verticalLayout)

        self.githubButton = TransparentToolButton(self.importCardWidget)
        self.githubButton.setObjectName(u"githubButton")

        self.horizontalLayout.addWidget(self.githubButton)

        self.giteeButton = TransparentToolButton(self.importCardWidget)
        self.giteeButton.setObjectName(u"giteeButton")

        self.horizontalLayout.addWidget(self.giteeButton)


        self.verticalLayout_6.addWidget(self.importCardWidget)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout_6.addItem(self.verticalSpacer)


        self.verticalLayout_4.addLayout(self.verticalLayout_6)

        self.bgScrollArea.setWidget(self.scrollAreaWidgetContents)

        self.horizontalLayout_3.addWidget(self.bgScrollArea)


        self.retranslateUi(settingInterface)

        QMetaObject.connectSlotsByName(settingInterface)
    # setupUi

    def retranslateUi(self, settingInterface):
        settingInterface.setWindowTitle(QCoreApplication.translate("settingInterface", u"Form", None))
        self.SubtitleLabel.setText(QCoreApplication.translate("settingInterface", u"TTS \u5f15\u64ce\u8bbe\u7f6e", None))
        self.engineSelectPathLabel.setText(QCoreApplication.translate("settingInterface", u"\u5f15\u64ce\u9009\u62e9", None))
        self.engineSelectCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u9009\u62e9\u672c\u5730 TTS \u5f15\u64ce\u6216\u5728\u7ebf\u5f15\u64ce", None))
        self.savePathLabel.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u8bbe\u7f6e", None))
        self.saveCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u540e\uff0c\u9700\u8981\u91cd\u65b0\u5bfc\u5165\u6587\u4ef6\u624d\u80fd\u751f\u6548", None))
        self.previewButton.setText(QCoreApplication.translate("settingInterface", u"\u8bd5\u542c", None))
        self.savePushButton.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u8bbe\u7f6e", None))
        self.SubtitleLabel_2.setText(QCoreApplication.translate("settingInterface", u"\u5173\u4e8e", None))
        self.versionShowLabel.setText(QCoreApplication.translate("settingInterface", u"\u5f53\u524d\u7248\u672c", None))
        self.versionLabel.setText(QCoreApplication.translate("settingInterface", u"0.0.0", None))
        self.versionPrimaryPushButton.setText(QCoreApplication.translate("settingInterface", u"\u68c0\u67e5\u66f4\u65b0", None))
        self.copyrightShowLabel.setText(QCoreApplication.translate("settingInterface", u"\u7248\u6743", None))
        self.copyrightLabel.setText(QCoreApplication.translate("settingInterface", u"Copyright \u00a9 2023-2026 Nagisa", None))
    # retranslateUi

