"""switch 语句语义回归测试 (Go 与 Python 双编译器对齐)。

覆盖 docs/SUGGESTIONS.md §3.1.2 / §3.1.3 修复的三类缺陷:
  1. Go 侧用 -1 当 default 哨兵, 导致 `case -1` 永远匹配不上 (Python 正确)。
  2. Go 侧非常量 case 只 continue 不补标签, 造成后续 case 标签与语句体错位。
  3. 两端 switch 内的 continue 绕过选择器弹出, 每轮迭代泄漏 8 字节栈。

断言策略: 程序把结果打印为 "R=<数字>", 两端都按 stdout 断言, 避免把 >255 的
返回值塞进进程退出码 (POSIX 下会被截断为低 8 位)。
"""

import os
import subprocess

import pytest

from codecin import CPU, Config
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


needs_go_cli = pytest.mark.skipif(_go_cli() is None, reason='Go CLI not built')


def _run_python(src: str, use_native: bool = True) -> str:
    """编译并运行, 返回程序 stdout。"""
    res = CINCompiler().compile_source(src)
    cfg = Config(interactive_mode=False, log_level='ERROR', use_native=use_native)
    cpu = CPU(cfg)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    cpu.run()
    return ''.join(cpu.output_buffer)


def _run_go(src: str, workdir: str):
    path = os.path.join(workdir, 'switch_probe.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    return subprocess.run([_go_cli(), path], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', cwd=ROOT, timeout=180)


NEGATIVE_CASE = '''
function main() -> int {
    int x = -1
    int r = 99
    switch (x) {
        case -1: r = 10 break
        case 1:  r = 20 break
        default: r = 30
    }
    println("R=" + int_to_str(r))
    return 0
}'''

DEFAULT_FIRST = '''
function main() -> int {
    int x = 7
    int r = 99
    switch (x) {
        default: r = 30 break
        case 7:  r = 70 break
    }
    println("R=" + int_to_str(r))
    return 0
}'''

NEGATIVE_AND_ZERO = '''
function main() -> int {
    int x = 0
    int r = 99
    switch (x) {
        case -1: r = 10 break
        case 0:  r = 20 break
        default: r = 30
    }
    println("R=" + int_to_str(r))
    return 0
}'''

NONCONST_CASE = '''
function main() -> int {
    int y = 2
    int x = 1
    int r = 0
    switch (x) {
        case y: r = 10 break
        case 1: r = 20 break
    }
    println("R=" + int_to_str(r))
    return 0
}'''

SWITCH_BREAK = '''
function main() -> int {
    int i = 0
    int n = 0
    while (i < 5000) {
        i = i + 1
        switch (i) {
            case 1: break
            default: n = n + 1
        }
    }
    println("R=" + int_to_str(n))
    return 0
}'''


def _switch_continue(n: int) -> str:
    return f'''
function main() -> int {{
    int i = 0
    int hits = 0
    while (i < {n}) {{
        i = i + 1
        switch (i) {{
            case 1: continue
            default: hits = hits + 1
        }}
    }}
    println("R=" + int_to_str(hits))
    return 0
}}'''


def _nested_switch_continue(n: int) -> str:
    return f'''
function main() -> int {{
    int i = 0
    int c = 0
    while (i < {n}) {{
        i = i + 1
        switch (i) {{
            case 1: continue
            default: {{
                switch (i) {{
                    case 2: continue
                    default: c = c + 1
                }}
            }}
        }}
    }}
    println("R=" + int_to_str(c))
    return 0
}}'''


# 语义正确性用例 (轻量, 三条执行路径都跑)
SEMANTIC_CASES = [
    ('negative_case', NEGATIVE_CASE, 'R=10'),
    ('default_first', DEFAULT_FIRST, 'R=70'),
    ('negative_and_zero', NEGATIVE_AND_ZERO, 'R=20'),
    ('switch_break', SWITCH_BREAK, 'R=4999'),
]

# 栈泄漏用例: 循环次数足以让"每轮泄漏 8 字节"撞上 32KB 处的堆 (旧实现在
# 这些次数下必然报 Stack overflow)。用生成器函数避免导入期求值。
LEAK_CASES = [
    ('switch_continue_20k', _switch_continue, 20000, 'R=19999'),
    ('nested_switch_continue', _nested_switch_continue, 10000, 'R=9998'),
]


@pytest.mark.parametrize('name,src,expected', SEMANTIC_CASES,
                         ids=[c[0] for c in SEMANTIC_CASES])
def test_switch_semantics_native(name, src, expected):
    """Python 编译器 + Go 原生 VM。"""
    assert expected in _run_python(src, use_native=True)


@pytest.mark.parametrize('name,src,expected', SEMANTIC_CASES,
                         ids=[c[0] for c in SEMANTIC_CASES])
def test_switch_semantics_interpreter(name, src, expected):
    """Python 编译器 + 纯解释执行 (两条执行路径必须一致)。"""
    assert expected in _run_python(src, use_native=False)


@pytest.mark.parametrize('name,src,expected', SEMANTIC_CASES,
                         ids=[c[0] for c in SEMANTIC_CASES])
@needs_go_cli
def test_switch_semantics_go_cli(name, src, expected, workdir):
    """Go 编译器 + Go VM 必须与 Python 侧给出相同结果。"""
    r = _run_go(src, workdir)
    assert r.returncode == 0, r.stderr
    assert expected in r.stdout, f'期望 {expected}, 实际 stdout={r.stdout!r}'


@pytest.mark.parametrize('name,make_src,n,expected', LEAK_CASES,
                         ids=[c[0] for c in LEAK_CASES])
def test_switch_continue_does_not_leak_stack_native(name, make_src, n, expected):
    """switch 内 continue 不得泄漏选择器栈槽 (原生路径)。"""
    assert expected in _run_python(make_src(n), use_native=True)


@pytest.mark.parametrize('name,make_src,n,expected', LEAK_CASES,
                         ids=[c[0] for c in LEAK_CASES])
@needs_go_cli
def test_switch_continue_does_not_leak_stack_go_cli(name, make_src, n, expected,
                                                    workdir):
    """Go 编译链路同样不得泄漏栈槽。"""
    r = _run_go(make_src(n), workdir)
    assert r.returncode == 0, r.stderr
    assert expected in r.stdout, f'期望 {expected}, 实际 stdout={r.stdout!r}'


def test_switch_continue_does_not_leak_stack_interpreter():
    """解释路径再校验一次 (次数调低以免拖慢测试套件)。"""
    assert 'R=5999' in _run_python(_switch_continue(6000), use_native=False)


def test_nonconstant_case_is_compile_error_python():
    """非常量 case 必须编译报错, 而不是静默生成错误分派。"""
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(NONCONST_CASE)
    assert 'constant' in str(ei.value).lower()


@needs_go_cli
def test_nonconstant_case_is_compile_error_go(workdir):
    """Go 侧同样必须拒绝非常量 case (此前会静默 miscompile)。"""
    r = _run_go(NONCONST_CASE, workdir)
    assert r.returncode != 0, f'Go 编译器接受了非常量 case: stdout={r.stdout!r}'
    assert 'constant' in (r.stderr + r.stdout).lower()
