"""CIN 新特性测试: 位运算符、整数除法 idiv、字符串单字符访问、
min/max、floor/ceil/round、atoi、trim/ltrim/rtrim (解释/JIT/原生三路径)。"""

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_source


# 解释路径默认 (use_native=False); 三路径一致见下方组合测试。
def test_bitwise_operators():
    src = """
function main() -> int {
    int a = 0b1100 & 0b1010
    int b = 0b1100 | 0b1010
    int c = 0b1100 ^ 0b1010
    int d = 1 << 4
    int e = ~0
    return a * 10000 + b * 1000 + c * 100 + d * 10 + (e == -1 ? 1 : 0)
}"""
    # a=8 b=14 c=6 d=16 e=-1 -> 80000+14000+600+160+1 = 94761
    assert run_cin_source(src).regs.read(0) == 94761


def test_arithmetic_shift_right():
    src = """
function main() -> int {
    int a = -16 >> 2
    int b = 16 >> 2
    return a + b
}"""
    # -4 + 4 = 0
    assert run_cin_source(src).regs.read(0) == 0
    # 同一程序在原生 VM 上同样成立 (回归: 原生 ASR 支持)
    assert run_cin_source(src, use_native=True).regs.read(0) == 0


def test_arithmetic_shift_right_prints():
    """>> 计算结果应可打印 (回归: 原生 VM 缺 ASR 会导致输出截断)。"""
    src = """
function main() -> int {
    int e = -16 >> 2
    println("asr=" + int_to_str(e))
    return 0
}"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cpu = run_cin_source(src, use_native=True)
    assert cpu.regs.read(0) == 0
    assert "asr=" in buf.getvalue()


def test_bitwise_compound_assign():
    src = """
function main() -> int {
    int g = 7
    g &= 3
    g |= 8
    g ^= 1
    g <<= 2
    g >>= 3
    return g
}"""
    # 7&3=3 |8=11 ^1=10 <<2=40 >>3=5
    assert run_cin_source(src).regs.read(0) == 5


def test_idiv_integer_division():
    src = """
function main() -> int {
    int a = idiv(17, 5)
    int b = idiv(-17, 5)
    int c = idiv(17, -5)
    return a + b + c
}"""
    # 3 + (-3) + (-3) = -3 (寄存器为 64 位无符号表示)
    val = run_cin_source(src).regs.read(0)
    if val >= (1 << 63):
        val -= (1 << 64)
    assert val == -3


def test_string_char_index():
    src = """
function main() -> int {
    string s = "Hello, World!"
    int a = s[0]
    int b = s[7]
    int c = s[12]
    return a * 1000 + b * 10 + c
}"""
    # 'H'=72 'W'=87 '!'=33 -> 72087 + 33? 72*1000=72000 + 87*10=870 + 33 = 72903
    assert run_cin_source(src).regs.read(0) == 72903


def test_min_max_int_and_float():
    src = """
function main() -> int {
    int a = min(5, 3)
    int b = max(5, 3)
    float lo = min(2.5, 1.5)
    float hi = max(2.5, 4.5)
    int li = lo
    int hi_i = hi
    return a * 10000 + b * 1000 + li * 100 + hi_i
}"""
    # a=3 b=5 lo=1.5->1 hi=4.5->4 => 3*10000 + 5*1000 + 1*100 + 4 = 35104
    assert run_cin_source(src).regs.read(0) == 35104


def test_floor_ceil_round():
    src = """
function main() -> int {
    int a = floor(3.7)
    int b = ceil(3.2)
    int c = round(2.5)
    int d = floor(-3.2)
    return a * 1000 + b * 100 + c * 10 + d
}"""
    # 3, 4, 3(floor(3.0)), -4 => 3*1000 + 4*100 + 3*10 + (-4) = 3426
    assert run_cin_source(src).regs.read(0) == 3426


def test_atoi():
    src = """
function main() -> int {
    int a = atoi(" 42 ")
    int b = atoi("-7")
    int c = atoi("abc")
    return a * 100 + b * 10 + c
}"""
    # 42*100 + (-7)*10 + 0 = 4130
    assert run_cin_source(src).regs.read(0) == 4130


def test_trim_builtins():
    src = """
function main() -> int {
    string t = "  hi  "
    int a = strlen(trim(t))
    int b = strlen(ltrim(t))
    int c = strlen(rtrim(t))
    string u = trim("x")
    int d = strcmp(u, "x")
    return a * 1000 + b * 100 + c * 10 + d
}"""
    # a=2 b=4 c=4 d=0 => 2400 + 40 + 0 = 2440
    assert run_cin_source(src).regs.read(0) == 2440


def test_bitwise_and_index_three_paths():
    """位运算/字符串下标/idiv/min-max 组合: 解释/JIT/原生三路径一致。"""
    src = """
function main() -> int {
    int a = (0b1111 & 0b1010) | (0b0001 ^ 0b0001)
    int b = 1 << 5
    int c = ~0
    string s = "ABC"
    int ch = s[1]
    int d = idiv(19, 4)
    int e = min(7, 9) + max(2, 6)
    int neg = c == -1 ? 1 : 0
    return a + b + ch + d + e + neg
}"""
    # a=10|0=10 b=32 c=-1 ch='B'=66 d=4 e=7+6=13 neg=1 => 126
    expected = 10 + 32 + 66 + 4 + 13 + 1
    assert run_cin_source(src, use_native=False).regs.read(0) == expected
    assert run_cin_source(src, use_native=False,
                          enable_jit=True).regs.read(0) == expected
    assert run_cin_source(src, use_native=True).regs.read(0) == expected


def test_bitwise_on_float_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { float f = 1.5\nint x = f & 3\nreturn 0 }\n")


def test_bitnot_on_float_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { float f = 1.5\nint x = ~f\nreturn 0 }\n")


def test_string_index_assign_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { string s = \"ab\"\ns[0] = 'c'\nreturn 0 }\n")


def test_bitwise_compound_on_float_is_error():
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(
            "function main() -> int { float f = 3.0\nf <<= 1\nreturn 0 }\n")


def test_utf8_bom_is_tolerated():
    src = "\ufefffunction main() -> int { return 42 }\n"
    assert run_cin_source(src).regs.read(0) == 42
