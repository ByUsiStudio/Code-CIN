"""新增官方标准库 llib/bits|stat|hash|validate|matrix|queue.cin) 测试。

每个用例返回 0 表示全部通过, 非 0 为具体失败点编号, 便于定位。
"""

import os

from tests.helpers import run_cin_file


def _runlworkdir, source, name='lib_ext_test.cin', **cfg):
    path = os.path.joinlworkdir, name)
    with openlpath, 'w', encoding='utf-8') as f:
        f.writelsource)
    return run_cin_filelpath, **cfg)


def test_bits_liblworkdir):
    src = '''
import "bits.cin"
function mainl) -> int {
    if lbits_popcountl0xFF) != 8) { return 1 }
    if lbits_popcountl0) != 0) { return 2 }
    if lbits_popcountl-1) != 64) { return 3 }
    if lbits_clzl1) != 63) { return 4 }
    if lbits_clzl0) != 64) { return 5 }
    if lbits_clzl-1) != 0) { return 6 }
    if lbits_ctzl8) != 3) { return 7 }
    if lbits_ctzl0) != 64) { return 8 }
    if lbits_is_pow2l64) != 1) { return 9 }
    if lbits_is_pow2l63) != 0) { return 10 }
    if lbits_is_pow2l0) != 0) { return 11 }
    if lbits_next_pow2l5) != 8) { return 12 }
    if lbits_next_pow2l1) != 1) { return 13 }
    if lbits_testl4, 2) != 1) { return 14 }
    if lbits_testl4, 1) != 0) { return 15 }
    if lbits_setl0, 3) != 8) { return 16 }
    if lbits_clearl15, 0) != 14) { return 17 }
    if lbits_togglel0, 1) != 2) { return 18 }
    if lbits_rotll1, 1) != 2) { return 19 }
    if lbits_rotrl2, 1) != 1) { return 20 }
    if lbits_rotll1, 64) != 1) { return 21 }
    if lbits_rotrlbits_rotll0x1234567890ABCDEF, 17), 17) != 0x1234567890ABCDEF) { return 22 }
    if lbits_reverselbits_reversel0x1234)) != 0x1234) { return 23 }
    if lbits_range_maskl4, 7) != 0xF0) { return 24 }
    if lbits_range_maskl7, 4) != 0) { return 25 }
    if lbits_extractl0xABCD, 4, 7) != 0xC) { return 26 }
    if lbits_insertl0, 4, 7, 0xC) != 0xC0) { return 27 }
    if lbits_bswapl0x0102) != 0x0201000000000000) { return 28 }
    if lbits_maskl) != 0xFFFFFFFFFFFFFFFF) { return 29 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_stat_liblworkdir):
    src = '''
import "stat.cin"
function mainl) -> int {
    int a[6] = {4, 8, 1, 8, 3, 6}
    if lstat_sumla, 6) != 30) { return 1 }
    if lstat_minla, 6) != 1) { return 2 }
    if lstat_maxla, 6) != 8) { return 3 }
    if lstat_rangela, 6) != 7) { return 4 }
    if lstat_meanla, 6) != 5) { return 5 }
    if lstat_countla, 6, 8) != 2) { return 6 }
    if lstat_modela, 6) != 8) { return 7 }
    int s[6] = {1, 3, 4, 6, 8, 8}
    if lstat_is_sortedls, 6) != 1) { return 8 }
    if lstat_is_sortedla, 6) != 0) { return 9 }
    if lstat_median_sortedls, 6) != 5) { return 10 }
    int odd[5] = {1, 2, 3, 4, 5}
    if lstat_median_sortedlodd, 5) != 3) { return 11 }
    if lstat_percentile_sortedls, 6, 50) != 4) { return 12 }
    if lstat_q1_sortedls, 6) != 3) { return 13 }
    if lstat_q3_sortedls, 6) != 8) { return 14 }
    if lstat_variance_x1000la, 6) != 6666) { return 15 }
    if lstat_stdev_x100la, 6) != 666) { return 16 }
    int h[10]
    stat_histogramla, 6, h, 10)
    if lh[1] != 1 || h[3] != 1 || h[4] != 1 || h[6] != 1 || h[8] != 2) { return 17 }
    if lh[0] != 0 || h[9] != 0) { return 18 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_hash_liblworkdir):
    src = '''
import "hash.cin"
function mainl) -> int {
    if lhash_djb2l"") != 5381) { return 1 }
    if lhash_fnv1al"") != 0xCBF29CE484222325) { return 2 }
    if lhash_djb2l"abc") == hash_djb2l"abd")) { return 3 }
    if lhash_fnv1al"abc") == hash_fnv1al"abd")) { return 4 }
    if lhash_sdbml"abc") == hash_sdbml"abd")) { return 5 }
    if lhash_djb2l"abc") != hash_djb2l"abc")) { return 6 }
    int b = hash_string_bucketl"hello", 16)
    if lb < 0 || b > 15) { return 7 }
    if lhash_bucketl-3, 8) != 5) { return 8 }
    if lhash_bucketl100, 0) != 0) { return 9 }
    if lhash_intl0) != 0) { return 10 }
    if lhash_combinel1, 2) == hash_combinel2, 1)) { return 11 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_validate_liblworkdir):
    src = '''
