"""应用明暗模式、主题色与界面字体，并修正第三方控件的主题适配差异。

外观不影响语音生成，因此由设置页即时应用和持久化，不参与语音参数的显式保存流程。
"""

import ctypes
import hashlib
import json
import sys
from functools import cmp_to_key

from PySide6.QtCore import QCollator, QEvent, QLocale, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QHBoxLayout, QToolButton, QWidget
from qfluentwidgets import (
    ColorDialog,
    FluentIcon,
    IconWidget,
    PrimaryDropDownPushButton,
    PrimaryDropDownToolButton,
    PrimaryPushButton,
    PrimarySplitPushButton,
    PrimarySplitToolButton,
    PrimaryToolButton,
    StrongBodyLabel,
    Theme,
    ToolTipFilter,
    ToolTipPosition,
    isDarkTheme,
    setFontFamilies,
    setTheme,
    setThemeColor,
)
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.icon import drawIcon
from qfluentwidgets.common.style_sheet import ThemeColor, updateStyleSheet

from app import icons, paths

DEFAULT_COLOR = '#B7472A'

# 预设色取自 Office 组件品牌色，并保留 PowerPoint 红作为默认值。
PRESET_COLORS = (
    (DEFAULT_COLOR, '红'),
    ('#217346', '绿'),
    ('#2B579A', '蓝'),
)

# 每项同时保存稳定配置值和本地化显示文本。
THEME_MODES = (
    ('auto', '跟随系统'),
    ('light', '浅色'),
    ('dark', '深色'),
)

_MODE_MAP = {'auto': Theme.AUTO, 'light': Theme.LIGHT, 'dark': Theme.DARK}

# 所选字体缺字时按此顺序回退；该列表同时是 qfluentwidgets 的默认字体。
FALLBACK_FONTS = ('Segoe UI', 'Microsoft YaHei', 'PingFang SC')

DEFAULT_FONT_LABEL = '系统默认'

# 界面是中文的，只提供能显示中文的字体，否则所选字体对界面几乎没有影响。
CJK_WRITING_SYSTEMS = (
    QFontDatabase.WritingSystem.SimplifiedChinese,
    QFontDatabase.WritingSystem.TraditionalChinese,
)

# 用来生成字体指纹的取样文字，兼顾中文、西文和数字。
FONT_PROBE_TEXT = '永字Ag1'

_available_fonts = None


def mode_labels() -> list:
    """返回主题模式的本地化显示文本。"""
    return [label for _value, label in THEME_MODES]


def label_for_mode(value: str) -> str:
    """将持久化模式值转换为显示文本，未知值回退为自动模式。"""
    for mode, label in THEME_MODES:
        if mode == value:
            return label
    return THEME_MODES[0][1]


def mode_for_label(label: str) -> str:
    """将显示文本转换为持久化模式值，未知文本回退为自动模式。"""
    for mode, text in THEME_MODES:
        if text == label:
            return mode
    return THEME_MODES[0][0]


def available_fonts() -> tuple:
    """返回可供选择的字体，每项为 ``(字体名, 显示名)``。

    字体全部来自系统枚举，没有内置清单。判断字体能否显示中文需要逐个加载字体文件，
    耗时随系统安装的字体数量增长，因此结果写入缓存，只在系统字体增减后重新枚举。
    """
    global _available_fonts
    if _available_fonts is None:
        families = QFontDatabase.families()
        signature = _font_cache_signature(families)
        fonts = _cached_fonts(signature)
        if fonts is None:
            fonts = _collect_fonts(families)
            _store_fonts(signature, fonts)
        _available_fonts = (('', DEFAULT_FONT_LABEL),) + fonts
    return _available_fonts


def _font_cache_signature(families: list) -> str:
    """计算缓存标识；系统字体或枚举规则变化后不再匹配。"""
    rules = (FONT_PROBE_TEXT, *(system.name for system in CJK_WRITING_SYSTEMS))
    payload = '\n'.join((*rules, *families))
    return hashlib.md5(payload.encode('utf-8')).hexdigest()


def _cached_fonts(signature: str):
    """读取上次的枚举结果；缓存缺失或已失效时返回 ``None``。"""
    path = paths.FONT_CACHE
    if not path.is_file():
        return None
    try:
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('signature') != signature:
            return None
        return tuple((str(value), str(label)) for value, label in data['fonts'])
    except Exception as e:
        print(f'[字体] 缓存读取失败，将重新枚举：{e}')
        return None


