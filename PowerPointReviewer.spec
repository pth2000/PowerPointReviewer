# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('.venv\\\\Lib\\\\site-packages\\\\pptx\\\\templates\\\\*', '.\\\\pptx\\\\templates'),
           ('engines\\edge_voices.json', 'engines')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这些包不再被引用，排除后可缩减产物体积与首次启动的磁盘读取量。
    excludes=['tkinter', 'PIL', 'pyautogui', 'pyscreeze', 'pygetwindow',
              'pymsgbox', 'mouseinfo', 'pytweening'],
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
