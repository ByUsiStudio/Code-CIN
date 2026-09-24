"""新增官方标准库 (codecin/lib: bits|stat|hash|validate|matrix|queue.cin) 测试。

每个用例返回 0 表示全部通过, 非 0 为具体失败点编号, 便于定位。
"""

import os

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_ext_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


def test_bits_lib(workdir):
    src = '''
import "bits.cin"
function main() -> int {
    if (bits_popcount(0xFF) != 8) { return 1 }
    if (bits_popcount(0) != 0) { return 2 }
    if (bits_popcount(-1) != 64) { return 3 }
    if (bits_clz(1) != 63) { return 4 }
    if (bits_clz(0) != 64) { return 5 }
    if (bits_clz(-1) != 0) { return 6 }
    if (bits_ctz(8) != 3) { return 7 }
    if (bits_ctz(0) != 64) { return 8 }
    if (bits_is_pow2(64) != 1) { return 9 }
    if (bits_is_pow2(63) != 0) { return 10 }
    if (bits_is_pow2(0) != 0) { return 11 }
    if (bits_next_pow2(5) != 8) { return 12 }
    if (bits_next_pow2(1) != 1) { return 13 }
    if (bits_test(4, 2) != 1) { return 14 }
    if (bits_test(4, 1) != 0) { return 15 }
    if (bits_set(0, 3) != 8) { return 16 }
    if (bits_clear(15, 0) != 14) { return 17 }
    if (bits_toggle(0, 1) != 2) { return 18 }
    if (bits_rotl(1, 1) != 2) { return 19 }
    if (bits_rotr(2, 1) != 1) { return 20 }
    if (bits_rotl(1, 64) != 1) { return 21 }
    if (bits_rotr(bits_rotl(0x1234567890ABCDEF, 17), 17) != 0x1234567890ABCDEF) { return 22 }
    if (bits_reverse(bits_reverse(0x1234)) != 0x1234) { return 23 }
    if (bits_range_mask(4, 7) != 0xF0) { return 24 }
    if (bits_range_mask(7, 4) != 0) { return 25 }
    if (bits_extract(0xABCD, 4, 7) != 0xC) { return 26 }
    if (bits_insert(0, 4, 7, 0xC) != 0xC0) { return 27 }
    if (bits_bswap(0x0102) != 0x0201000000000000) { return 28 }
    if (bits_mask() != 0xFFFFFFFFFFFFFFFF) { return 29 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_stat_lib(workdir):
    src = '''