def _store_fonts(signature: str, fonts: tuple):
    """写入枚举结果；写入失败不影响本次使用。"""
    try:
        paths.FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with paths.FONT_CACHE.open('w', encoding='utf-8') as f:
            json.dump({'signature': signature, 'fonts': [list(item) for item in fonts]},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[字体] 缓存写入失败：{e}')


def _collect_fonts(families: list) -> tuple:
    """枚举能显示界面文字的系统字体，合并别名后按中文习惯排序。"""
    groups = {}
    for family in families:
        # 点阵字体无法缩放，放大后会严重锯齿。
        if not QFontDatabase.isScalable(family):
            continue
        if not set(QFontDatabase.writingSystems(family)) & set(CJK_WRITING_SYSTEMS):
            continue
        groups.setdefault(_font_signature(family), []).append(family)

    collator = QCollator(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    fonts = [_font_names(aliases) for aliases in groups.values()]
    fonts.sort(key=cmp_to_key(lambda a, b: collator.compare(a[1], b[1])))
    return tuple(fonts)


def _font_signature(family: str) -> bytes:
    """渲染取样文字作为字体指纹。

    系统会把同一字体的中英文名各登记一次，例如「宋体」和「SimSun」；
    二者指向同一字体文件，渲染结果完全相同，据此可以合并为一项。
    """
    font = QFont(family)
    font.setPixelSize(16)
    image = QImage(72, 22, QImage.Format.Format_Grayscale8)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignLeft, FONT_PROBE_TEXT)
    painter.end()
    return image.constBits().tobytes()


def _font_names(aliases: list) -> tuple:
    """在同一字体的多个名称中选出持久化用名和显示用名。

    英文名在任何语言的系统上都能解析，用于持久化；中文名更易辨认，用于显示。
    """
    ordered = sorted(aliases, key=lambda name: (len(name), name))
    latin = [name for name in ordered if name.isascii()]
    localized = [name for name in ordered if not name.isascii()]
    value = latin[0] if latin else ordered[0]
    return value, (localized[0] if localized else value)


def label_for_font(value: str) -> str:
    """将持久化字体名转换为显示文本，列表外的字体直接显示其字体名。"""
    for family, label in available_fonts():
        if family == value:
            return label
    return str(value) or DEFAULT_FONT_LABEL


def font_for_label(label: str) -> str:
    """将显示文本转换为持久化字体名，列表外的文本按字体名处理。"""
    for family, text in available_fonts():
        if text == label:
            return family
    return label


def normalize_font(value: str) -> str:
    """规范化字体名，未安装的字体回退为系统默认。"""
    text = str(value or '').strip()
    if text and text in QFontDatabase.families():
        return text
    return ''


def font_families(value: str) -> tuple:
    """返回所选字体及其回退字体的完整顺序。"""
    family = normalize_font(value)
    return (family, *FALLBACK_FONTS) if family else FALLBACK_FONTS


def normalize_color(value: str) -> str:
    """规范化 ``#RRGGBB`` 颜色值，非法输入回退到默认色。"""
    text = str(value or '').strip()
    if len(text) == 7 and text.startswith('#'):
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            pass
    return DEFAULT_COLOR


def apply_theme(mode: str, color: str):
    """应用主题，并刷新原生标签、强调色按钮和资源图标。

    传入 ``save=False`` 可阻止 qfluentwidgets 另写一份配置，确保本应用配置是唯一数据源。
    """
    disable_dark_color_boost()
    setTheme(_MODE_MAP.get(str(mode), Theme.AUTO), save=False)
    setThemeColor(normalize_color(color), save=False)
    _apply_native_widget_style()
    _patch_widgets()
    icons.refresh_all()


def apply_font(value: str):
    """应用界面字体，使其同时作用于样式表、新建控件和已创建控件。"""
    families = list(font_families(value))
    setFontFamilies(families, save=False)
    updateStyleSheet()
    _apply_widget_fonts(families)


def _apply_widget_fonts(families: list):
    """把字体族写入应用默认字体和已创建的控件，各控件的字号与字重保持不变。

    qfluentwidgets 组件在构造时就固定了自己的字体，仅改配置只能影响此后新建的控件。
    """
    app = QApplication.instance()
    if app is None:
        return

    font = app.font()
    font.setFamilies(families)
    app.setFont(font)

    # 应用默认字体只覆盖未显式设置字体的控件，其余逐个改写字体族。
    for widget in app.allWidgets():
        widget_font = widget.font()
        if widget_font.families() == families:
            continue
        widget_font.setFamilies(families)
        widget.setFont(widget_font)


def _apply_native_widget_style():
    """让 qfluentwidgets 未覆盖的原生控件跟随主题。

    原生 QLabel 补充主题文字色，QSplitter 分隔条改为透明以融入背景。
    应用级规则优先级低于控件自身样式，不会覆盖 qfluentwidgets 组件。
    """
    app = QApplication.instance()
    if app is None:
        return
    color = '#FFFFFF' if isDarkTheme() else '#000000'
    app.setStyleSheet(f'QLabel {{ color: {color}; }}\n'
                      'QSplitter::handle { background: transparent; }')


def create_color_dialog(color, parent, title: str = '选择主题色'):
    """创建取色对话框，并本地化 qfluentwidgets 的内置英文文案。"""
    dialog = ColorDialog(color, title, parent)
    dialog.editLabel.setText('编辑颜色')
    dialog.redLabel.setText('红')
    dialog.greenLabel.setText('绿')
    dialog.blueLabel.setText('蓝')
    dialog.opacityLabel.setText('不透明度')
    dialog.yesButton.setText('确定')
    dialog.cancelButton.setText('取消')
    return dialog


# 动态标题需与 Qt Designer 中的静态标题保持相同字号。
CARD_TITLE_POINT_SIZE = 10


def make_card_title(text: str, parent=None) -> StrongBodyLabel:
    """创建主题感知且与静态界面样式一致的卡片标题。

    使用字体属性而非样式表调整字号和字重，从而保留组件自带的主题文字色。
    """
    label = StrongBodyLabel(text, parent)
    font = label.font()
    font.setPointSize(CARD_TITLE_POINT_SIZE)
    font.setWeight(QFont.Weight.Bold)
    label.setFont(font)
    return label


# DWMWA_USE_IMMERSIVE_DARK_MODE 在 Windows 10 20H1 起为 20，更早的版本为 19
_DARK_TITLE_BAR_ATTRIBUTES = (20, 19)


def window_background() -> QColor:
    """返回与当前主题匹配的窗口背景色。"""
    return QColor(32, 32, 32) if isDarkTheme() else QColor(243, 243, 243)


def apply_title_bar_theme(widget):
    """设置系统标题栏的明暗；不支持的系统上保持默认外观。"""
    if sys.platform != 'win32':
        return
    value = ctypes.c_int(1 if isDarkTheme() else 0)
    hwnd = int(widget.winId())
    for attribute in _DARK_TITLE_BAR_ATTRIBUTES:
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
        if result == 0:
            break


HELP_ICON_SIZE = 14
HELP_ICON_MARGIN = 4


class _HelpIcon(IconWidget):
    """在控件内部留出绘制边距，容纳分数缩放下的抗锯齿边缘。"""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing |
                               QPainter.RenderHint.SmoothPixmapTransform)
        margin = HELP_ICON_MARGIN
        rect = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        drawIcon(self._icon, painter, rect)


