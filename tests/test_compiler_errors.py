"""编译器报错一致性: 非法程序必须被 Go 与 Python 两侧同时拒绝。

背景 (docs/SUGGESTIONS.md §3.1.4): Go 代码生成原先没有错误返回通道,
语义错误分支只 return/continue, 会把非法程序编译成"静默错误的字节码"
并正常退出 (例如 println(foo()) 打印 0、~1.5 打印 float 位模式)。
本测试保证:
  1. 非法程序在两侧都被拒绝 (不再是静默错算);
  2. 合法程序在两侧都仍然被接受 (防止把检查做得过严)。
"""

import os
import subprocess

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GO_CLI_CANDIDATES = (
    'codecin/native/codecin.exe', 'codecin/native/codecin',
    'codecin/codecin.exe', 'codecin/codecin',
)


def _go_cli():
    for rel in GO_CLI_CANDIDATES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            return p
    return None


needs_go_cli = pytest.mark.skipif(
    _go_cli() is None,
    reason='Go CLI 已于 5.5.0 下线 (Python 是唯一 CLI 入口); '
           '待 Go 编译器经原生库暴露后恢复双编译器对照')


def _run_go(src, workdir, name='probe.cin'):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    return subprocess.run([_go_cli(), path], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', cwd=ROOT, timeout=180)


REJECT = [
    ('unknown_function', '''
function main() -> int {
    println(foo())
    return 0
}'''),
    ('member_on_int', '''
function main() -> int {
    int x = 1
    println(int_to_str(x.foo))
    return 0
}'''),
    ('bitnot_float', '''
function main() -> int {
    println(float_to_str(~1.5))
    return 0
}'''),
    ('float_mod', '''
function main() -> int {
    println(float_to_str(7.5 % 2.0))
    return 0
}'''),
    ('float_bitwise_and', '''
function main() -> int {
    int x = 0
    if (1.5 & 1) { x = 1 }
    return x
}'''),
    ('switch_float', '''
function main() -> int {
    float f = 1.5
    switch (f) {
        case 1: return 1
    }
    return 0
}'''),
    ('undefined_var', '''
function main() -> int {
    zzz = 1
    return 0
}'''),
    ('index_non_array', '''
function main() -> int {
    int x = 1
    println(int_to_str(x[0]))
    return 0
}'''),
    ('string_compound_assign', '''
function main() -> int {
    string s = "a"
    s += "b"
    println(s)
    return 0
}'''),
    ('struct_no_such_field', '''
struct P { int x }
function main() -> int {
    P p
    p.x = 1
    return p.y
}'''),
    ('break_outside_loop', '''
function main() -> int {
    break
    return 0
}'''),
    ('continue_outside_loop', '''
function main() -> int {
    continue
    return 0
}'''),
    ('sqrt_no_args', '''
function main() -> int {
    println(float_to_str(sqrt()))
    return 0
}'''),
    ('substr_too_few_args', '''
function main() -> int {
    println(substr("abc", 1))
    return 0
}'''),
    ('pow_one_arg', '''
function main() -> int {
    println(float_to_str(pow(2.0)))
    return 0
}'''),
    ('min_one_arg', '''
function main() -> int {
    println(int_to_str(min(1)))
    return 0
}'''),
    ('atoi_no_args', '''
function main() -> int {
    println(int_to_str(atoi()))
    return 0
}'''),
]

# 合法程序: 检查不能被"做过头"的校验误杀
ACCEPT = [
    ('switch_basic', '''
function main() -> int {
    int x = 1
    switch (x) {
        case 1: return 5
        default: return 6
    }
    return 0
}'''),
    ('switch_negative_case', '''
function main() -> int {
    int x = -1
    switch (x) {
        case -1: return 5
        default: return 6
    }
    return 0
}'''),
    ('switch_continue_ok', '''
function main() -> int {
    int i = 0
    int n = 0
    while (i < 10) {
        i = i + 1
        switch (i) {
            case 1: continue
            default: n = n + 1
        }
    }
    return n
}'''),
    ('break_continue_in_loop', '''
function main() -> int {
    int i = 0
    while (i < 10) {
        i = i + 1
        if (i == 3) { continue }
        if (i == 7) { break }
    }
    return i
}'''),
    ('bitwise_ints', '''
function main() -> int {
    int a = 6
    int b = 3
    return (a & b) + (a | b) + (a ^ b) + (a << 1) + (a >> 1) + (~a)
}'''),
    ('minmax_and_math', '''
function main() -> int {
    return min(3, 4) + max(1, 2) + abs(0 - 5)
}'''),
    ('extra_builtin_args_ok', '''
function main() -> int {
    return strlen("ab", 9)
}'''),
    ('user_fn_fewer_args_ok', '''
function f(int a, int b) -> int {
    return a
}
function main() -> int {
    return f(1)
}'''),
]


@pytest.mark.parametrize('name,src', REJECT, ids=[c[0] for c in REJECT])
def test_rejected_by_python(name, src):
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(src)


@pytest.mark.parametrize('name,src', REJECT, ids=[c[0] for c in REJECT])
@needs_go_cli
def test_rejected_by_go(name, src, workdir):
    r = _run_go(src, workdir)
    assert r.returncode != 0, (
        f'Go 编译器接受了非法程序 {name}: stdout={r.stdout!r}')
    assert 'compile error' in r.stderr.lower(), (
        f'Go 侧 {name} 的报错不像编译错误: stderr={r.stderr!r}')


@pytest.mark.parametrize('name,src', ACCEPT, ids=[c[0] for c in ACCEPT])
def test_accepted_by_python(name, src):
    CINCompiler().compile_source(src)


@pytest.mark.parametrize('name,src', ACCEPT, ids=[c[0] for c in ACCEPT])
@needs_go_cli
def test_accepted_by_go(name, src, workdir):
    r = _run_go(src, workdir)
    assert r.returncode == 0, (
        f'Go 编译器拒绝了合法程序 {name}: stderr={r.stderr!r}')
