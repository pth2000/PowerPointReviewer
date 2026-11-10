# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('third_party\\signalsmith\\SignalsmithStretch_x64.dll', 'third_party/signalsmith'),
        ('native\\media_mux\\MediaMux_x64.dll', 'native/media_mux'),
    ],
    datas=collect_data_files('pptx', includes=['templates/*'])
    + [
        ('engines\\edge_voices.json', 'engines'),
        ('audio\\countdown.wav', 'audio'),
        ('audio\\countdown-final.wav', 'audio'),
        ('third_party\\signalsmith\\LICENSE-stretch.txt', 'third_party/signalsmith'),
        ('third_party\\signalsmith\\LICENSE-linear.txt', 'third_party/signalsmith'),
        ('third_party\\lucide\\LICENSE', 'third_party/lucide'),
        ('third_party\\fluent_system_icons\\LICENSE', 'third_party/fluent_system_icons'),
        ('third_party\\ffmpeg\\COPYING.LGPLv2.1', 'third_party/ffmpeg'),
        ('third_party\\ffmpeg\\NOTICE.md', 'third_party/ffmpeg'),
    ],
    # pywin32 在 IStream.Stat 等原生调用中动态加载时区模块。
    hiddenimports=['win32timezone'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 可选 GUI 与数组处理依赖不属于应用的运行路径。
    excludes=[
        'tkinter',
        'numpy',
        # 程序不处理 AVIF，排除其大型可选编解码库以控制发布包体积。
        'PIL.AvifImagePlugin',
        'PIL._avif',
        # 桌面程序只使用 SDK 的语音接口，不提供命令行、分词或本地 API 服务。
        # httpx._main 是可选 CLI；排除后保留正常 HTTP 客户端及 SDK 初始化依赖。
        'httpx._main',
        'dashscope.__main__',
        'dashscope.cli',
        'dashscope.acli',
        'typer',
        'click',
        'rich',
        'pygments',
        'markdown_it',
        'mdurl',
        'tiktoken',
        'tiktoken_ext',
        'regex',
        'fastapi',
        'uvicorn',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PowerPointReviewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 会让每个 DLL 在启动时于内存中解压，拖慢冷启动。
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['image\\ppt_ico.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PowerPointReviewer',
)
