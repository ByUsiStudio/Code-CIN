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
                                  'modules_demo.cin', 'bitwise_builtins.cin'])
def test_go_cli_examples(name):
    # 全部示例编译并运行成功 (输出等价性由 test_go_cli_matches_python_compiler 校验)
    r = run_go(os.path.join(ROOT, 'examples', name))
    assert r.returncode == 0, r.stderr


@needs_cli
def test_go_cli_matches_python_compiler():
    """Go 编译 + Go VM 与 Python 编译 + Go VM 输出逐字节一致 (编译器等价)。"""
    sys.path.insert(0, os.path.join(ROOT, 'script'))
    import diff_go_python as d  # noqa: E402

    names = ['basic.cin'] + [os.path.join('examples', f) for f in (
        'control_flow.cin', 'literals_types.cin', 'modules_demo.cin',
        'bitwise_builtins.cin')]
    for name in names:
        path = os.path.join(ROOT, name)
        py_out, err = d.run_python(path)
        go_out, rc, ge = d.run_go(path)
        assert err is None, f'{name}: python error: {err}'
        assert rc == 0, f'{name}: go exit {rc}: {ge}'
        assert py_out == go_out, f'{name}: Go/Python compiler output differs'
