"""从完整备注提取朗读文字，保留可再次编辑的原始注释。"""

import re


_LINE_BREAKS = re.compile(r'\r\n|[\r\x0b\x0c\u2028\u2029]')
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0e-\x1f\x7f]+')


def normalize(text):
    return _CONTROL_CHARS.sub('', _LINE_BREAKS.sub('\n', str(text)))


def is_comment(line):
    return line.lstrip().startswith('//')


def spoken_text(text, enabled=False):
    text = normalize(text)
    if enabled:
        text = '\n'.join(line for line in text.split('\n') if not is_comment(line))
    return text


def spoken_notes(notes, enabled=False):
    return {page: spoken_text(text, enabled) for page, text in notes.items()}


def replace_spoken_text(original, replacement):
    """替换正文，按原先所在的正文行位置保留整行注释。"""
    comments = []
    position = 0
    for line in normalize(original).split('\n'):
        if is_comment(line):
            comments.append((position, line))
        else:
            position += 1
    if not comments:
        return replacement
    lines = spoken_text(replacement, True).split('\n')
    inserted = 0
    for position, line in comments:
        lines.insert(min(position, len(lines) - inserted) + inserted, line)
        inserted += 1
    return '\n'.join(lines)
