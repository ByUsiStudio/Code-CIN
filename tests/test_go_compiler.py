"""Go 版 CIN 编译器 + 独立 CLI 集成测试 (未编译 CLI 时跳过)。

Go CLI 构建: cd codecin/native && go build -o codecin ./cmd/codecin
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CANDIDATES = [
    os.path.join(ROOT, 'codecin', 'native', 'codecin.exe'),
    os.path.join(ROOT, 'codecin', 'native', 'codecin'),
    os.path.join(ROOT, 'codecin', 'codecin.exe'),
    os.path.join(ROOT, 'codecin', 'codecin'),
]
GO_CLI = next((p for p in _CANDIDATES if os.path.exists(p)), None)

needs_cli = pytest.mark.skipif(GO_CLI is None, reason="Go CLI not built")


def run_go(path):
    return subprocess.run([GO_CLI, path], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', cwd=ROOT,
                          timeout=180)


@needs_cli
def test_go_cli_basic_demo():
    r = run_go(os.path.join(ROOT, 'basic.cin'))
    assert r.returncode == 0, r.stderr
    assert 'factorial(5) = 120' in r.stdout
    assert 'DEMO COMPLETED SUCCESSFULLY' in r.stdout


@needs_cli
@pytest.mark.parametrize('name', ['control_flow.cin', 'literals_types.cin',
                                  'modules_demo.cin', 'bitwise_builtins.cin',
                                  'system_interaction.cin', 'stdlib_demo.cin'])
def test_go_cli_examples(name):
    # 全部示例编译并运行成功 (输出等价性由 test_go_cli_matches_python_compiler 校验)
    r = run_go(os.path.join(ROOT, 'examples', name))
    assert r.returncode == 0, r.stderr


@needs_cli
def test_go_cli_matches_python_compiler():
    """Go 编译 + Go VM 与 Python 编译 + Go VM 输出逐字节一致 (编译器等价)。

    注: basic.cin 使用 srand(time()), 输出依赖时钟, 由 test_go_cli_basic_demo
    以标记位方式校验, 不纳入逐字节比较。
    """
    sys.path.insert(0, os.path.join(ROOT, 'script'))
    import diff_go_python as d  # noqa: E402

    names = [os.path.join('examples', f) for f in (
        'control_flow.cin', 'literals_types.cin', 'modules_demo.cin',
        'bitwise_builtins.cin', 'system_interaction.cin', 'stdlib_demo.cin')]
    for name in names:
        path = os.path.join(ROOT, name)
        py_out, err = d.run_python(path)
        go_out, rc, ge = d.run_go(path)
        assert err is None, f'{name}: python error: {err}'
        assert rc == 0, f'{name}: go exit {rc}: {ge}'
        assert py_out == go_out, f'{name}: Go/Python compiler output differs'


@needs_cli
@pytest.mark.parametrize('name', [
    'control_flow.cin', 'literals_types.cin', 'modules_demo.cin',
    'bitwise_builtins.cin', 'system_interaction.cin', 'stdlib_demo.cin'])
def test_go_python_bytecode_is_identical(name):
    """编译产物级等价: UCBC 字节必须逐个相同 (比 stdout 比对强得多)。

    覆盖 docs/SUGGESTIONS.md §3.4 "把差分测试升级为产物级比对"。
    使用 Go CLI 的 --dump-bytecode 与 Python encode_program 的原始输出。
    """
    sys.path.insert(0, os.path.join(ROOT, 'script'))
    import diff_go_python as d  # noqa: E402

    path = os.path.join(ROOT, 'examples', name)
    py_bc = d.run_python_bytecode(path)
    go_bc, err = d.run_go_bytecode(path)
    assert go_bc is not None, f'{name}: go dump failed: {err}'
    assert py_bc == go_bc, f'{name}: {d._first_diff(py_bc, go_bc)}'
