"""codecin/lib/bigint.cin (任意精度整数) 测试。

每个用例的 CIN `main()` 返回 0 表示全部通过, 非 0 是具体失败点编号。
每个用例都在解释与 Go 原生两条路径上各跑一遍 (ids: interp / native)。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_bigint_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

def test_bigint_construct_and_print(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    if (bi_capacity() != 64) { return 1 }
    if (strcmp(bi_to_str(bi_zero()), "0") != 0) { return 2 }
    if (strcmp(bi_to_str(bi_from_int(0)), "0") != 0) { return 3 }
    if (strcmp(bi_to_str(bi_from_int(123)), "123") != 0) { return 4 }
    if (strcmp(bi_to_str(bi_from_int(-456)), "-456") != 0) { return 5 }
    if (strcmp(bi_to_str(bi_from_int(9223372036854775807)), "9223372036854775807") != 0) { return 6 }
    if (strcmp(bi_to_str(bi_from_str("0")), "0") != 0) { return 7 }
    if (strcmp(bi_to_str(bi_from_str("+42")), "42") != 0) { return 8 }
    if (strcmp(bi_to_str(bi_from_str("-0007")), "-7") != 0) { return 9 }
    if (strcmp(bi_to_str(bi_from_str("12ab")), "12") != 0) { return 10 }
    if (strcmp(bi_to_str(bi_from_str("abc")), "0") != 0) { return 11 }
    if (strcmp(bi_to_str(bi_from_str("")), "0") != 0) { return 12 }
    if (strcmp(bi_to_str(bi_from_str("-")), "0") != 0) { return 13 }
    if (strcmp(bi_to_str(bi_from_str("-0")), "0") != 0) { return 14 }
    if (bi_digits(bi_from_str("1000")) != 4) { return 15 }
    if (bi_digits(bi_from_str("000")) != 1) { return 16 }
    if (bi_digits(bi_zero()) != 1) { return 17 }
    if (bi_is_zero(bi_from_str("0")) != 1) { return 18 }
    if (bi_is_zero(bi_from_int(7)) != 0) { return 19 }
    if (bi_is_negative(bi_from_int(-5)) != 1) { return 20 }
    if (bi_is_negative(bi_from_int(5)) != 0) { return 21 }
    if (bi_is_negative(bi_from_int(0)) != 0) { return 22 }
    if (bi_sign(bi_from_int(-5)) != -1) { return 23 }
    if (bi_sign(bi_from_int(0)) != 0) { return 24 }
    if (bi_sign(bi_from_int(5)) != 1) { return 25 }
    BigInt a = bi_from_int(1000)
    BigInt b = bi_zero()
    bi_copy(b, a)
    if (bi_cmp(a, b) != 0) { return 26 }
    bi_clear(b)
    if (bi_is_zero(b) != 1) { return 27 }
    if (bi_overflow() != 0) { return 28 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_compare(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    BigInt a = bi_from_str("100000000000000000000")
    BigInt b = bi_from_str("-100000000000000000000")
    if (bi_cmp(a, b) != 1) { return 1 }
    if (bi_cmp(b, a) != -1) { return 2 }
    if (bi_cmp(a, a) != 0) { return 3 }
    if (bi_cmp(b, b) != 0) { return 4 }
    if (bi_cmp(bi_zero(), b) != 1) { return 5 }
    if (bi_cmp(b, bi_zero()) != -1) { return 6 }
    if (bi_cmp(bi_zero(), bi_zero()) != 0) { return 7 }
    if (bi_cmp(bi_zero(), a) != -1) { return 8 }
    if (bi_cmp(a, bi_zero()) != 1) { return 9 }
    BigInt c = bi_from_int(-99)
    BigInt d = bi_from_int(-100)
    if (bi_cmp(c, d) != 1) { return 10 }
    if (bi_cmp(d, c) != -1) { return 11 }
    if (bi_cmp_mag(a, b) != 0) { return 12 }
    if (bi_cmp_mag(c, d) != -1) { return 13 }
    if (bi_cmp_mag(d, c) != 1) { return 14 }
    if (bi_cmp_mag(bi_zero(), bi_zero()) != 0) { return 15 }
    if (bi_cmp(bi_from_str("0005"), bi_from_int(5)) != 0) { return 16 }
    if (bi_cmp(bi_from_str("100"), bi_from_str("99")) != 1) { return 17 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_add_sub(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    BigInt a = bi_from_str("100")
    BigInt b = bi_from_int(23)
    BigInt c = bi_from_int(-100)
    if (strcmp(bi_to_str(bi_add(a, b)), "123") != 0) { return 1 }
    if (strcmp(bi_to_str(bi_sub(a, b)), "77") != 0) { return 2 }
    if (strcmp(bi_to_str(bi_sub(b, a)), "-77") != 0) { return 3 }
    if (strcmp(bi_to_str(bi_add(c, b)), "-77") != 0) { return 4 }
    if (strcmp(bi_to_str(bi_sub(c, b)), "-123") != 0) { return 5 }
    if (strcmp(bi_to_str(bi_add(a, c)), "0") != 0) { return 6 }
    if (strcmp(bi_to_str(bi_sub(b, c)), "123") != 0) { return 7 }
    if (strcmp(bi_to_str(bi_add(c, c)), "-200") != 0) { return 8 }
    if (strcmp(bi_to_str(bi_sub(c, c)), "0") != 0) { return 9 }
    if (strcmp(bi_to_str(bi_add(b, bi_zero())), "23") != 0) { return 10 }
    if (strcmp(bi_to_str(bi_sub(bi_zero(), b)), "-23") != 0) { return 11 }
    if (strcmp(bi_to_str(bi_neg(a)), "-100") != 0) { return 12 }
    if (strcmp(bi_to_str(bi_neg(c)), "100") != 0) { return 13 }
    if (strcmp(bi_to_str(bi_neg(bi_zero())), "0") != 0) { return 14 }
    if (strcmp(bi_to_str(bi_abs(c)), "100") != 0) { return 15 }
    if (strcmp(bi_to_str(bi_abs(a)), "100") != 0) { return 16 }
    // 跨数位的进位 / 借位
    BigInt big = bi_from_str("99999999999999999999")
    BigInt one = bi_from_int(1)
    if (strcmp(bi_to_str(bi_add(big, one)), "100000000000000000000") != 0) { return 17 }
    if (strcmp(bi_to_str(bi_sub(bi_add(big, one), one)), "99999999999999999999") != 0) { return 18 }
    if (bi_cmp(bi_sub(bi_add(big, one), one), big) != 0) { return 19 }
    if (strcmp(bi_to_str(bi_add(big, big)), "199999999999999999998") != 0) { return 20 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_mul(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    BigInt a = bi_from_str("99999999999999999999")
    BigInt p = bi_mul(a, a)
    if (strcmp(bi_to_str(p), "9999999999999999999800000000000000000001") != 0) { return 1 }
    if (bi_digits(p) != 40) { return 2 }
    BigInt x = bi_from_str("12345678901234567890")
    BigInt y = bi_from_str("98765432109876543210")
    if (strcmp(bi_to_str(bi_mul(x, y)), "1219326311370217952237463801111263526900") != 0) { return 3 }
    BigInt n = bi_from_int(-3)
    BigInt m = bi_from_int(4)
    if (strcmp(bi_to_str(bi_mul(n, m)), "-12") != 0) { return 4 }
    if (strcmp(bi_to_str(bi_mul(n, n)), "9") != 0) { return 5 }
    if (strcmp(bi_to_str(bi_mul(n, bi_zero())), "0") != 0) { return 6 }
    if (strcmp(bi_to_str(bi_mul(bi_from_int(99), bi_from_int(99))), "9801") != 0) { return 7 }
    if (strcmp(bi_to_str(bi_mul(bi_from_int(123456789), bi_from_int(987654321))), "121932631112635269") != 0) { return 8 }
    if (strcmp(bi_to_str(bi_mul_small(bi_from_int(12), -3)), "-36") != 0) { return 9 }
    if (strcmp(bi_to_str(bi_mul_small(bi_from_str("999999999999999999999"), 0)), "0") != 0) { return 10 }
    if (strcmp(bi_to_str(bi_mul_small(bi_from_int(1), 999999999)), "999999999") != 0) { return 11 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_fact_golden(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    if (strcmp(bi_to_str(bi_fact(0)), "1") != 0) { return 1 }
    if (strcmp(bi_to_str(bi_fact(1)), "1") != 0) { return 2 }
    if (strcmp(bi_to_str(bi_fact(5)), "120") != 0) { return 3 }
    if (strcmp(bi_to_str(bi_fact(10)), "3628800") != 0) { return 4 }
    BigInt f20 = bi_fact(20)
    if (strcmp(bi_to_str(f20), "2432902008176640000") != 0) { return 5 }
    if (bi_digits(f20) != 19) { return 6 }
    BigInt f25 = bi_fact(25)
    if (strcmp(bi_to_str(f25), "15511210043330985984000000") != 0) { return 7 }
    if (bi_digits(f25) != 26) { return 8 }
    if (bi_digits(bi_fact(30)) != 33) { return 9 }
    if (strcmp(bi_to_str(bi_fact(-3)), "1") != 0) { return 10 }
    if (bi_overflow() != 0) { return 11 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_pow_golden(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    if (strcmp(bi_to_str(bi_pow(bi_from_int(2), 10)), "1024") != 0) { return 1 }
    BigInt p64 = bi_pow(bi_from_int(2), 64)
    if (strcmp(bi_to_str(p64), "18446744073709551616") != 0) { return 2 }
    if (bi_digits(p64) != 20) { return 3 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(10), 20)), "100000000000000000000") != 0) { return 4 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(-7), 5)), "-16807") != 0) { return 5 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(-7), 4)), "2401") != 0) { return 6 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(-7), 0)), "1") != 0) { return 7 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(-7), -3)), "1") != 0) { return 8 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(0), 5)), "0") != 0) { return 9 }
    if (strcmp(bi_to_str(bi_pow(bi_from_int(2), 200)), "1606938044258990275541962092341162602522202993782792835301376") != 0) { return 10 }
    if (bi_overflow() != 0) { return 11 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_overflow_saturates(workdir):
    src = '''
import "bigint.cin"

function main() -> int {
    bi_reset_overflow()
    if (bi_overflow() != 0) { return 1 }
    BigInt cap = bi_from_str("9999999999999999999999999999999999999999999999999999999999999999")
    if (bi_digits(cap) != 64) { return 2 }
    if (bi_is_saturated(cap) != 1) { return 3 }
    if (bi_overflow() != 0) { return 4 }
    BigInt one = bi_from_int(1)
    BigInt o = bi_add(cap, one)
    if (bi_overflow() != 1) { return 5 }
    if (bi_is_saturated(o) != 1) { return 6 }
    // 饱和值继续参与运算仍然是饱和值, 不崩溃
    if (bi_is_saturated(bi_mul(o, o)) != 1) { return 7 }
    if (bi_is_saturated(bi_add(o, o)) != 1) { return 8 }
    bi_reset_overflow()
    if (bi_overflow() != 0) { return 9 }
    BigInt f = bi_fact(55)
    if (bi_overflow() != 1) { return 10 }
    if (bi_is_saturated(f) != 1) { return 11 }
    // 65 位十进制字符串 -> 饱和
    bi_reset_overflow()
    BigInt s65 = bi_from_str("1234567890123456789012345678901234567890123456789012345678901234567890")
    if (bi_overflow() != 1) { return 12 }
    if (bi_is_saturated(s65) != 1) { return 13 }
    // 恰好 64 位 -> 不溢出
    bi_reset_overflow()
    BigInt s64 = bi_from_str("1234567890123456789012345678901234567890123456789012345678901234")
    if (bi_overflow() != 0) { return 14 }
    if (bi_digits(s64) != 64) { return 15 }
    // 乘法溢出: 32 位十进制 x 32 位十进制 = 65 位
    bi_reset_overflow()
    BigInt h = bi_from_str("100000000000000000000000000000000")
    BigInt prod = bi_mul(h, h)
    if (bi_overflow() != 1) { return 16 }
    if (bi_is_saturated(prod) != 1) { return 17 }
    // 乘法不溢出: 21 位十进制 x 21 位十进制 = 41 位, 结果为 10^40
    bi_reset_overflow()
    BigInt h2 = bi_from_str("100000000000000000000")
    BigInt prod2 = bi_mul(h2, h2)
    if (bi_overflow() != 0) { return 18 }
    if (bi_digits(prod2) != 41) { return 19 }
    if (strcmp(bi_to_str(prod2), "10000000000000000000000000000000000000000") != 0) { return 20 }
    bi_reset_overflow()
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_into_helpers_avoid_growth(workdir):
    """*_into 形式只写调用方提供的工作区, 长循环不产生新堆块。"""
    src = '''
import "bigint.cin"

function main() -> int {
    BigInt acc = bi_from_int(1)
    BigInt w = bi_zero()
    BigInt one = bi_from_int(1)
    BigInt tmp = bi_zero()
    for (int i = 0; i < 30; i = i + 1) {
        bi_mul_small_into(acc, 1, w)
        if (bi_cmp(w, acc) != 0) { return 1 }
        bi_add_into(acc, one, tmp)
        bi_sub_into(tmp, one, w)
        if (bi_cmp(w, acc) != 0) { return 2 }
    }
    bi_norm(acc)
    if (bi_digits(acc) != 1) { return 3 }
    bi_abs_into(bi_from_int(-5), w)
    if (strcmp(bi_to_str(w), "5") != 0) { return 4 }
    bi_neg_into(w, tmp)
    if (strcmp(bi_to_str(tmp), "-5") != 0) { return 5 }
    bi_abs_into(tmp, acc)
    if (strcmp(bi_to_str(acc), "5") != 0) { return 6 }
    bi_copy(w, acc)
    if (bi_cmp(w, acc) != 0) { return 7 }
    bi_mul_into(bi_from_int(12), bi_from_int(12), tmp)
    if (strcmp(bi_to_str(tmp), "144") != 0) { return 8 }
    bi_mul_small_into(bi_from_int(-12), 3, tmp)
    if (strcmp(bi_to_str(tmp), "-36") != 0) { return 9 }
    if (bi_capacity() != 64 || bi_digits(bi_zero()) != 1) { return 10 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0

def test_bigint_and_frac_coexist(workdir):
    """两个新库同时导入: 全局符号与 struct 名不得冲突。"""
    src = '''
import "bigint.cin"
import "frac.cin"

function main() -> int {
    if (strcmp(bi_to_str(bi_fact(10)), "3628800") != 0) { return 1 }
    if (strcmp(fr_to_str(fr_make(2, 4)), "1/2") != 0) { return 2 }
    if (strcmp(fr_to_str(fr_add(fr_make(1, 3), fr_make(1, 6))), "1/2") != 0) { return 3 }
    if (bi_overflow() != 0 || fr_error() != 0) { return 4 }
    return 0
}
'''
    assert _run(workdir, src).regs.read(0) == 0
