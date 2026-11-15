"""8.1.x 回归: 相对 import 的 ``*`` 通配展开与平台专用源码择优。

8.1.0 让相对 import 学会两件事: 通配展开 (``import "./mods/*.cin"``) 和
``foo_win.cin`` / ``foo_linux.cin`` / ``foo_macos.cin`` / ``foo_android.cin``
平台专用文件择优。当时没有用例, 这里把展开顺序、去重、跨平台排除与回退行为
固定下来。

平台判定可用 ``CIN_TARGET_PLATFORM`` 覆盖, 因此本文件不依赖宿主机实际平台。
"""

import os

import pytest

from codecin.errors import CompilerError
from tests.helpers import run_cin_file


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _run(path: str, platform: str, monkeypatch):
    monkeypatch.setenv('CIN_TARGET_PLATFORM', platform)
    cpu = run_cin_file(path)
    assert not cpu.execution_failed, '导入用例不应执行失败'
    return cpu.regs.read(0)


def test_wildcard_expands_all_modules(workdir, monkeypatch):
    """``*.cin`` 展开目录下所有模块, 多文件项目不必逐条写 import。"""
    _write(os.path.join(workdir, 'mods', 'a.cin'),
           'function ma(int x) -> int { return x + 1 }\n')
    _write(os.path.join(workdir, 'mods', 'b.cin'),
           'function mb(int x) -> int { return x * 2 }\n')
    _write(os.path.join(workdir, 'main.cin'),
           'import "./mods/*.cin"\n'
           'function main() -> int { return ma(mb(10)) }\n')
    assert _run(os.path.join(workdir, 'main.cin'), 'linux', monkeypatch) == 21


def test_wildcard_ignores_non_cin_files(workdir, monkeypatch):
    """目录里的非 .cin 文件不参与展开 (不会被当成源码解析)。"""
    _write(os.path.join(workdir, 'mods', 'a.cin'),
           'function ma() -> int { return 3 }\n')
    _write(os.path.join(workdir, 'mods', 'readme.txt'),
           '这不是 CIN 源码 {{{ \n')
    _write(os.path.join(workdir, 'main.cin'),
           'import "./mods/*.cin"\n'
           'function main() -> int { return ma() }\n')
    assert _run(os.path.join(workdir, 'main.cin'), 'linux', monkeypatch) == 3


def test_platform_variant_wins_over_plain_file(workdir, monkeypatch):
    """存在当前平台专用文件时优先使用它, 而不是同名 ``foo.cin``。"""
    _write(os.path.join(workdir, 'p.cin'),
           'function pv() -> int { return 1 }\n')
    _write(os.path.join(workdir, 'p_linux.cin'),
           'function pv() -> int { return 2 }\n')
    _write(os.path.join(workdir, 'main.cin'),
           'import "./p.cin"\n'
           'function main() -> int { return pv() }\n')
    path = os.path.join(workdir, 'main.cin')
    assert _run(path, 'linux', monkeypatch) == 2   # 命中 p_linux.cin
    assert _run(path, 'win', monkeypatch) == 1     # 无 p_win.cin -> 回退 p.cin


def test_wildcard_excludes_other_platform_files(workdir, monkeypatch):
    """通配展开排除其它平台文件, 并按去掉平台后缀的路径去重。

    ``x.cin`` 与 ``x_win.cin`` 定义同一个函数: 若两者都被包含会报重复定义,
    因此「编译通过且返回值来自平台文件」同时验证了排除与去重。
    """
    _write(os.path.join(workdir, 'mods', 'x.cin'),
           'function xv() -> int { return 1 }\n')
    _write(os.path.join(workdir, 'mods', 'x_win.cin'),
           'function xv() -> int { return 2 }\n')
    _write(os.path.join(workdir, 'main.cin'),
           'import "./mods/*.cin"\n'
           'function main() -> int { return xv() }\n')
    path = os.path.join(workdir, 'main.cin')
    assert _run(path, 'win', monkeypatch) == 2
    assert _run(path, 'linux', monkeypatch) == 1


def test_wildcard_without_match_reports_error(workdir, monkeypatch):
    """通配模式无匹配时报导入错误 (不是静默通过)。"""
    _write(os.path.join(workdir, 'main.cin'),
           'import "./nothing/*.cin"\n'
           'function main() -> int { return 0 }\n')
    monkeypatch.setenv('CIN_TARGET_PLATFORM', 'linux')
    with pytest.raises(CompilerError) as info:
        run_cin_file(os.path.join(workdir, 'main.cin'))
    assert 'nothing/*.cin' in str(info.value)


def test_platform_suffix_is_not_stripped_for_other_platforms(workdir, monkeypatch):
    """显式导入别的平台文件时按普通文件处理 (不被误判为当前平台变体)。"""
    _write(os.path.join(workdir, 'q.cin'),
           'function qv() -> int { return 7 }\n')
    _write(os.path.join(workdir, 'main.cin'),
           'import "./q.cin"\n'
           'function main() -> int { return qv() }\n')
    assert _run(os.path.join(workdir, 'main.cin'), 'macos', monkeypatch) == 7
