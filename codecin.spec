# -*- mode: python ; coding: utf-8 -*-

import os

_native_dll = 'codecin/codecin_native.dll'

a = Analysis(
    ['cpu.py'],
    pathex=[],
    # 原生库缺失时不阻断打包 (产物退化为纯 Python 执行)
    binaries=[(_native_dll, '.')] if os.path.exists(_native_dll) else [],
    datas=[
        ('lib', 'lib'),                 # 标准库 (import "lib/xxx.cin" 依赖)
        ('misc/vim', 'misc/vim'),       # 编辑器语法文件
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'matplotlib', 'pandas', 'sklearn', 'PIL',
        'pywin32', 'win32com', 'win32api', 'pythoncom',
        'cryptography', 'Crypto', 'nacl',
        'yaml', 'requests', 'urllib3', 'boto3', 'bs4',
        'setuptools', 'pkg_resources', 'IPython', 'jedi', 'debugpy',
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
    name='codecin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='codecin',
)
