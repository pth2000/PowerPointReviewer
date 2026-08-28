"""管理 AI 改写风格与模板，并构造、清理和校验逐页改写结果。

改写以整页为上下文，页内分隔符对应演讲点击节奏。模型若改变分隔符数量，结果仍可展示，
但必须标记为需要人工确认。

风格与提示词模板均可持久化编辑；本模块的内置值只负责初始化和恢复默认。
"""

import copy
import re

DEFAULT_STYLE_NAME = '朗读友好'

# 内置风格是初始化和“恢复默认”的数据源，调用方只能使用其深拷贝。
BUILTIN_STYLES = [
    {
        'name': DEFAULT_STYLE_NAME,
        'instruction': (
            '这份讲稿已经过人工推敲，默认逐字保留，只修改会让语音合成读错或读不出来的地方：\n'
            '- 阿拉伯数字改写为中文读法：年份、编号、电话等逐位读，数量、小数、百分比按数值读\n'
            '- 英文缩写与型号改写为实际读法，例如 "GPT-4" 写成 "G P T 四"、"API" 写成 "A P I"\n'
            '- 数学与特殊符号改写为中文读法，例如不等号写成 "大于等于"、乘号写成 "乘以"、"3~5" 写成 "三到五"\n'
            '- 计量单位改写为中文，例如 "km/h" 写成 "公里每小时"、"kg" 写成 "千克"\n'
            '- 确有歧义的多音字，在不改变含义的前提下换成读音明确的同义表达\n'
            '除上述情况外，不得调整语气、措辞、句式、语序和篇幅，不得增删内容。'
            '如果整页都没有需要处理的地方，原样输出原文。'
        ),
    },
    {
        'name': 'English narration',
        'instruction': (
            'The script is already polished; keep it word for word by default. '
            'Only fix what a text-to-speech engine would read incorrectly:\n'
            '- spell out digits, ordinals and years the way they are spoken\n'
            '- expand or re-space acronyms and model names so they are read correctly\n'
            '- replace symbols and units with spoken words, e.g. write comparison signs and units in full\n'
            '- resolve abbreviations that would be read as words by mistake\n'
            'Do not change tone, wording, sentence structure or length, and do not add or remove content. '
            'If nothing on the page needs fixing, return the original text unchanged. '
            'Answer in English.'
        ),
    },
    {
        'name': '口语化',
        'instruction': (
            '把书面语改写成适合口头表达的说法：去掉"综上所述""此外""其"这类书面连接词，'
            '长句拆成短句，必要时补充口语化的过渡。保留原有观点与信息量。'
        ),
    },
    {
        'name': '更精简',
        'instruction': '在不丢失关键信息的前提下压缩篇幅，删掉冗余修饰与重复表述，让每句话都有信息量。',
    },
    {
        'name': '更充实',
        'instruction': '在原有观点基础上适当展开，补充自然的过渡与铺垫，让讲述更完整流畅，但不得编造事实。',
    },
]

NEW_STYLE_INSTRUCTION = '在这里描述你希望模型如何改写这份讲稿。'

# 可编辑模板支持 instruction、text、mark、segment_count 和 segment_rule 占位符。
# text 是唯一必需项；未知占位符保留原样，便于用户在文本中使用普通花括号。

DEFAULT_SYSTEM_PROMPT = (
    '你是一位演讲稿润色专家，擅长在尽量保留作者原意与措辞的前提下，'
    '把讲稿调整为适合口头讲述与语音合成的形式。'
    '你只输出改写后的讲稿正文，不做任何解释。'
)

DEFAULT_USER_TEMPLATE = '''请改写下面这一页 PPT 的讲稿。

规则：
1. 只输出改写后的正文，不要标题、不要解释、不要 Markdown 标记、不要代码块。
2. 改写要求：{instruction}
3. 保留原文的换行分段结构。
4. 不得编造原文中没有的事实、数据或结论。
{segment_rule}
原文：
---
{text}
---'''

DEFAULT_SEGMENT_RULE = (
    '5. 原文中的 "{mark}" 是页内分隔符，用来标记演讲时的点击节奏。'
    '必须原样保留，且数量恰好是 {segment_count} 个，不得增删或移动到别的语义位置。\n'
)

TEMPLATE_FIELDS = ('system_prompt', 'user_template', 'segment_rule')

