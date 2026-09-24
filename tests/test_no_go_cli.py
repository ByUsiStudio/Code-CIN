"""架构门禁: Go 侧只作为库存在, 不提供任何 CLI 入口。

约定 (5.5.0): Python 是唯一 CLI 入口; `codecin/native/` 下唯一允许的
`package main` 是与 Python ctypes 共享的 c-shared 库入口 `main.go`。
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, 'codecin', 'native')


def _go_files():
    out = []
    for base, dirs, files in os.walk(NATIVE):
        # Go 工具链忽略以 '.' 开头的目录; 这里同样跳过
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in files:
            if name.endswith('.go'):
                out.append(os.path.join(base, name))
    return out


def test_only_allowed_main_package_is_shared_lib_entry():
    mains = []
    for path in _go_files():
        with open(path, encoding='utf-8') as f:
            head = f.read(4096)
        if head.startswith('package main') or '\npackage main' in head:
            mains.append(os.path.relpath(path, ROOT).replace('\\', '/'))
    assert mains == ['codecin/native/main.go'], (
        'Go 侧出现了额外的 CLI 入口 (package main): '
        f'{mains}; 唯一允许的是 c-shared 库入口 codecin/native/main.go')


def test_shared_lib_entry_is_cgo_shared_library():
    with open(os.path.join(NATIVE, 'main.go'), encoding='utf-8') as f:
        src = f.read()
    assert '#include <stdlib.h>' in src, 'main.go 不再是 cgo c-shared 库入口'
    assert 'import "C"' in src


def test_no_go_cli_binary_committed():
    for name in ('codecin', 'codecin.exe', 'libcodecin_native.so',
                 'codecin_native.dll', 'libcodecin_native.dylib'):
        assert not os.path.exists(os.path.join(NATIVE, name)), \
            f'{name} 是构建产物, 不应出现在源码树里'
