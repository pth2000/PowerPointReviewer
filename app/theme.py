"""应用明暗模式与主题色，并修正第三方控件的主题适配差异。

主题不影响语音生成，因此由设置页即时应用和持久化，不参与语音参数的显式保存流程。
"""

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont
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
    setTheme,
    setThemeColor,
)
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.style_sheet import ThemeColor

from app import icons

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


def make_help_icon(text: str = '', parent=None) -> QWidget:
    """创建悬停时显示说明的信息图标；没有说明时隐藏。"""
    holder = QWidget(parent)
    holder.setFixedSize(22, 22)
    icon = IconWidget(FluentIcon.INFO, holder)
    icon.setFixedSize(14, 14)
    icon.move(4, 4)
    icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
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