def make_help_icon(text: str = '', parent=None) -> QWidget:
    """创建悬停时显示说明的信息图标；没有说明时隐藏。"""
    holder = _HelpIcon(FluentIcon.INFO, parent)
    extent = HELP_ICON_SIZE + HELP_ICON_MARGIN * 2
    holder.setFixedSize(extent, extent)
    holder.installEventFilter(ToolTipFilter(holder, 300, ToolTipPosition.TOP))
    set_help_text(holder, text)
    return holder


def set_help_text(icon: QWidget, text: str):
    """更新说明图标的内容；没有说明时隐藏图标。"""
    icon.setToolTip(text or '')
    icon.setVisible(bool(text))


def make_title_row(title: QWidget, help_text: str = ''):
    """把标题与说明图标排成一行，返回行布局与说明图标。"""
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(title)
    icon = make_help_icon(help_text, title.parentWidget())
    row.addWidget(icon)
    row.addStretch(1)
    return row, icon


def wrap_title_with_help(layout, title: QWidget, help_text: str = ''):
    """把布局中已有的标题替换为带说明图标的标题行。"""
    index = layout.indexOf(title)
    layout.removeWidget(title)
    row, icon = make_title_row(title, help_text)
    layout.insertLayout(index, row)
    return row, icon


# 深色模式会把强调色按钮文字改黑；应用统一使用白字以保持对比度。
ACCENT_BUTTON_TYPES = (
    PrimaryPushButton, PrimarySplitPushButton, PrimaryToolButton,
    PrimaryDropDownPushButton, PrimaryDropDownToolButton, PrimarySplitToolButton,
)