# 缺少正文占位符会使讲稿无法进入请求，因此保存模板时必须校验。
REQUIRED_PLACEHOLDER = '{text}'

# 风格数据

def default_styles() -> list[dict]:
    """返回可由调用方安全修改的内置风格副本。"""
    return copy.deepcopy(BUILTIN_STYLES)


def normalize_styles(raw) -> list[dict]:
    """过滤无效、空白或重名风格；没有可用项时恢复内置列表。"""
    styles = []
    seen = set()

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name', '')).strip()
            instruction = str(item.get('instruction', '')).strip()
            if not name or not instruction or name in seen:
                continue
            seen.add(name)
            styles.append({'name': name, 'instruction': instruction})

    return styles or default_styles()


def style_names(styles) -> list[str]:
    """提取用于界面选择的风格名称。"""
    return [item['name'] for item in styles]


def find_instruction(styles, name: str) -> str:
    """按名称查找改写要求；找不到时回退到首个可用风格。"""
    for item in styles:
        if item['name'] == name:
            return item['instruction']
    return styles[0]['instruction'] if styles else BUILTIN_STYLES[0]['instruction']


def unique_name(styles, base: str = '新风格') -> str:
    """基于给定前缀生成未被占用的风格名称。"""
    existing = {item['name'] for item in styles}
    if base not in existing:
        return base
    for index in range(2, 1000):
        candidate = f'{base} {index}'
        if candidate not in existing:
            return candidate
    return base


# 提示词构造

def default_templates() -> dict:
    """返回一份新的内置提示词模板映射。"""
    return {
        'system_prompt': DEFAULT_SYSTEM_PROMPT,
        'user_template': DEFAULT_USER_TEMPLATE,
        'segment_rule': DEFAULT_SEGMENT_RULE,
    }


def normalize_templates(raw) -> dict:
    """补齐缺失、空白或非字符串的模板字段。"""
    templates = default_templates()
    if isinstance(raw, dict):
        for key in TEMPLATE_FIELDS:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                templates[key] = value
    return templates


def _render(template: str, values: dict) -> str:
    """按键名执行有限的占位符字面替换。

    不使用 ``str.format``，因为用户模板中的未知花括号必须原样保留而非触发异常。
    """
    text = str(template or '')
    for key, value in values.items():
        text = text.replace('{' + key + '}', str(value))
    return text


def build_user_prompt(page_text: str, *, mark: str = '●', instruction: str = '',
                      templates=None) -> str:
    """渲染页面级用户提示词；界面预览与实际请求共用此实现。"""
    config = normalize_templates(templates)
    segment_count = page_text.count(mark) if mark else 0

    base = {
        'mark': mark,
        'segment_count': segment_count,
        'instruction': str(instruction or '').strip() or NEW_STYLE_INSTRUCTION,
    }

    rendered = _render(config['user_template'], {
        **base,
        'segment_rule': _render(config['segment_rule'], base) if segment_count else '',
        # 正文最后注入，避免原文本身包含模板占位符时被二次展开。
        'text': '\x00TEXT\x00',
    })

    # 无分隔符页面不会渲染 segment_rule，顺带收敛由此留下的空行。
    rendered = re.sub(r'\n{3,}', '\n\n', rendered)
    return rendered.replace('\x00TEXT\x00', page_text)


def build_messages(page_text: str, *, mark: str = '●', instruction: str = '',
                   templates=None) -> list[dict]:
    """构造一次页面改写所需的 system 和 user 消息。"""
    config = normalize_templates(templates)
    return [
        {'role': 'system', 'content': config['system_prompt']},
        {'role': 'user', 'content': build_user_prompt(
            page_text, mark=mark, instruction=instruction, templates=config)},
    ]


def clean_response(text: str) -> str:
    """去除模型偶尔附加的最外层 Markdown 代码围栏。"""
    content = str(text or '').strip()
    if content.startswith('```'):
        lines = content.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        content = '\n'.join(lines).strip()
    return content


def check_segments(original: str, rewritten: str, mark: str = '●') -> str:
    """比较改写前后的分隔符数量，结构变化时返回警告文本。"""
    if not mark:
        return ''

    before = original.count(mark)
    after = rewritten.count(mark)
    if before == after:
        return ''

    return f'分隔符数量由 {before} 变为 {after}，页内点击节奏将改变'