import "stat.cin"
function main() -> int {
    int a[6] = {4, 8, 1, 8, 3, 6}
    if (stat_sum(a, 6) != 30) { return 1 }
    if (stat_min(a, 6) != 1) { return 2 }
    if (stat_max(a, 6) != 8) { return 3 }
    if (stat_range(a, 6) != 7) { return 4 }
    if (stat_mean(a, 6) != 5) { return 5 }
    if (stat_count(a, 6, 8) != 2) { return 6 }
    if (stat_mode(a, 6) != 8) { return 7 }
    int s[6] = {1, 3, 4, 6, 8, 8}
    if (stat_is_sorted(s, 6) != 1) { return 8 }
    if (stat_is_sorted(a, 6) != 0) { return 9 }
    if (stat_median_sorted(s, 6) != 5) { return 10 }
    int odd[5] = {1, 2, 3, 4, 5}
    if (stat_median_sorted(odd, 5) != 3) { return 11 }
    if (stat_percentile_sorted(s, 6, 50) != 4) { return 12 }
    if (stat_q1_sorted(s, 6) != 3) { return 13 }
    if (stat_q3_sorted(s, 6) != 8) { return 14 }
    if (stat_variance_x1000(a, 6) != 6666) { return 15 }
    if (stat_stdev_x100(a, 6) != 666) { return 16 }
    int h[10]
    stat_histogram(a, 6, h, 10)
    if (h[1] != 1 || h[3] != 1 || h[4] != 1 || h[6] != 1 || h[8] != 2) { return 17 }
    if (h[0] != 0 || h[9] != 0) { return 18 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_hash_lib(workdir):
    src = '''
import "hash.cin"
function main() -> int {
    if (hash_djb2("") != 5381) { return 1 }
    if (hash_fnv1a("") != 0xCBF29CE484222325) { return 2 }
    if (hash_djb2("abc") == hash_djb2("abd")) { return 3 }
    if (hash_fnv1a("abc") == hash_fnv1a("abd")) { return 4 }
    if (hash_sdbm("abc") == hash_sdbm("abd")) { return 5 }
    if (hash_djb2("abc") != hash_djb2("abc")) { return 6 }
    int b = hash_string_bucket("hello", 16)
    if (b < 0 || b > 15) { return 7 }
    if (hash_bucket(-3, 8) != 5) { return 8 }
    if (hash_bucket(100, 0) != 0) { return 9 }
    if (hash_int(0) != 0) { return 10 }
    if (hash_combine(1, 2) == hash_combine(2, 1)) { return 11 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_validate_lib(workdir):
    src = '''
import "validate.cin"
function main() -> int {
    if (val_is_digit('7') != 1) { return 1 }
    if (val_is_digit('a') != 0) { return 2 }
    if (val_is_alpha('A') != 1 || val_is_alpha('z') != 1) { return 3 }
    if (val_is_alpha('1') != 0) { return 4 }
    if (val_is_alnum('9') != 1 || val_is_alnum('_') != 0) { return 5 }
    if (val_is_hex('f') != 1 || val_is_hex('G') != 0) { return 6 }
    if (val_is_space(' ') != 1 || val_is_space('x') != 0) { return 7 }
    if (val_is_upper('A') != 1 || val_is_lower('A') != 0) { return 8 }
    if (val_is_int("42") != 1) { return 9 }
    if (val_is_int("-42") != 1) { return 10 }
    if (val_is_int("+42") != 1) { return 11 }
    if (val_is_int("") != 0) { return 12 }
    if (val_is_int("+") != 0) { return 13 }
    if (val_is_int("4a") != 0) { return 14 }
    if (val_is_float("3.14") != 1) { return 15 }
    if (val_is_float("-0.5") != 1) { return 16 }
    if (val_is_float("1.2.3") != 0) { return 17 }
    if (val_is_float(".") != 0) { return 18 }
    if (val_is_ident("_x1") != 1) { return 19 }
    if (val_is_ident("1x") != 0) { return 20 }
    if (val_is_ident("x-y") != 0) { return 21 }
    if (val_count_char("banana", 'a') != 3) { return 22 }
    if (val_is_blank("  \\t ") != 1) { return 23 }
    if (val_is_blank(" x ") != 0) { return 24 }
    if (val_clamp_int(15, 0, 10) != 10) { return 25 }
    if (val_clamp_int(-5, 0, 10) != 0) { return 26 }
    if (val_clamp_int(5, 0, 10) != 5) { return 27 }
    if (val_parse_int("42", -1) != 42) { return 28 }
    if (val_parse_int("x", -1) != -1) { return 29 }
    if (val_is_hex_color("#A1B2C3") != 1) { return 30 }
    if (val_is_hex_color("#ABC") != 1) { return 31 }
    if (val_is_hex_color("A1B2C3") != 1) { return 32 }
    if (val_is_hex_color("#AB") != 0) { return 33 }
    if (val_is_hex_color("#XYZ") != 0) { return 34 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_matrix_lib(workdir):
    src = '''
import "matrix.cin"
function main() -> int {
    int n = 2
    int a[4] = {1, 2, 3, 4}
    int b[4] = {5, 6, 7, 8}
    int out[4]
    int id[4]
    mat_identity(id, 2)
    if (id[0] != 1 || id[1] != 0 || id[2] != 0 || id[3] != 1) { return 1 }

    mat_add(a, b, out, n)
    if (out[0] != 6 || out[3] != 12) { return 2 }
    mat_sub(b, a, out, n)
    if (out[0] != 4 || out[3] != 4) { return 3 }
    mat_mul(a, b, out, n)
    if (out[0] != 19 || out[1] != 22 || out[2] != 43 || out[3] != 50) { return 4 }
    mat_transpose(a, out, n)
    if (out[0] != 1 || out[1] != 3 || out[2] != 2 || out[3] != 4) { return 5 }
    mat_scale(a, 3, out, n)
    if (out[0] != 3 || out[3] != 12) { return 6 }

    if (mat_trace(a, n) != 5) { return 7 }
    if (mat_sum(a, n) != 10) { return 8 }
    if (mat_det(a, n) != -2) { return 9 }
    if (mat_equals(a, a, n) != 1) { return 10 }
    if (mat_equals(a, b, n) != 0) { return 11 }

    int sym[4] = {1, 2, 2, 3}
    if (mat_is_symmetric(sym, n) != 1) { return 12 }
    if (mat_is_symmetric(a, n) != 0) { return 13 }

    int m3[9] = {2, 0, 0, 0, 3, 0, 0, 0, 4}
    if (mat_det(m3, 3) != 24) { return 14 }
    int t3[9] = {1, 2, 3, 4, 5, 6, 7, 8, 10}
    if (mat_det(t3, 3) != -3) { return 15 }

    mat_get(a, n, 1, 0)
    if (mat_get(a, n, 1, 0) != 3) { return 16 }
    if (mat_get(a, n, 5, 5) != 0) { return 17 }
    mat_set(a, n, 0, 0, 9)
    if (mat_get(a, n, 0, 0) != 9) { return 18 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_queue_lib(workdir):
    src = '''
import "queue.cin"
function main() -> int {
    queue_clear()
    if (queue_is_empty() != 1) { return 1 }
    if (queue_push(1) != 1) { return 2 }
    if (queue_push(2) != 1) { return 3 }
    if (queue_push(3) != 1) { return 4 }
    if (queue_size() != 3) { return 5 }
    if (queue_front() != 1) { return 6 }
    if (queue_back() != 3) { return 7 }
    if (queue_pop() != 1) { return 8 }
    if (queue_pop() != 2) { return 9 }
    if (queue_pop() != 3) { return 10 }
    if (queue_is_empty() != 1) { return 11 }
    if (queue_pop() != 0) { return 12 }

    // 环绕: 填满 -> 出队 10 -> 再入队 10
    for (int i = 0; i < 64; i = i + 1) {
        if (queue_push(i) != 1) { return 13 }
    }
    if (queue_is_full() != 1) { return 14 }
    if (queue_push(999) != 0) { return 15 }
    for (int i = 0; i < 10; i = i + 1) {
        if (queue_pop() != i) { return 16 }
    }
    for (int i = 100; i < 110; i = i + 1) {
        if (queue_push(i) != 1) { return 17 }
    }
    if (queue_size() != 64) { return 18 }
    if (queue_front() != 10) { return 19 }
    if (queue_back() != 109) { return 20 }

    stack_clear()
    if (stack_is_empty() != 1) { return 21 }
    if (stack_size() != 0) { return 22 }
    if (stack_peek() != 0) { return 23 }
    if (stack_push(7) != 1) { return 24 }
    if (stack_push(8) != 1) { return 25 }
    if (stack_size() != 2) { return 26 }
    if (stack_peek() != 8) { return 27 }
    if (stack_pop() != 8) { return 28 }
    if (stack_pop() != 7) { return 29 }
    if (stack_pop() != 0) { return 30 }
    if (queue_capacity() != 64 || stack_capacity() != 64) { return 31 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_new_libs_are_importable_together(workdir):
    """多个新库同时导入不得冲突 (全局符号/名称)。"""
    src = '''
import "bits.cin"
import "stat.cin"
import "hash.cin"
import "validate.cin"
import "matrix.cin"
import "queue.cin"
function main() -> int {
    if (bits_popcount(3) != 2) { return 1 }
    int a[3] = {1, 2, 3}
    if (stat_sum(a, 3) != 6) { return 2 }
    if (hash_fnv1a("") != 0xCBF29CE484222325) { return 3 }
    if (val_is_digit('1') != 1) { return 4 }
    int m[4] = {1, 0, 0, 1}
    if (mat_trace(m, 2) != 2) { return 5 }
    queue_clear()
    if (queue_push(5) != 1) { return 6 }
    if (queue_pop() != 5) { return 7 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0