_ACCENT_TEXT_RULE = '\n'.join((
    '',
    '/* app-accent-text */',
    'PrimaryPushButton, PrimarySplitPushButton, PrimaryToolButton,',
    'PrimaryDropDownPushButton, PrimaryDropDownToolButton, PrimarySplitToolButton {',
    '    color: white;',
    '}',
    '',
))

_widget_patcher = None


def _patch_accent_button(widget):
    """将白字规则追加到强调色按钮自身的样式表。

    应用级样式表优先级不足以覆盖组件的 ``color: black``，必须修改控件级样式。
    """
    style = widget.styleSheet()
    if 'app-accent-text' in style:
        return
    widget.setStyleSheet(style + _ACCENT_TEXT_RULE)


def _use_point_font(widget):
    """把控件的像素字号换算为等效的点数字号。

    Qt 样式表引擎处理工具按钮的悬停状态时会读取字体的点数，像素字体读到的是 -1，
    在缩放比例与主屏不同的屏幕上会因此输出 QFont::setPointSize 警告。
    """
    font = widget.font()
    if font.pixelSize() <= 0:
        return
    font.setPointSizeF(font.pixelSize() * 72 / widget.logicalDpiY())
    widget.setFont(font)


def _patch_widget(widget):
    """对单个控件应用全部修正。"""
    if isinstance(widget, ACCENT_BUTTON_TYPES):
        _patch_accent_button(widget)
    if isinstance(widget, QToolButton):
        _use_point_font(widget)


class _WidgetPatcher(QObject):
    """在控件完成样式装配或字体变化时，为新建控件补充应用级修正。"""

    WATCHED = (QEvent.Type.Polish, QEvent.Type.StyleChange)

    def eventFilter(self, obj, event):
        event_type = event.type()
        if event_type in self.WATCHED:
            _patch_widget(obj)
        elif event_type == QEvent.Type.FontChange and isinstance(obj, QToolButton):
            _use_point_font(obj)
        return False


def _patch_widgets():
    """修正现有控件，并安装过滤器覆盖后续创建的控件。"""
    global _widget_patcher

    app = QApplication.instance()
    if app is None:
        return

    for widget in app.allWidgets():
        _patch_widget(widget)

    if _widget_patcher is None:
        _widget_patcher = _WidgetPatcher()
        app.installEventFilter(_widget_patcher)

# 强调色派生规则

_boost_disabled = False


def _plain_theme_color(self):
    """直接从用户所选颜色派生各级强调色，不受明暗模式影响。

    默认实现会在深色模式下显著提亮并降低饱和度，导致实际颜色偏离选择值。
    此实现保留 PRIMARY 原色，只为悬停和按下状态计算固定比例的深浅变体。
    """
    base = qconfig.get(qconfig._cfg.themeColor)
    hue, saturation, value, _ = base.getHsvF()

    if self == ThemeColor.DARK_1:
        value *= 0.75
    elif self == ThemeColor.DARK_2:
        saturation *= 1.05
        value *= 0.5
    elif self == ThemeColor.DARK_3:
        saturation *= 1.1
        value *= 0.4
    elif self == ThemeColor.LIGHT_1:
        value *= 1.05
    elif self == ThemeColor.LIGHT_2:
        saturation *= 0.75
        value *= 1.05
    elif self == ThemeColor.LIGHT_3:
        saturation *= 0.65
        value *= 1.05

    return QColor.fromHsvF(hue, min(saturation, 1), min(value, 1))


def disable_dark_color_boost():
    """幂等替换 qfluentwidgets 的强调色派生实现。

    第三方库结构变化时只记录错误并保留默认行为，不阻断应用启动。
    """
    global _boost_disabled
    if _boost_disabled:
        return

    try:
        ThemeColor.color = _plain_theme_color
        _boost_disabled = True
    except Exception as e:
        print(f'[主题] 未能取消深色模式提亮，将沿用默认行为：{e}')
