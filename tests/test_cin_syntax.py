"""CIN 新语法特性测试: break/continue、do-while、switch、复合赋值、
自增自减、三目、类型别名/字面量、内建转换函数 (解释/JIT/原生三路径)。"""

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_source

SRC_FEATURE = """
function main() -> int {
    int sum = 0
    for (int i = 0; i < 5; i++) {
        if (i == 2) continue
        sum += i
        if (sum > 10) break
    }
    int d = 0
    do { d++ } while (d < 3)
    int s = 0
    switch (d) {
        case 1: s = 10; break
        case 2: s = 20; break
        case 3: s = 30; break
        default: s = 99
    }
    int t = (s > 20) ? 1 : 2
    s -= t
    return sum + d + s
}
"""


def test_feature_suite_three_paths():
    """复合特性组合: 解释 / JIT / 原生三路径结果一致 (=40)。"""
    expected = 40
    assert run_cin_source(SRC_FEATURE, use_native=False).regs.read(0) == expected
    assert run_cin_source(SRC_FEATURE, use_native=False,
                          enable_jit=True).regs.read(0) == expected
    assert run_cin_source(SRC_FEATURE, use_native=True).regs.read(0) == expected


def test_break_continue_semantics():
    src = """
function main() -> int {
    int total = 0
    for (int i = 0; i < 10; i++) {
        if (i % 2 == 1) continue
        total += i
        if (total > 6) break
    }
    return total
}"""
    # 0+2+4=6, +6=12 -> break; total=12
    assert run_cin_source(src).regs.read(0) == 12


def test_dowhile_runs_at_least_once():
    src = """
function main() -> int {
    int n = 0
    int guard = 0
    do {
        guard++
        n += guard
    } while (guard < 4)
    return n
}"""
    assert run_cin_source(src).regs.read(0) == 10


def test_switch_no_default_and_fallthrough():
    src = """
function main() -> int {
    int s = 0
    switch (2) {
        case 1: s = 1; break
        case 2:
        case 3: s = 100; break
        default: s = 999
    }
    int miss = 0
    switch (99) { case 1: miss = 1 }
    return s + miss
}"""
    assert run_cin_source(src).regs.read(0) == 100


def test_switch_break_does_not_break_outer_loop():
    src = """
function main() -> int {
    int total = 0
    for (int i = 0; i < 3; i++) {
        switch (i) {
            case 1: total += 10; break
            case 2: total += 20; break
            default: total += 1
        }
        total += 100
    }
    return total
}"""
    # i=0: +1 +100=101; i=1: +10+100=211; i=2:+20+100=331
    assert run_cin_source(src).regs.read(0) == 331


def test_compound_assign_and_mod():
    src = """
function main() -> int {
    int x = 100
    x /= 4
    x *= 3
    x -= 5
    x %= 7
    return x
}"""
    # 25 -> 75 -> 70 -> 0
    assert run_cin_source(src).regs.read(0) == 0


def test_prefix_postfix_incdec():
    src = """
function main() -> int {
    int a = 5
    int p = ++a
    int q = a++
    a--
    int r = --a
    return p * 100 + q * 10 + r
}"""
    # a:5 -> 6(p=6) -> q=6,a=7 -> a=6 -> r=5,a=5; return 665
    assert run_cin_source(src).regs.read(0) == 665


def test_float_compound():
    src = """
function main() -> int {
    float f = 1.5
    f += 2.0
    f *= 2.0
    f -= 1.0
    f /= 3.0
    int i = f
    return i
}"""
    # (1.5+2)=3.5 *2=7 -1=6 /3=2 -> 2
    assert run_cin_source(src).regs.read(0) == 2


def test_ternary_int_and_float():
    src = """
function main() -> int {
    int a = (2 > 1) ? 7 : 9
    float f = (1 < 0) ? 2.5 : 4.25
    int i = f
    return a + i
}"""
    assert run_cin_source(src).regs.read(0) == 11


def test_type_aliases_and_literals():
    src = """
function main() -> int {
    unsigned int u = 3000000000
    int h = 0xFF
    int b = 0b1010
    int o = 0o17
    char c = 'Z'
    short s = 2
    long l = 3
    int hi = u / 1000000000
    int low = h + b + o + s + l
    return hi * 1000 + low
}"""
    # hi=3; low=255+10+15+2+3=285 -> 3285
    assert run_cin_source(src).regs.read(0) == 3285


def test_char_literal_escapes():
    src = """
function main() -> int {
    char nl = '\\n'
    char q = '\\''
    char z = 'A'
    return nl + q + z
}"""
    # 10 + 39 + 65 = 114
    assert run_cin_source(src).regs.read(0) == 114


def test_string_conversion_builtins():
    src = """
function main() -> int {
    int a = strlen(int_to_str(42))
    int b = strlen(float_to_str(3.5))
    int c = strlen(bool_to_str(true))
    string x = int_to_str(-7)
    int first = 0
    if (strlen(x) > 0) { first = 1 }
    return a * 1000 + b * 100 + c * 10 + first
}"""
    cpu = run_cin_source(src)
    val = cpu.regs.read(0)
    # a=2, b>0, c=4, first=1
    assert val >= 2000 + 100 + 40 + 1
    assert (val // 1000) == 2


def test_number_underscores():
    src = """
function main() -> int {
    int a = 1_000
    int b = 0x1_0
    int c = 0b1_0_1_0
    return a + b + c
}"""
    assert run_cin_source(src).regs.read(0) == 1000 + 16 + 10


def test_break_outside_loop_is_compile_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { break }\n")


def test_continue_outside_loop_is_compile_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { continue }\n")


def test_switch_float_selector_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { float f = 1.5\n"
            "switch (f) { case 1: return 1 } return 0 }\n")


def test_switch_nonconst_case_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { int i = 3\n"
            "switch (i) { case i: return 1 } return 0 }\n")


def test_float_modulo_compound_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { float f = 3.0\nf %= 2.0\nreturn 0 }\n")


def test_do_without_while_is_parse_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source("function main() -> int { do { } }\n")
