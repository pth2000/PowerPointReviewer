# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'Ui_mainwindow.ui'
##
## Created by: Qt User Interface Compiler version 6.11.2
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

from qfluentwidgets import (CaptionLabel, CardWidget, IconInfoBadge, IconWidget,
    IndeterminateProgressBar, InfoBadge, PrimarySplitPushButton, PushButton,
    ScrollArea, SplitPushButton, SubtitleLabel)
from ui.playback_controls import PlaybackControls
from ui.playback_overview import PlaybackOverview
import resource_rc

class Ui_mainwindow(object):
    def setupUi(self, mainwindow):
        if not mainwindow.objectName():
            mainwindow.setObjectName(u"mainwindow")
        mainwindow.resize(850, 720)
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
        self.verticalLayout_11.setSpacing(20)
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
        self.notesPathLabel_5.setStyleSheet(u"font-weight: 700;\n"
"font-size: 10pt;")

        self.verticalLayout_7.addWidget(self.notesPathLabel_5)

        self.CaptionLabel_5 = CaptionLabel(self.CardWidget)
        self.CaptionLabel_5.setObjectName(u"CaptionLabel_5")

        self.verticalLayout_7.addWidget(self.CaptionLabel_5)


        self.horizontalLayout_11.addLayout(self.verticalLayout_7)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

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
        self.notesPathShowLabel.setStyleSheet(u"font-weight: 700;\n"
"font-size: 10pt;")

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

        self.playbackOverview = PlaybackOverview(self.scrollAreaWidgetContents_3)
        self.playbackOverview.setObjectName(u"playbackOverview")

        self.verticalLayout_6.addWidget(self.playbackOverview)

        self.playCardWidget_3 = PlaybackControls(self.scrollAreaWidgetContents_3)
        self.playCardWidget_3.setObjectName(u"playCardWidget_3")

        self.verticalLayout_6.addWidget(self.playCardWidget_3)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout_6.addItem(self.verticalSpacer)


        self.verticalLayout_11.addLayout(self.verticalLayout_6)

        self.bgScrollArea.setWidget(self.scrollAreaWidgetContents_3)

        self.horizontalLayout_13.addWidget(self.bgScrollArea)


        self.retranslateUi(mainwindow)

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
        self.getFileButton.setProperty(u"text_", QCoreApplication.translate("mainwindow", u"\u5bfc\u5165 PowerPoint", None))
        self.SubtitleLabel_2.setText(QCoreApplication.translate("mainwindow", u"\u64ad\u653e\u63a7\u5236", None))
    # retranslateUi

