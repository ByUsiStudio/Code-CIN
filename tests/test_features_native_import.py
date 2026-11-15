"""8.1.2 回归: cincc (``--native`` 机器码路径) 的 import 解析。

8.1.0 的 import 解析重写只对齐了通配展开与平台专用文件，cincc 侧还留着两个洞：

* ``cmd/cincc/main.go`` 给 ``CompileFile`` 传的是 ``libDir=""``，于是
  ``import "math.cin"`` 在 ``--native`` 下永远 ``Import file not found`` ——
  41 个内置标准库在机器码路径上全部不可用；
* 裸名字 import 先查**当前目录的同名文件**，与文档「裸名字是内置库，相对路径
  要写 ``./``」以及默认引擎的行为相反：同一份源码 cincc 能过、默认引擎报错。

本文件锁定修复后的行为。用例只要求 cincc 可执行文件存在，不需要 C 编译器，
也不实际运行产物（Windows 上跑 ELF 需要 WSL）。
"""

import os
import subprocess

import pytest

from codecin import cli

CINCC = cli._find_cincc()
needs_cincc = pytest.mark.skipif(CINCC is None, reason='cincc 未构建')


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _cincc(workdir: str, prog: str, *extra: str):
    out = os.path.join(workdir, 'out.bin')
    cmd = [CINCC, '-o', out] + list(extra) + [prog]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    return proc, out


def test_run_native_passes_lib_dir_via_env(workdir, monkeypatch):
    """--native 调 cincc 时要带上内置标准库目录。

    走环境变量而不是 ``-lib`` 参数: 旧版 cincc 不认识该参数, 加上会让
    ``codecin prog.cin --native`` 直接报 flag provided but not defined。
    """
    recorded = {}

    class _Proc:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(cmd, **kwargs):
        recorded['cmd'] = list(cmd)
        recorded['env'] = dict(kwargs.get('env') or {})
        return _Proc()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    prog = os.path.join(workdir, 'p.cin')
    _write(prog, 'function main() -> int { return 0 }\n')
    out = os.path.join(workdir, 'p.exe')
    assert cli.main([prog, '--native-out', out, '--log-level', 'ERROR']) == 0

    cmd = recorded['cmd']
    assert cmd[-1] == prog, '入口文件必须是最后一个参数'
    assert '-lib' not in cmd, '不应用 -lib 参数调用 (旧版 cincc 会拒绝)'
    lib = recorded['env'].get('CODECIN_LIB_DIR', '')
    assert lib.replace('\\', '/').endswith('/codecin/lib')


@needs_cincc
def test_cincc_resolves_builtin_library(workdir):
    """显式 -lib 下裸名字 import 命中 <lib>/<pkg>/src/<pkg>.cin (pic 包布局)。"""
    lib = os.path.join(workdir, 'lib')
    _write(os.path.join(lib, 'mymod', 'src', 'mymod.cin'),
           'function my_add(int a, int b) -> int { return a + b }\n')
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "mymod.cin"\n'
                 'function main() -> int { return my_add(1, 2) }\n')
    proc, out = _cincc(workdir, prog, '-lib', lib)
    assert proc.returncode == 0, proc.stderr
    assert os.path.isfile(out)


@needs_cincc
def test_cincc_reads_lib_dir_from_env(workdir):
    """CODECIN_LIB_DIR 也能指定库目录 (Python 侧就是这么传的)。"""
    lib = os.path.join(workdir, 'lib')
    _write(os.path.join(lib, 'mymod', 'src', 'mymod.cin'),
           'function my_mul(int a, int b) -> int { return a * b }\n')
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "mymod.cin"\n'
                 'function main() -> int { return my_mul(2, 3) }\n')
    out = os.path.join(workdir, 'out.bin')
    env = dict(os.environ)
    env['CODECIN_LIB_DIR'] = lib
    proc = subprocess.run([CINCC, '-o', out, prog], capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          env=env)
    assert proc.returncode == 0, proc.stderr
    assert os.path.isfile(out)


@needs_cincc
def test_cincc_rejects_bare_sibling_import(workdir):
    """裸名字不命中兄弟文件: 与默认引擎及文档规则一致。"""
    _write(os.path.join(workdir, 'helper.cin'),
           'function hv() -> int { return 1 }\n')
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "helper.cin"\n'
                 'function main() -> int { return hv() }\n')
    lib = os.path.join(workdir, 'lib')
    proc, _ = _cincc(workdir, prog, '-lib', lib)
    assert proc.returncode != 0
    assert 'Import file not found' in proc.stderr
    assert '内置标准库' in proc.stderr


@needs_cincc
def test_cincc_missing_relative_import_reports_searched_path(workdir):
    """相对导入目标不存在时报可操作信息, 不是裸 OS 错误。"""
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "./nope.cin"\n'
                 'function main() -> int { return 0 }\n')
    proc, _ = _cincc(workdir, prog)
    assert proc.returncode != 0
    assert 'Import file not found' in proc.stderr
    assert '相对引用' in proc.stderr
    assert 'nope.cin' in proc.stderr


@needs_cincc
def test_cincc_wildcard_without_match(workdir):
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "./mods/*.cin"\n'
                 'function main() -> int { return 0 }\n')
    proc, _ = _cincc(workdir, prog)
    assert proc.returncode != 0
    assert 'Import pattern matched no .cin file' in proc.stderr


@needs_cincc
def test_cincc_platform_variant_preferred(workdir, monkeypatch):
    """当前平台专用文件优先: 故意把 foo.cin 写成语法错误, 命中变体才编译得动。"""
    _write(os.path.join(workdir, 'foo.cin'),
           'function fv() -> int { return ??? }\n')
    _write(os.path.join(workdir, 'foo_win.cin'),
           'function fv() -> int { return 1 }\n')
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "./foo.cin"\n'
                 'function main() -> int { return fv() }\n')
    monkeypatch.setenv('CIN_TARGET_PLATFORM', 'win')
    proc, out = _cincc(workdir, prog)
    assert proc.returncode == 0, proc.stderr
    assert os.path.isfile(out)


@needs_cincc
def test_cincc_other_platform_variant_is_not_used(workdir, monkeypatch):
    """别的平台专用文件不参与解析: 只留 foo_win.cin 时 linux 目标报错。"""
    _write(os.path.join(workdir, 'foo_win.cin'),
           'function fv() -> int { return 1 }\n')
    prog = os.path.join(workdir, 'main.cin')
    _write(prog, 'import "./foo.cin"\n'
                 'function main() -> int { return fv() }\n')
    monkeypatch.setenv('CIN_TARGET_PLATFORM', 'linux')
    proc, _ = _cincc(workdir, prog)
    assert proc.returncode != 0
    assert 'Import file not found' in proc.stderr
