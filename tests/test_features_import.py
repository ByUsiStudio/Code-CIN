"""B2: 字符串内建 (substr/indexof/upper/lower) 与 import/lib 模块化。"""

import os

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_file, run_cin_source

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'examples')


# ---------------- 字符串内建 ----------------

def test_substr_clamps_and_indexof():
    src = """
function main() -> int {
    string s = "abcdef"
    string t = substr(s, 1, 3)          // "bcd"
    int i1 = indexof(s, "cd")
    int i2 = indexof(s, "zz")
    int i3 = strlen(substr(s, 2, 99))   // 越界裁剪为 "cdef"
    return strlen(t) * 1000 + i1 * 100 + i2 + i3 * 0 + i3 * 1
}"""
    # 3*1000 + 2*100 + (-1) + 4 = 3203
    cpu = run_cin_source(src)
    assert cpu.regs.read(0) == 3203


def test_upper_lower():
    src = """
function main() -> int {
    string a = upper("aBc12")
    string b = lower("XyZ")
    return strlen(a) * 100 + strlen(b) * 10 + 1
}"""
    assert run_cin_source(src).regs.read(0) == 531


def test_string_builtins_three_paths():
    src = """
function main() -> int {
    string s = upper("hello world")
    int pos = indexof(s, "WORLD")
    string tail = substr(s, pos, 5)
    return strlen(tail) * 10 + strlen(lower("AB"))
}"""
    expected = 5 * 10 + 2
    assert run_cin_source(src, use_native=False).regs.read(0) == expected
    assert run_cin_source(src, use_native=False,
                          enable_jit=True).regs.read(0) == expected
    assert run_cin_source(src, use_native=True).regs.read(0) == expected


# ---------------- import ----------------

MOD_B = 'function mod_b(int x) -> int { return x * 2 }\n'
MOD_A = ('function mod_a(int x) -> int { return x + mod_b(x) }\n'
         'import "mod_b.cin"\n')


def test_import_single_file(workdir):
    with open(os.path.join(workdir, 'mod_b.cin'), 'w', encoding='utf-8') as f:
        f.write(MOD_B)
    with open(os.path.join(workdir, 'mod_a.cin'), 'w', encoding='utf-8') as f:
        f.write(MOD_A)
    with open(os.path.join(workdir, 'main.cin'), 'w', encoding='utf-8') as f:
        f.write('import "mod_a.cin"\n'
                'function main() -> int { return mod_a(10) }\n')
    cpu = run_cin_file(os.path.join(workdir, 'main.cin'))
    assert cpu.regs.read(0) == 30          # 10 + 2*10


def test_duplicate_import_is_idempotent(workdir):
    # 同一文件 import 两次仍能编译 (防重复包含)
    with open(os.path.join(workdir, 'mod_b.cin'), 'w', encoding='utf-8') as f:
        f.write(MOD_B)
    with open(os.path.join(workdir, 'main.cin'), 'w', encoding='utf-8') as f:
        f.write('import "mod_b.cin"\nimport "mod_b.cin"\n'
                'function main() -> int { return mod_b(5) }\n')
    cpu = run_cin_file(os.path.join(workdir, 'main.cin'))
    assert cpu.regs.read(0) == 10


def test_import_stdlib_math_and_str(workdir):
    src = ('import "math.cin"\nimport "str.cin"\n'
           'function main() -> int {\n'
           '    int a = f_floor(3.9)\n'
           '    int b = f_ceil(1.2)\n'
           '    int c = i_clamp(99, 0, 10)\n'
           '    int d = s_count("aaa", "aa")\n'
           '    int e = s_ends_with("readme.txt", ".txt")\n'
           '    int f = s_starts_with("hello", "he")\n'
           '    int g = strlen(s_repeat("ab", 3))\n'
           '    return (a*1000000 + b*100000 + c*10000 + d*1000'
           ' + e*100 + f*10 + g)\n'
           '}\n')
    p = os.path.join(workdir, 'libmain.cin')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(src)
    cpu = run_cin_file(p)
    # a=3 b=2 c=10 d=1 e=1 f=1 g=6
    assert cpu.regs.read(0) == 3301116


def test_circular_import_error(workdir):
    a = os.path.join(workdir, 'circ_a.cin')
    b = os.path.join(workdir, 'circ_b.cin')
    with open(a, 'w', encoding='utf-8') as f:
        f.write('import "circ_b.cin"\nfunction circ_a() -> int { return 1 }\n')
    with open(b, 'w', encoding='utf-8') as f:
        f.write('import "circ_a.cin"\nfunction circ_b() -> int { return 2 }\n')
    with pytest.raises(CompilerError):
        CINCompiler().compile(a)


def test_missing_import_error(workdir):
    p = os.path.join(workdir, 'missing.cin')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('import "no_such.cin"\nfunction main() -> int { return 0 }\n')
    with pytest.raises(CompilerError):
        CINCompiler().compile(p)


def test_example_modules_demo_runs():
    path = os.path.join(EXAMPLES, 'modules_demo.cin')
    cpu = run_cin_file(path)
    assert cpu.regs.read(0) == 12
