"""加载可随明暗主题重新着色的 SVG 资源图标。

qfluentwidgets 只会处理内置图标，而项目资源中的单色 SVG 使用固定深灰填充，
在深色模式下对比度不足。本模块仅替换约定的单色值，并登记图标使用方以供刷新。

品牌色和多色图标不参与改写，因为其配色本身也是图标语义的一部分。
"""

from PySide6.QtCore import QByteArray, QFile, QIODevice, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import isDarkTheme

# 只替换资源中约定的单色填充，避免破坏多色图标。
MONOCHROME_FILLS = ('#2c2c2c', '#2C2C2C', '#31303D', '#31303d')

RENDER_SIZE = 64

# 登记项为“支持 setIcon 的对象 + Qt 资源路径”。
_registry = []
_cache = {}


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

    pixmap = QPixmap(RENDER_SIZE, RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode('utf-8'))).render(painter)
    painter.end()

    icon = QIcon(pixmap)
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
