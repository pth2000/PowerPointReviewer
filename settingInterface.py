# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'settingInterface.ui'
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

from qfluentwidgets import (CaptionLabel, CardWidget, ComboBox, DoubleSpinBox,
    IconWidget, PrimaryPushButton, PushButton, SpinBox,
    SubtitleLabel, ToolButton, TransparentToolButton)
import resource_rc

class Ui_settingInterface(object):
    def setupUi(self, settingInterface):
        if not settingInterface.objectName():
            settingInterface.setObjectName(u"settingInterface")
        settingInterface.resize(858, 692)
        self.horizontalLayout_3 = QHBoxLayout(settingInterface)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.bgWidget = QWidget(settingInterface)
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

        self.CardWidget = CardWidget(self.importWidget)
        self.CardWidget.setObjectName(u"CardWidget")
        self.horizontalLayout_11 = QHBoxLayout(self.CardWidget)
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.horizontalLayout_11.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_7 = QVBoxLayout()
        self.verticalLayout_7.setSpacing(0)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.enginePathLabel = QLabel(self.CardWidget)
        self.enginePathLabel.setObjectName(u"enginePathLabel")
        self.enginePathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_7.addWidget(self.enginePathLabel)

        self.engineCaptionLabel = CaptionLabel(self.CardWidget)
        self.engineCaptionLabel.setObjectName(u"engineCaptionLabel")

        self.verticalLayout_7.addWidget(self.engineCaptionLabel)


        self.horizontalLayout_11.addLayout(self.verticalLayout_7)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_11.addItem(self.horizontalSpacer)

        self.engineComboBox = ComboBox(self.CardWidget)
        self.engineComboBox.setObjectName(u"engineComboBox")
        self.engineComboBox.setMinimumSize(QSize(240, 0))
        self.engineComboBox.setMaximumSize(QSize(240, 16777215))

        self.horizontalLayout_11.addWidget(self.engineComboBox)


        self.verticalLayout_2.addWidget(self.CardWidget)

        self.CardWidget_2 = CardWidget(self.importWidget)
        self.CardWidget_2.setObjectName(u"CardWidget_2")
        self.horizontalLayout_12 = QHBoxLayout(self.CardWidget_2)
        self.horizontalLayout_12.setObjectName(u"horizontalLayout_12")
        self.horizontalLayout_12.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_9 = QVBoxLayout()
        self.verticalLayout_9.setSpacing(0)
        self.verticalLayout_9.setObjectName(u"verticalLayout_9")
        self.ratePathLabel = QLabel(self.CardWidget_2)
        self.ratePathLabel.setObjectName(u"ratePathLabel")
        self.ratePathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_9.addWidget(self.ratePathLabel)

        self.rateCaptionLabel = CaptionLabel(self.CardWidget_2)
        self.rateCaptionLabel.setObjectName(u"rateCaptionLabel")

        self.verticalLayout_9.addWidget(self.rateCaptionLabel)


        self.horizontalLayout_12.addLayout(self.verticalLayout_9)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_12.addItem(self.horizontalSpacer_2)

        self.rateSpinBox = SpinBox(self.CardWidget_2)
        self.rateSpinBox.setObjectName(u"rateSpinBox")
        self.rateSpinBox.setMinimumSize(QSize(180, 33))
        self.rateSpinBox.setMaximumSize(QSize(180, 33))
        self.rateSpinBox.setMaximum(500)
        self.rateSpinBox.setValue(200)

        self.horizontalLayout_12.addWidget(self.rateSpinBox)


        self.verticalLayout_2.addWidget(self.CardWidget_2)

        self.CardWidget_3 = CardWidget(self.importWidget)
        self.CardWidget_3.setObjectName(u"CardWidget_3")
        self.horizontalLayout_13 = QHBoxLayout(self.CardWidget_3)
        self.horizontalLayout_13.setObjectName(u"horizontalLayout_13")
        self.horizontalLayout_13.setContentsMargins(20, 20, 20, 20)
        self.verticalLayout_10 = QVBoxLayout()
        self.verticalLayout_10.setSpacing(0)
        self.verticalLayout_10.setObjectName(u"verticalLayout_10")
        self.volumePathLabel = QLabel(self.CardWidget_3)
        self.volumePathLabel.setObjectName(u"volumePathLabel")
        self.volumePathLabel.setStyleSheet(u"font: 700 10pt 'Segoe UI', 'Microsoft YaHei', 'PingFang SC';")

        self.verticalLayout_10.addWidget(self.volumePathLabel)

        self.volumeCaptionLabel = CaptionLabel(self.CardWidget_3)
        self.volumeCaptionLabel.setObjectName(u"volumeCaptionLabel")

        self.verticalLayout_10.addWidget(self.volumeCaptionLabel)


        self.horizontalLayout_13.addLayout(self.verticalLayout_10)

        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_13.addItem(self.horizontalSpacer_3)

        self.volumeDoubleSpinBox = DoubleSpinBox(self.CardWidget_3)
        self.volumeDoubleSpinBox.setObjectName(u"volumeDoubleSpinBox")
        self.volumeDoubleSpinBox.setMinimumSize(QSize(180, 33))
        self.volumeDoubleSpinBox.setMaximumSize(QSize(180, 33))
        self.volumeDoubleSpinBox.setMaximum(1.000000000000000)
        self.volumeDoubleSpinBox.setSingleStep(0.010000000000000)
        self.volumeDoubleSpinBox.setValue(1.000000000000000)

        self.horizontalLayout_13.addWidget(self.volumeDoubleSpinBox)


        self.verticalLayout_2.addWidget(self.CardWidget_3)


        self.verticalLayout_8.addWidget(self.importWidget)

        self.CardWidget_4 = CardWidget(self.bgWidget)
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

        self.horizontalSpacer_5 = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_15.addItem(self.horizontalSpacer_5)

        self.savePushButton = PushButton(self.CardWidget_4)
        self.savePushButton.setObjectName(u"savePushButton")
        self.savePushButton.setMinimumSize(QSize(180, 0))
        self.savePushButton.setMaximumSize(QSize(180, 16777215))

        self.horizontalLayout_15.addWidget(self.savePushButton)


        self.verticalLayout_8.addWidget(self.CardWidget_4)

        self.verticalLayout_6 = QVBoxLayout()
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.SubtitleLabel_2 = SubtitleLabel(self.bgWidget)
        self.SubtitleLabel_2.setObjectName(u"SubtitleLabel_2")
        self.SubtitleLabel_2.setMinimumSize(QSize(0, 30))
        self.SubtitleLabel_2.setMaximumSize(QSize(16777215, 30))

        self.verticalLayout_6.addWidget(self.SubtitleLabel_2)

        self.importCardWidget_2 = CardWidget(self.bgWidget)
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

        self.importCardWidget = CardWidget(self.bgWidget)
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

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)

        self.verticalLayout_6.addItem(self.verticalSpacer)


        self.verticalLayout_8.addLayout(self.verticalLayout_6)


        self.horizontalLayout_3.addWidget(self.bgWidget)


        self.retranslateUi(settingInterface)

        QMetaObject.connectSlotsByName(settingInterface)
    # setupUi

    def retranslateUi(self, settingInterface):
        settingInterface.setWindowTitle(QCoreApplication.translate("settingInterface", u"Form", None))
        self.SubtitleLabel.setText(QCoreApplication.translate("settingInterface", u"TTS \u5f15\u64ce\u8bbe\u7f6e", None))
        self.enginePathLabel.setText(QCoreApplication.translate("settingInterface", u"\u5f15\u64ce\u9009\u62e9", None))
        self.engineCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u9009\u62e9\u53ef\u7528\u7684 TTS \u5f15\u64ce", None))
        self.ratePathLabel.setText(QCoreApplication.translate("settingInterface", u"\u8bed\u901f\u8bbe\u7f6e", None))
        self.rateCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u8c03\u8282\u53d1\u58f0\u901f\u5ea6\uff0c\u9ed8\u8ba4\u4e3a 200", None))
        self.volumePathLabel.setText(QCoreApplication.translate("settingInterface", u"\u97f3\u91cf\u8bbe\u7f6e", None))
        self.volumeCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u8c03\u8282\u97f3\u91cf\uff0c\u9ed8\u8ba4\u4e3a 1.0", None))
        self.savePathLabel.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u8bbe\u7f6e", None))
        self.saveCaptionLabel.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u540e\uff0c\u9700\u8981\u91cd\u65b0\u5bfc\u5165\u6587\u4ef6\u624d\u80fd\u751f\u6548", None))
        self.savePushButton.setText(QCoreApplication.translate("settingInterface", u"\u4fdd\u5b58\u8bbe\u7f6e", None))
        self.SubtitleLabel_2.setText(QCoreApplication.translate("settingInterface", u"\u5173\u4e8e", None))
        self.versionShowLabel.setText(QCoreApplication.translate("settingInterface", u"\u5f53\u524d\u7248\u672c", None))
        self.versionLabel.setText(QCoreApplication.translate("settingInterface", u"0.0.0", None))
        self.versionPrimaryPushButton.setText(QCoreApplication.translate("settingInterface", u"\u68c0\u67e5\u66f4\u65b0", None))
        self.copyrightShowLabel.setText(QCoreApplication.translate("settingInterface", u"\u7248\u6743", None))
        self.copyrightLabel.setText(QCoreApplication.translate("settingInterface", u"Copyright \u00a9 2023 Nagisa", None))
    # retranslateUi

