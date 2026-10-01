"""官方标准库 combin.cin (数论与组合数学) 测试。

覆盖: 每个公开函数至少一个用例 + 边界 (负数 / 0 / 1 / 越界 / 溢出范围 / 非法进制)。
黄金用例 (可独立验算):
  comb_gcd(48, 18) = 6, comb_choose(5, 2) = 10, comb_catalan(5) = 42,
  comb_sieve(100) = 25 (π(100) = 25), comb_fact(10) = 3628800,
  comb_to_base(255, 16) = "FF", comb_fib(20) = 6765 (F(20)),
  comb_divisor_sum(12) = 28, comb_collatz_steps(27) = 111,
  comb_is_perfect(28) = 1, comb_is_armstrong(153) = 1。

CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
字符串断言用 strcmp (CIN 的 == 比较的是指针)。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_combin_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


COMBIN_SRC = '''
import "combin.cin"

function main() -> int {
    // ---- comb_gcd / comb_lcm / comb_gcd3 ----
    if (comb_gcd(48, 18) != 6) { return 1 }
    if (comb_gcd(18, 48) != 6) { return 2 }
    if (comb_gcd(17, 5) != 1) { return 3 }
    if (comb_gcd(-48, 18) != 6) { return 4 }
    if (comb_gcd(0, 5) != 5) { return 5 }
    if (comb_gcd(0, 0) != 0) { return 6 }
    if (comb_gcd(7, 7) != 7) { return 7 }
    if (comb_lcm(4, 6) != 12) { return 8 }
    if (comb_lcm(21, 6) != 42) { return 9 }
    if (comb_lcm(0, 5) != 0) { return 10 }
    if (comb_lcm(-4, 6) != 12) { return 11 }
    if (comb_gcd3(12, 18, 30) != 6) { return 12 }
    if (comb_gcd3(7, 13, 29) != 1) { return 13 }
    if (comb_gcd3(0, 0, 9) != 9) { return 14 }

    // ---- comb_is_prime ----
    if (comb_is_prime(-7) != 0) { return 15 }
    if (comb_is_prime(0) != 0) { return 16 }
    if (comb_is_prime(1) != 0) { return 17 }
    if (comb_is_prime(2) != 1) { return 18 }
    if (comb_is_prime(3) != 1) { return 19 }
    if (comb_is_prime(4) != 0) { return 20 }
    if (comb_is_prime(9) != 0) { return 21 }
    if (comb_is_prime(25) != 0) { return 22 }
    if (comb_is_prime(97) != 1) { return 23 }
    if (comb_is_prime(100) != 0) { return 24 }
    if (comb_is_prime(1000003) != 1) { return 25 }
    if (comb_is_prime(1000005) != 0) { return 26 }

    // ---- comb_sieve / comb_prime_at / comb_prime_count / comb_next_prime ----
    if (comb_sieve(100) != 25) { return 27 }
    if (comb_prime_count() != 25) { return 28 }
    if (comb_prime_at(0) != 2) { return 29 }
    if (comb_prime_at(1) != 3) { return 30 }
    if (comb_prime_at(24) != 97) { return 31 }
    if (comb_prime_at(25) != -1) { return 32 }
    if (comb_prime_at(-1) != -1) { return 33 }
    if (comb_next_prime(1) != 2) { return 34 }
    if (comb_next_prime(2) != 2) { return 35 }
    if (comb_next_prime(8) != 11) { return 36 }
    if (comb_next_prime(97) != 97) { return 37 }
    if (comb_next_prime(101) != -1) { return 38 }
    if (comb_sieve(10) != 4) { return 39 }
    if (comb_prime_at(3) != 7) { return 40 }
    if (comb_sieve(511) != 97) { return 41 }
    if (comb_prime_at(96) != 509) { return 42 }
    if (comb_sieve(1000) != 97) { return 43 }
    if (comb_sieve(1) != 0) { return 44 }
    if (comb_prime_count() != 0) { return 45 }
    if (comb_prime_at(0) != -1) { return 46 }
    if (comb_sieve(2) != 1) { return 47 }
    if (comb_next_prime(2) != 2) { return 48 }

    // ---- comb_fact / comb_perm / comb_choose / comb_catalan / comb_fib ----
    if (comb_fact(-1) != 0) { return 49 }
    if (comb_fact(0) != 1) { return 50 }
    if (comb_fact(1) != 1) { return 51 }
    if (comb_fact(10) != 3628800) { return 52 }
    if (comb_fact(20) != 2432902008176640000) { return 53 }
    if (comb_fact(21) != 0) { return 54 }
    if (comb_perm(5, 0) != 1) { return 55 }
    if (comb_perm(5, 1) != 5) { return 56 }
    if (comb_perm(5, 2) != 20) { return 57 }
    if (comb_perm(6, 6) != 720) { return 58 }
    if (comb_perm(5, 6) != 0) { return 59 }
    if (comb_perm(5, -1) != 0) { return 60 }
    if (comb_perm(-5, 1) != 0) { return 61 }
    if (comb_choose(5, 0) != 1) { return 62 }
    if (comb_choose(5, 2) != 10) { return 63 }
    if (comb_choose(5, 3) != 10) { return 64 }
    if (comb_choose(5, 5) != 1) { return 65 }
    if (comb_choose(5, 6) != 0) { return 66 }
    if (comb_choose(5, -1) != 0) { return 67 }
    if (comb_choose(-1, 0) != 0) { return 68 }
    if (comb_choose(0, 1) != 0) { return 69 }
    if (comb_choose(40, 20) != 137846528820) { return 70 }
    if (comb_choose(52, 5) != 2598960) { return 71 }
    if (comb_catalan(0) != 1) { return 72 }
    if (comb_catalan(1) != 1) { return 73 }
    if (comb_catalan(5) != 42) { return 74 }
    if (comb_catalan(10) != 16796) { return 75 }
    if (comb_catalan(-1) != 0) { return 76 }
    if (comb_fib(-1) != 0) { return 77 }
    if (comb_fib(0) != 0) { return 78 }
    if (comb_fib(1) != 1) { return 79 }
    if (comb_fib(2) != 1) { return 80 }
    if (comb_fib(10) != 55) { return 81 }
    if (comb_fib(20) != 6765) { return 82 }
    if (comb_fib(50) != 12586269025) { return 83 }
    if (comb_fib(90) != 2880067194370816120) { return 84 }

    // ---- comb_digit_sum / comb_digit_count / comb_reverse_int ----
    if (comb_digit_sum(0) != 0) { return 85 }
    if (comb_digit_sum(12345) != 15) { return 86 }
    if (comb_digit_sum(-123) != 6) { return 87 }
    if (comb_digit_sum(1000) != 1) { return 88 }
    if (comb_digit_count(0) != 1) { return 89 }
    if (comb_digit_count(7) != 1) { return 90 }
    if (comb_digit_count(-1234) != 4) { return 91 }
    if (comb_digit_count(1000000000000000000) != 19) { return 92 }
    if (comb_reverse_int(0) != 0) { return 93 }
    if (comb_reverse_int(120) != 21) { return 94 }
    if (comb_reverse_int(-120) != -21) { return 95 }
    if (comb_reverse_int(7) != 7) { return 96 }

    // ---- comb_is_palindrome ----
    if (comb_is_palindrome(0) != 1) { return 97 }
    if (comb_is_palindrome(7) != 1) { return 98 }
    if (comb_is_palindrome(10) != 0) { return 99 }
    if (comb_is_palindrome(121) != 1) { return 100 }
    if (comb_is_palindrome(-121) != 1) { return 101 }
    if (comb_is_palindrome(123) != 0) { return 102 }
    if (comb_is_palindrome(1221) != 1) { return 103 }
    if (comb_is_palindrome(1000021) != 0) { return 104 }

    // ---- comb_is_armstrong ----
    if (comb_is_armstrong(0) != 1) { return 105 }
    if (comb_is_armstrong(1) != 1) { return 106 }
    if (comb_is_armstrong(9) != 1) { return 107 }
    if (comb_is_armstrong(10) != 0) { return 108 }
    if (comb_is_armstrong(153) != 1) { return 109 }
    if (comb_is_armstrong(370) != 1) { return 110 }
    if (comb_is_armstrong(371) != 1) { return 111 }
    if (comb_is_armstrong(407) != 1) { return 112 }
    if (comb_is_armstrong(152) != 0) { return 113 }
    if (comb_is_armstrong(1634) != 1) { return 114 }
    if (comb_is_armstrong(9474) != 1) { return 115 }

    // ---- comb_to_base / comb_from_base ----
    if (strcmp(comb_to_base(0, 2), "0") != 0) { return 116 }
    if (strcmp(comb_to_base(255, 16), "FF") != 0) { return 117 }
    if (strcmp(comb_to_base(255, 2), "11111111") != 0) { return 118 }
    if (strcmp(comb_to_base(8, 8), "10") != 0) { return 119 }
    if (strcmp(comb_to_base(35, 36), "Z") != 0) { return 120 }
    if (strcmp(comb_to_base(1234, 10), "1234") != 0) { return 121 }
    if (strcmp(comb_to_base(-255, 16), "-FF") != 0) { return 122 }
    if (strcmp(comb_to_base(-7, 2), "-111") != 0) { return 123 }
    if (strcmp(comb_to_base(10, 1), "") != 0) { return 124 }
    if (strcmp(comb_to_base(10, 0), "") != 0) { return 125 }
    if (strcmp(comb_to_base(10, 37), "") != 0) { return 126 }
    if (strcmp(comb_to_base(10, -2), "") != 0) { return 127 }
    if (comb_from_base("0", 10) != 0) { return 128 }
    if (comb_from_base("1234", 10) != 1234) { return 129 }
    if (comb_from_base("-42", 10) != -42) { return 130 }
    if (comb_from_base("+42", 10) != 42) { return 131 }
    if (comb_from_base("FF", 16) != 255) { return 132 }
    if (comb_from_base("ff", 16) != 255) { return 133 }
    if (comb_from_base("0xFF", 16) != 255) { return 134 }
    if (comb_from_base("-1F", 16) != -31) { return 135 }
    if (comb_from_base("11111111", 2) != 255) { return 136 }
    if (comb_from_base("Z", 36) != 35) { return 137 }
    if (comb_from_base("  42", 10) != 42) { return 138 }
    if (comb_from_base("", 10) != 0) { return 139 }
    if (comb_from_base("12", 2) != 0) { return 140 }
    if (comb_from_base("12a", 10) != 0) { return 141 }
    if (comb_from_base("G", 16) != 0) { return 142 }
    if (comb_from_base("10", 1) != 0) { return 143 }
    if (comb_from_base("10", 37) != 0) { return 144 }
    if (comb_from_base("-", 10) != 0) { return 145 }
    if (comb_from_base("0x", 16) != 0) { return 146 }

    // ---- comb_mod_pow ----
    if (comb_mod_pow(2, 10, 1000) != 24) { return 147 }
    if (comb_mod_pow(2, 0, 7) != 1) { return 148 }
    if (comb_mod_pow(5, 3, 7) != 6) { return 149 }
    if (comb_mod_pow(2, 10, 0) != 0) { return 150 }
    if (comb_mod_pow(2, 10, -7) != 0) { return 151 }
    if (comb_mod_pow(2, -1, 7) != 0) { return 152 }
    if (comb_mod_pow(-2, 3, 7) != 6) { return 153 }
    if (comb_mod_pow(3, 100, 7) != 4) { return 154 }
    if (comb_mod_pow(1, 1000000000, 97) != 1) { return 155 }

    // ---- comb_collatz_steps ----
    if (comb_collatz_steps(-5) != 0) { return 156 }
    if (comb_collatz_steps(0) != 0) { return 157 }
    if (comb_collatz_steps(1) != 0) { return 158 }
    if (comb_collatz_steps(2) != 1) { return 159 }
    if (comb_collatz_steps(6) != 8) { return 160 }
    if (comb_collatz_steps(7) != 16) { return 161 }
    if (comb_collatz_steps(27) != 111) { return 162 }

    // ---- comb_is_perfect / comb_divisor_count / comb_divisor_sum ----
    if (comb_is_perfect(-6) != 0) { return 163 }
    if (comb_is_perfect(0) != 0) { return 164 }
    if (comb_is_perfect(1) != 0) { return 165 }
    if (comb_is_perfect(2) != 0) { return 166 }
    if (comb_is_perfect(6) != 1) { return 167 }
    if (comb_is_perfect(28) != 1) { return 168 }
    if (comb_is_perfect(496) != 1) { return 169 }
    if (comb_is_perfect(8128) != 1) { return 170 }
    if (comb_is_perfect(12) != 0) { return 171 }
    if (comb_divisor_count(0) != 0) { return 172 }
    if (comb_divisor_count(-12) != 0) { return 173 }
    if (comb_divisor_count(1) != 1) { return 174 }
    if (comb_divisor_count(12) != 6) { return 175 }
    if (comb_divisor_count(16) != 5) { return 176 }
    if (comb_divisor_count(97) != 2) { return 177 }
    if (comb_divisor_sum(0) != 0) { return 178 }
    if (comb_divisor_sum(-12) != 0) { return 179 }
    if (comb_divisor_sum(1) != 1) { return 180 }
    if (comb_divisor_sum(12) != 28) { return 181 }
    if (comb_divisor_sum(16) != 31) { return 182 }
    if (comb_divisor_sum(97) != 98) { return 183 }

    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_combin_lib(workdir, use_native):
    cpu = _run(workdir, COMBIN_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


TOGETHER_SRC = '''
import "combin.cin"
import "path.cin"

function main() -> int {
    // 两库同时导入: 全局符号 (comb_primes / path_parts_buf) 与函数名不得冲突
    if (comb_sieve(100) != 25) { return 1 }
    if (comb_choose(5, 2) != 10) { return 2 }
    if (strcmp(path_normalize("a//b/./c/../d"), "a/b/d") != 0) { return 3 }
    if (strcmp(path_str_basename("/a/b/c.txt"), "c.txt") != 0) { return 4 }
    // 交错调用: path.cin 的全局分段缓冲不得破坏 combin.cin 的筛表
    if (path_split_count("/a/b/c") != 3) { return 5 }
    if (comb_prime_at(24) != 97) { return 6 }
    if (strcmp(path_str_basename("/x/y/z"), "z") != 0) { return 7 }
    if (comb_prime_count() != 25) { return 8 }
    if (comb_catalan(5) != 42) { return 9 }
    if (path_within("/x", "/x/y/z") != 1) { return 10 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_combin_and_path_importable_together(workdir, use_native):
    cpu = _run(workdir, TOGETHER_SRC, name='lib_combin_path.cin',
               use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
