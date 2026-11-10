"""首页与审听播放器共用的圆形控制按钮。"""

from PySide6.QtCore import QSize
from qfluentwidgets import (
    FluentIcon,
    PrimaryToolButton,
    Theme,
    TransparentToolButton,
    setCustomStyleSheet,
)

from ui.icons import SystemIcon


class RoundPlaybackButton(TransparentToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setIconSize(QSize(18, 18))
        setCustomStyleSheet(self, self._style('#f3f3f3', '#e9e9e9', '#e1e1e1'),
                            self._style('#363636', '#404040', '#484848'))

    @staticmethod
    def _style(rest, hover, pressed):
        return (
            'TransparentToolButton, TransparentToolButton:checked {'
            f'background-color: {rest}; border-radius: 18px; }}'
            f'TransparentToolButton:hover {{ background-color: {hover}; }}'
            f'TransparentToolButton:pressed {{ background-color: {pressed}; }}'
            f'TransparentToolButton:disabled {{ background-color: {rest}; }}'
        )


class PlayButton(PrimaryToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(SystemIcon.PLAY.icon(Theme.DARK))
        self.setFixedSize(44, 44)
        self.setIconSize(QSize(18, 18))
        rounded = 'PrimaryToolButton { border-radius: 22px; }'
        setCustomStyleSheet(self, rounded, rounded)


class StepPlaybackButton(RoundPlaybackButton):
    def __init__(self, backward, parent=None):
        super().__init__(parent)
        self.setIcon(FluentIcon.CHEVRON_RIGHT)
        self.backward = backward
        self.setIconSize(QSize(14, 14))

    def _drawIcon(self, icon, painter, rect, state=None):
        painter.save()
        if self.backward:
            painter.translate(2 * rect.center().x(), 0)
            painter.scale(-1, 1)
        super()._drawIcon(icon, painter, rect)
        painter.restore()
