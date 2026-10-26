"""识别媒体自身的播放控件，区别于需要讲稿配合的对象触发动画。"""

from functools import lru_cache
from pathlib import Path
import zipfile

from app.logging_setup import get_logger


@lru_cache(maxsize=64)
def _contains_media(filename, modified_ns, size):
    extensions = {'.mp3', '.wav', '.m4a', '.wma', '.aac', '.mp4', '.wmv', '.avi', '.mov', '.m4v', '.mpeg', '.mpg'}
    with zipfile.ZipFile(filename) as package:
        return any(name.startswith('ppt/media/') and Path(name).suffix.lower() in extensions
                   for name in package.namelist())


def contains_embedded_media(source):
    if not source:
        return False
    source = Path(source)
    if source.suffix.lower() == '.ppt':
        return True
    try:
        stat = source.stat()
        return _contains_media(str(source.resolve()), stat.st_mtime_ns, stat.st_size)
    except (OSError, zipfile.BadZipFile):
        get_logger().debug('无法预读文稿媒体信息：%s', source, exc_info=True)
        return False

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'p': P}


def media_shape_ids(timing):
    return {target.get('spid') for node in timing.findall('.//p:cMediaNode', NS)
            for target in node.findall('p:tgtEl/p:spTgt', NS) if target.get('spid')}


def is_media_control_sequence(node, media_ids):
    """仅放行点视频本身暂停/继续的标准控制，不放行其他对象触发。"""
    conditions = node.findall('p:stCondLst/p:cond', NS)
    if len(conditions) != 1 or conditions[0].get('evt') != 'onClick':
        return False
    target = conditions[0].find('p:tgtEl/p:spTgt', NS)
    if target is None or target.get('spid') not in media_ids:
        return False
    identifier = target.get('spid')
    for condition in node.iter(f'{{{P}}}cond'):
        if condition.get('evt') not in (None, 'onClick'):
            return False
        targets = condition.findall('p:tgtEl/p:spTgt', NS)
        if targets and any(item.get('spid') != identifier for item in targets):
            return False
    effects = [item for item in node.iter(f'{{{P}}}cTn')
               if item.get('nodeType') in ('clickEffect', 'withEffect', 'afterEffect')]
    commands = node.findall('.//p:cmd', NS)
    if not effects or len(commands) != len(effects):
        return False
    if any(effect.get('presetClass') != 'mediacall' for effect in effects):
        return False
    if any(node.find(f'.//p:{tag}', NS) is not None for tag in
           ('anim', 'animClr', 'animEffect', 'animMotion', 'animRot', 'animScale', 'set')):
        return False
    return all(command.get('type') == 'call' and command.get('cmd') == 'togglePause'
               and [item.get('spid') for item in command.findall('.//p:spTgt', NS)] == [identifier]
               for command in commands)
