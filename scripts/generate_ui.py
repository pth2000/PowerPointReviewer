"""生成 Designer 页面与 Qt 资源；--check 仅检查，不修改文件。"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    ('Ui_mainwindow.ui', 'Ui_mainwindow.py', 'uic'),
    ('settingInterface.ui', 'settingInterface.py', 'uic'),
    ('toolsInterface.ui', 'toolsInterface.py', 'uic'),
    ('resource.qrc', 'resource_rc.py', 'rcc'),
)


def find_tool(kind):
    name = f'pyside6-{kind}'
    executable = Path(sys.executable).parent / (name + ('.exe' if sys.platform == 'win32' else ''))
    if executable.is_file():
        return str(executable)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f'未找到 {name}，请使用已安装项目依赖的 Python 环境。')


def generate(*, check=False):
    pending = []
    # 所有源文件都成功转换后才写回，转换失败不会留下半套更新的页面。
    with tempfile.TemporaryDirectory(prefix='ppr-ui-') as directory:
        for source, target, kind in SOURCES:
            output = Path(directory) / target
            subprocess.run([find_tool(kind), str(ROOT / source), '-o', str(output)],
                           cwd=ROOT, check=True)
            content = output.read_text(encoding='utf-8-sig')
            path = ROOT / target
            current = path.read_text(encoding='utf-8-sig') if path.exists() else None
            if current != content:
                pending.append((path, content))
        if check:
            for path, _content in pending:
                print(f'需要重新生成：{path.name}')
        else:
            for path, content in pending:
                path.write_text(content, encoding='utf-8')
                print(f'已生成：{path.name}')
    if not pending:
        print('UI 与资源文件已同步。')
    return 1 if check and pending else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='仅检查生成文件是否与源文件一致')
    args = parser.parse_args()
    return generate(check=args.check)


if __name__ == '__main__':
    sys.exit(main())
