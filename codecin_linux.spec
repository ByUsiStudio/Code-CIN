# -*- mode: python ; coding: utf-8 -*-

import os
import sys

_native = ('codecin/libcodecin_native.dylib' if sys.platform == 'darwin'
           else 'codecin/libcodecin_native.so')

a = Analysis(
    ['cpu.py'],
    pathex=[],
    binaries=[(_native, '.')] if os.path.exists(_native) else [],
    datas=[
        ('lib', 'lib'),
        ('misc/vim', 'misc/vim'),
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
