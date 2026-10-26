"""加载可随明暗主题重新着色的 SVG 资源图标。

qfluentwidgets 只会处理内置图标，而项目资源中的单色 SVG 使用固定深灰填充，
在深色模式下对比度不足。本模块仅替换约定的单色值，并登记图标使用方以供刷新。

品牌色和多色图标不参与改写，因为其配色本身也是图标语义的一部分。
"""

from enum import Enum

from PySide6.QtCore import QByteArray, QFile, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import FluentIconBase, Theme, isDarkTheme
from qfluentwidgets.common.icon import getIconColor

# 只替换资源中约定的单色填充，避免破坏多色图标。
MONOCHROME_FILLS = ('#2c2c2c', '#2C2C2C', '#31303D', '#31303d')

# 登记项为“支持 setIcon 的对象 + Qt 资源路径”。
_registry = []
_cache = {}


class _ResourceIcon(FluentIconBase):
    """从项目 SVG 资源按当前主题生成矢量图标。"""

    def icon(self, theme=Theme.AUTO, color=None):
        fill = QColor(color).name() if color is not None else getIconColor(theme)
        key = (self.path(), fill)
        if key not in _cache:
            svg = _read_svg(self.path()).replace('currentColor', fill).replace('#212121', fill)
            _cache[key] = QIcon(_SvgIconEngine(svg))
        return _cache[key]

    def render(self, painter, rect, theme=Theme.AUTO, indexes=None, **attributes):
        color = attributes.get('stroke', attributes.get('fill'))
        painter.save()
        painter.translate(rect.x(), rect.y())
        self.icon(theme, color).paint(painter, 0, 0, round(rect.width()), round(rect.height()))
        painter.restore()


class SystemIcon(_ResourceIcon, Enum):
    """Lucide 线性图标，使用 SVG 资源并适配明暗主题。"""

    HELP = 'circle-question-mark'
    HISTORY = 'rotate-ccw-clock'
    UPDATE = 'refresh-cw'
    UNDO = 'undo-2'
    REDO = 'redo-2'
    PREVIOUS = 'skip-back'
    NEXT = 'skip-forward'
    COPYRIGHT = 'copyright'
    DOCUMENT = 'file-text'
    FOLDER = 'folder-open'
    LIST = 'list'
    TIMER = 'timer'
    CLOCK = 'clock-3'
    PLAY = 'play'
    PAUSE = 'pause'
    STOP = 'square'
    RESET = 'rotate-ccw'
    DOWNLOAD = 'download'
    INFO = 'info'
    MORE = 'ellipsis'

    def path(self, theme=Theme.AUTO):
        return f':/image/image/lucide/{self.value}.svg'

class FluentSystemIcon(_ResourceIcon, Enum):
    """微软 Fluent System Icons，统一使用为 20px 绘制的 Regular 版本。"""

    ENGINE = 'speaker_settings'
    SPEED = 'top_speed'
    VOLUME = 'speaker_2'
    WAVE = 'sound_wave_circle'
    LANGUAGE = 'local_language'
    GLOBE = 'globe'
    KEY = 'key'
    MODEL = 'cube'
    FOLDER = 'folder'
    INSTRUCTIONS = 'text_description'
    MICROPHONE = 'mic'
    NAME = 'text_field'
    MUSIC = 'music_note_2'
    DOCUMENT = 'document'
    LINK = 'link'
    TIMER = 'timer'
    NUMBER = 'number_symbol'
    SHUFFLE = 'arrow_shuffle'
    LEADING_SPACE = 'padding_left'
    TRAILING_SPACE = 'padding_right'
    SAVE = 'save'
    THEME = 'dark_theme'
    PALETTE = 'color'
    FONT = 'text_font'
    SLIDES = 'slide_multiple'
    COMMENT = 'comment_note'
    CHECK = 'document_checkmark'
    HISTORY = 'history'
    SYNC = 'arrow_sync'
    FULL_SCREEN = 'full_screen_maximize'
    SLIDESHOW = 'slide_play'
    NEXT_SLIDE = 'slide_arrow_right'
    LINKED_SLIDE = 'slide_link'
    STOP = 'stop'
    KEYBOARD = 'keyboard'
    HEADPHONE = 'headphones'
    CONNECT = 'plug_connected'
    VOICE = 'person_voice'
    UPDATE = 'arrow_clockwise'
    INFO = 'info'
    SLIDE_SETTINGS = 'slide_settings'
    GUIDE = 'book_open'
    LOG = 'document_text'
    SETTING = 'settings'
    EDIT = 'text_edit_style'
    VIDEO = 'video_clip'
    ARCHIVE = 'folder_zip'

    def path(self, theme=Theme.AUTO):
        return f':/image/image/fluent/ic_fluent_{self.value}_20_regular.svg'


class _SvgIconEngine(QIconEngine):
    """按最终尺寸绘制 SVG，避免固定尺寸位图在分数缩放下反复采样。"""

    def __init__(self, svg):
        super().__init__()
        self.svg = svg
        self.renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))

    def clone(self):
        return _SvgIconEngine(self.svg)

    def isNull(self):
        return not self.renderer.isValid()

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(painter.opacity() * 0.5)
        self.renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        pixels = QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)))
        pixmap = QPixmap(pixels)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRectF(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap


def _read_svg(path: str) -> str:
    """从 Qt 资源系统读取 SVG 文本；读取失败时返回空串。"""
    file = QFile(path)
    if not file.open(QIODevice.ReadOnly):
        return ''
    try:
        return bytes(file.readAll()).decode('utf-8', errors='replace')
    finally:
        file.close()


def themed_icon(path: str) -> QIcon:
    """返回按当前主题着色的图标；无可替换颜色时保留原资源。"""
    key = (path, isDarkTheme())
    if key in _cache:
        return _cache[key]

    svg = _read_svg(path)
    if not svg or not any(fill in svg for fill in MONOCHROME_FILLS):
        icon = QIcon(path)
        _cache[key] = icon
        return icon

    color = '#FFFFFF' if isDarkTheme() else '#2C2C2C'
    for fill in MONOCHROME_FILLS:
        svg = svg.replace(fill, color)

    icon = QIcon(_SvgIconEngine(svg))
    _cache[key] = icon
    return icon


def apply(owner, path: str):
    """为对象设置主题图标，并登记后续主题刷新。"""
    owner.setIcon(themed_icon(path))
    _registry.append((owner, path))
    return owner


def refresh_all():
    """刷新所有已登记图标，并移除已销毁的 Qt 对象。"""
    alive = []
    for owner, path in _registry:
        try:
            owner.setIcon(themed_icon(path))
        except RuntimeError:
            # 动态卡片重建后，Python 包装对象仍可能在表中，但底层 C++ 对象已销毁。
            continue
        alive.append((owner, path))

    _registry[:] = alive
