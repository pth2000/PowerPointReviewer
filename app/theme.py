"""应用明暗模式与主题色，并修正第三方控件的主题适配差异。

主题不影响语音生成，因此由设置页即时应用和持久化，不参与语音参数的显式保存流程。
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    ColorDialog,
    PrimaryDropDownPushButton,
    PrimaryDropDownToolButton,
    PrimaryPushButton,
    PrimarySplitPushButton,
    PrimarySplitToolButton,
    PrimaryToolButton,
    StrongBodyLabel,
    Theme,
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
    _apply_plain_label_color()
    _patch_accent_buttons()
    icons.refresh_all()


def _apply_plain_label_color():
    """为 Qt Designer 生成的原生 QLabel 补充主题文字色。

    qfluentwidgets 只更新自带标签；应用级规则优先级低于控件自身样式，不会覆盖其组件。
    """
    app = QApplication.instance()
    if app is None:
        return
    color = '#FFFFFF' if isDarkTheme() else '#000000'
    app.setStyleSheet(f'QLabel {{ color: {color}; }}')


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

_accent_patcher = None


def _patch_accent_button(widget):
    """将白字规则追加到强调色按钮自身的样式表。

    应用级样式表优先级不足以覆盖组件的 ``color: black``，必须修改控件级样式。
    """
    style = widget.styleSheet()
    if 'app-accent-text' in style:
        return
    widget.setStyleSheet(style + _ACCENT_TEXT_RULE)


class _AccentTextPatcher(QObject):
    """在控件完成样式装配时为新建的强调色按钮补白字规则。"""

    WATCHED = (QEvent.Type.Polish, QEvent.Type.StyleChange)

    def eventFilter(self, obj, event):
        if event.type() in self.WATCHED and isinstance(obj, ACCENT_BUTTON_TYPES):
            _patch_accent_button(obj)
        return False


def _patch_accent_buttons():
    """修正现有强调色按钮，并安装过滤器覆盖后续创建的按钮。"""
    global _accent_patcher

    app = QApplication.instance()
    if app is None:
        return

    for widget in app.allWidgets():
        if isinstance(widget, ACCENT_BUTTON_TYPES):
            _patch_accent_button(widget)

    if _accent_patcher is None:
        _accent_patcher = _AccentTextPatcher()
        app.installEventFilter(_accent_patcher)

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