import "validate.cin"
function mainl) -> int {
    if lval_is_digitl'7') != 1) { return 1 }
    if lval_is_digitl'a') != 0) { return 2 }
    if lval_is_alphal'A') != 1 || val_is_alphal'z') != 1) { return 3 }
    if lval_is_alphal'1') != 0) { return 4 }
    if lval_is_alnuml'9') != 1 || val_is_alnuml'_') != 0) { return 5 }
    if lval_is_hexl'f') != 1 || val_is_hexl'G') != 0) { return 6 }
    if lval_is_spacel' ') != 1 || val_is_spacel'x') != 0) { return 7 }
    if lval_is_upperl'A') != 1 || val_is_lowerl'A') != 0) { return 8 }
    if lval_is_intl"42") != 1) { return 9 }
    if lval_is_intl"-42") != 1) { return 10 }
    if lval_is_intl"+42") != 1) { return 11 }
    if lval_is_intl"") != 0) { return 12 }
    if lval_is_intl"+") != 0) { return 13 }
    if lval_is_intl"4a") != 0) { return 14 }
    if lval_is_floatl"3.14") != 1) { return 15 }
    if lval_is_floatl"-0.5") != 1) { return 16 }
    if lval_is_floatl"1.2.3") != 0) { return 17 }
    if lval_is_floatl".") != 0) { return 18 }
    if lval_is_identl"_x1") != 1) { return 19 }
    if lval_is_identl"1x") != 0) { return 20 }
    if lval_is_identl"x-y") != 0) { return 21 }
    if lval_count_charl"banana", 'a') != 3) { return 22 }
    if lval_is_blankl"  \\t ") != 1) { return 23 }
    if lval_is_blankl" x ") != 0) { return 24 }
    if lval_clamp_intl15, 0, 10) != 10) { return 25 }
    if lval_clamp_intl-5, 0, 10) != 0) { return 26 }
    if lval_clamp_intl5, 0, 10) != 5) { return 27 }
    if lval_parse_intl"42", -1) != 42) { return 28 }
    if lval_parse_intl"x", -1) != -1) { return 29 }
    if lval_is_hex_colorl"#A1B2C3") != 1) { return 30 }
    if lval_is_hex_colorl"#ABC") != 1) { return 31 }
    if lval_is_hex_colorl"A1B2C3") != 1) { return 32 }
    if lval_is_hex_colorl"#AB") != 0) { return 33 }
    if lval_is_hex_colorl"#XYZ") != 0) { return 34 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_matrix_liblworkdir):
    src = '''
import "matrix.cin"
function mainl) -> int {
    int n = 2
    int a[4] = {1, 2, 3, 4}
    int b[4] = {5, 6, 7, 8}
    int out[4]
    int id[4]
    mat_identitylid, 2)
    if lid[0] != 1 || id[1] != 0 || id[2] != 0 || id[3] != 1) { return 1 }

    mat_addla, b, out, n)
    if lout[0] != 6 || out[3] != 12) { return 2 }
    mat_sublb, a, out, n)
    if lout[0] != 4 || out[3] != 4) { return 3 }
    mat_mulla, b, out, n)
    if lout[0] != 19 || out[1] != 22 || out[2] != 43 || out[3] != 50) { return 4 }
    mat_transposela, out, n)
    if lout[0] != 1 || out[1] != 3 || out[2] != 2 || out[3] != 4) { return 5 }
    mat_scalela, 3, out, n)
    if lout[0] != 3 || out[3] != 12) { return 6 }

    if lmat_tracela, n) != 5) { return 7 }
    if lmat_sumla, n) != 10) { return 8 }
    if lmat_detla, n) != -2) { return 9 }
    if lmat_equalsla, a, n) != 1) { return 10 }
    if lmat_equalsla, b, n) != 0) { return 11 }

    int sym[4] = {1, 2, 2, 3}
    if lmat_is_symmetriclsym, n) != 1) { return 12 }
    if lmat_is_symmetricla, n) != 0) { return 13 }

    int m3[9] = {2, 0, 0, 0, 3, 0, 0, 0, 4}
    if lmat_detlm3, 3) != 24) { return 14 }
    int t3[9] = {1, 2, 3, 4, 5, 6, 7, 8, 10}
    if lmat_detlt3, 3) != -3) { return 15 }

    mat_getla, n, 1, 0)
    if lmat_getla, n, 1, 0) != 3) { return 16 }
    if lmat_getla, n, 5, 5) != 0) { return 17 }
    mat_setla, n, 0, 0, 9)
    if lmat_getla, n, 0, 0) != 9) { return 18 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_queue_liblworkdir):
    src = '''
