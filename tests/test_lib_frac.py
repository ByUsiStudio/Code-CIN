"""codecin/lib/frac.cin (有理数) 测试。

每个用例的 CIN `main()` 返回 0 表示全部通过, 非 0 是具体失败点编号。
每个用例都在解释与 Go 原生两条路径上各跑一遍 (ids: interp / native)。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_frac_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

def test_frac_construct_normalizes(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    if (strcmp(fr_to_str(fr_make(2, 4)), "1/2") != 0) { return 1 }
    if (strcmp(fr_to_str(fr_make(3, -4)), "-3/4") != 0) { return 2 }
    if (strcmp(fr_to_str(fr_make(-3, -4)), "3/4") != 0) { return 3 }
    if (strcmp(fr_to_str(fr_make(-6, 8)), "-3/4") != 0) { return 4 }
    if (strcmp(fr_to_str(fr_make(0, 5)), "0") != 0) { return 5 }
    if (strcmp(fr_to_str(fr_make(6, 3)), "2") != 0) { return 6 }
    if (strcmp(fr_to_str(fr_make(-8, 2)), "-4") != 0) { return 7 }
    if (strcmp(fr_to_str(fr_make(7, 1)), "7") != 0) { return 8 }
    if (strcmp(fr_to_str(fr_zero()), "0") != 0) { return 9 }
    if (fr_num(fr_make(6, 8)) != 3) { return 10 }
    if (fr_den(fr_make(6, 8)) != 4) { return 11 }
    if (fr_num(fr_make(3, -4)) != -3) { return 12 }
    if (fr_den(fr_make(3, -4)) != 4) { return 13 }
    if (fr_num(fr_zero()) != 0 || fr_den(fr_zero()) != 1) { return 14 }
    if (fr_gcd(12, 18) != 6) { return 15 }
    if (fr_gcd(-12, 18) != 6) { return 16 }
    if (fr_gcd(12, -18) != 6) { return 17 }
    if (fr_gcd(0, 5) != 5) { return 18 }
    if (fr_gcd(7, 13) != 1) { return 19 }
    if (fr_is_int(fr_make(4, 2)) != 1) { return 20 }
    if (fr_is_int(fr_make(4, 3)) != 0) { return 21 }
    if (fr_is_int(fr_zero()) != 1) { return 22 }
    if (fr_is_int(fr_make(-6, 3)) != 1) { return 23 }
    if (fr_error() != 0) { return 24 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_frac_arith_golden(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    Frac a = fr_make(1, 3)
    Frac b = fr_make(1, 6)
    if (strcmp(fr_to_str(fr_add(a, b)), "1/2") != 0) { return 1 }
    if (strcmp(fr_to_str(fr_sub(a, b)), "1/6") != 0) { return 2 }
    if (strcmp(fr_to_str(fr_sub(b, a)), "-1/6") != 0) { return 3 }
    if (strcmp(fr_to_str(fr_mul(a, b)), "1/18") != 0) { return 4 }
    if (strcmp(fr_to_str(fr_div(a, b)), "2") != 0) { return 5 }
    if (strcmp(fr_to_str(fr_neg(a)), "-1/3") != 0) { return 6 }
    if (strcmp(fr_to_str(fr_neg(fr_make(-5, 7))), "5/7") != 0) { return 7 }
    if (strcmp(fr_to_str(fr_neg(fr_zero())), "0") != 0) { return 8 }
    if (strcmp(fr_to_str(fr_abs(fr_make(-3, 7))), "3/7") != 0) { return 9 }
    if (strcmp(fr_to_str(fr_abs(fr_make(3, 7))), "3/7") != 0) { return 10 }
    if (strcmp(fr_to_str(fr_add(fr_make(1, 2), fr_make(1, 2))), "1") != 0) { return 11 }
    if (strcmp(fr_to_str(fr_sub(fr_make(1, 2), fr_make(1, 2))), "0") != 0) { return 12 }
    if (strcmp(fr_to_str(fr_mul(fr_make(-2, 3), fr_make(3, 4))), "-1/2") != 0) { return 13 }
    if (strcmp(fr_to_str(fr_div(fr_make(-1, 2), fr_make(1, 4))), "-2") != 0) { return 14 }
    if (strcmp(fr_to_str(fr_add(fr_make(1, 4), fr_make(1, 4))), "1/2") != 0) { return 15 }
    if (strcmp(fr_to_str(fr_div(fr_make(3, 4), fr_make(3, 4))), "1") != 0) { return 16 }
    if (strcmp(fr_to_str(fr_mul(fr_zero(), fr_make(5, 7))), "0") != 0) { return 17 }
    if (strcmp(fr_to_str(fr_add(fr_make(1, 6), fr_make(1, 3))), "1/2") != 0) { return 18 }
    if (fr_error() != 0) { return 19 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_frac_compare_equals(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    if (fr_cmp(fr_make(1, 3), fr_make(1, 2)) != -1) { return 1 }
    if (fr_cmp(fr_make(1, 2), fr_make(1, 3)) != 1) { return 2 }
    if (fr_cmp(fr_make(2, 4), fr_make(1, 2)) != 0) { return 3 }
    if (fr_cmp(fr_make(-1, 3), fr_make(1, 3)) != -1) { return 4 }
    if (fr_cmp(fr_make(-1, 3), fr_make(-1, 2)) != 1) { return 5 }
    if (fr_cmp(fr_make(-1, 2), fr_make(-1, 3)) != -1) { return 6 }
    if (fr_cmp(fr_zero(), fr_make(1, 1000000)) != -1) { return 7 }
    if (fr_cmp(fr_make(1, 1000000), fr_zero()) != 1) { return 8 }
    if (fr_cmp(fr_zero(), fr_zero()) != 0) { return 9 }
    if (fr_cmp(fr_make(-1, 4), fr_zero()) != -1) { return 10 }
    if (fr_equals(fr_make(2, 4), fr_make(1, 2)) != 1) { return 11 }
    if (fr_equals(fr_make(1, 3), fr_make(1, 2)) != 0) { return 12 }
    if (fr_equals(fr_make(-2, 4), fr_make(1, -2)) != 1) { return 13 }
    if (fr_equals(fr_zero(), fr_make(0, 9)) != 1) { return 14 }
    if (fr_equals(fr_make(3, 1), fr_make(3, 1)) != 1) { return 15 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_frac_zero_denominator_error(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    fr_reset_error()
    if (fr_error() != 0) { return 1 }
    Frac z = fr_make(5, 0)
    if (fr_error() != 1) { return 2 }
    if (strcmp(fr_to_str(z), "0") != 0) { return 3 }
    if (fr_num(z) != 0 || fr_den(z) != 1) { return 4 }
    fr_reset_error()
    if (fr_error() != 0) { return 5 }
    if (strcmp(fr_to_str(fr_make(2, 0)), "0") != 0) { return 6 }
    if (fr_error() != 1) { return 7 }
    fr_reset_error()
    Frac d = fr_div(fr_make(1, 2), fr_zero())
    if (fr_error() != 1) { return 8 }
    if (strcmp(fr_to_str(d), "0") != 0) { return 9 }
    if (strcmp(fr_to_str(fr_make(0, 0)), "0") != 0) { return 10 }
    if (fr_error() != 1) { return 11 }
    fr_reset_error()
    if (fr_error() != 0) { return 12 }
    // 正常运算不置错误标志
    if (strcmp(fr_to_str(fr_add(fr_make(1, 2), fr_make(1, 2))), "1") != 0) { return 13 }
    if (fr_error() != 0) { return 14 }
    if (strcmp(fr_to_str(fr_div(fr_make(1, 3), fr_make(1, 6))), "2") != 0) { return 15 }
    if (fr_error() != 0) { return 16 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_frac_to_float(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    if (fr_to_float(fr_make(1, 4)) != 0.25) { return 1 }
    if (fr_to_float(fr_make(-3, 2)) != -1.5) { return 2 }
    if (fr_to_float(fr_make(7, 2)) != 3.5) { return 3 }
    if (fr_to_float(fr_zero()) != 0.0) { return 4 }
    if (fr_to_float(fr_make(1, 3)) > 0.34) { return 5 }
    if (fr_to_float(fr_make(1, 3)) < 0.33) { return 6 }
    if (fr_to_float(fr_make(10, 4)) != 2.5) { return 7 }
    if (fr_to_float(fr_make(-8, 2)) != -4.0) { return 8 }
    if (fr_to_float(fr_make(0, 3)) != 0.0) { return 9 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_frac_loops_keep_exact(workdir):
    src = '''
import "frac.cin"

function main() -> int {
    // 调和级数前 10 项: 1/1 + 1/2 + ... + 1/10 = 7381/2520
    Frac s = fr_zero()
    for (int i = 1; i <= 10; i = i + 1) {
        s = fr_add(s, fr_make(1, i))
    }
    if (strcmp(fr_to_str(s), "7381/2520") != 0) { return 1 }
    if (fr_num(s) != 7381) { return 2 }
    if (fr_den(s) != 2520) { return 3 }
    // 望远镜乘积: (1/2)(2/3)...(6/7) = 1/7
    Frac p = fr_make(1, 1)
    for (int i = 1; i <= 6; i = i + 1) {
        p = fr_mul(p, fr_make(i, i + 1))
    }
    if (strcmp(fr_to_str(p), "1/7") != 0) { return 4 }
    // 1/2 + 1/3 + 1/6 = 1
    Frac t = fr_add(fr_add(fr_make(1, 2), fr_make(1, 3)), fr_make(1, 6))
    if (strcmp(fr_to_str(t), "1") != 0) { return 5 }
    if (fr_is_int(t) != 1) { return 6 }
    if (fr_error() != 0) { return 7 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0