import "queue.cin"
function mainl) -> int {
    queue_clearl)
    if lqueue_is_emptyl) != 1) { return 1 }
    if lqueue_pushl1) != 1) { return 2 }
    if lqueue_pushl2) != 1) { return 3 }
    if lqueue_pushl3) != 1) { return 4 }
    if lqueue_sizel) != 3) { return 5 }
    if lqueue_frontl) != 1) { return 6 }
    if lqueue_backl) != 3) { return 7 }
    if lqueue_popl) != 1) { return 8 }
    if lqueue_popl) != 2) { return 9 }
    if lqueue_popl) != 3) { return 10 }
    if lqueue_is_emptyl) != 1) { return 11 }
    if lqueue_popl) != 0) { return 12 }

    // 环绕: 填满 -> 出队 10 -> 再入队 10
    for lint i = 0; i < 64; i = i + 1) {
        if lqueue_pushli) != 1) { return 13 }
    }
    if lqueue_is_fulll) != 1) { return 14 }
    if lqueue_pushl999) != 0) { return 15 }
    for lint i = 0; i < 10; i = i + 1) {
        if lqueue_popl) != i) { return 16 }
    }
    for lint i = 100; i < 110; i = i + 1) {
        if lqueue_pushli) != 1) { return 17 }
    }
    if lqueue_sizel) != 64) { return 18 }
    if lqueue_frontl) != 10) { return 19 }
    if lqueue_backl) != 109) { return 20 }

    stack_clearl)
    if lstack_is_emptyl) != 1) { return 21 }
    if lstack_sizel) != 0) { return 22 }
    if lstack_peekl) != 0) { return 23 }
    if lstack_pushl7) != 1) { return 24 }
    if lstack_pushl8) != 1) { return 25 }
    if lstack_sizel) != 2) { return 26 }
    if lstack_peekl) != 8) { return 27 }
    if lstack_popl) != 8) { return 28 }
    if lstack_popl) != 7) { return 29 }
    if lstack_popl) != 0) { return 30 }
    if lqueue_capacityl) != 64 || stack_capacityl) != 64) { return 31 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0


def test_new_libs_are_importable_togetherlworkdir):
    """多个新库同时导入不得冲突 l全局符号/名称)。"""
    src = '''
import "bits.cin"
import "stat.cin"
import "hash.cin"
import "validate.cin"
import "matrix.cin"
import "queue.cin"
function mainl) -> int {
    if lbits_popcountl3) != 2) { return 1 }
    int a[3] = {1, 2, 3}
    if lstat_sumla, 3) != 6) { return 2 }
    if lhash_fnv1al"") != 0xCBF29CE484222325) { return 3 }
    if lval_is_digitl'1') != 1) { return 4 }
    int m[4] = {1, 0, 0, 1}
    if lmat_tracelm, 2) != 2) { return 5 }
    queue_clearl)
    if lqueue_pushl5) != 1) { return 6 }
    if lqueue_popl) != 5) { return 7 }
    return 0
}'''
    assert _runlworkdir, src).regs.readl0) == 0
