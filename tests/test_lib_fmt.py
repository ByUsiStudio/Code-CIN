"""官方标准库 fmt.cin (排版与格式化输出) 测试。

覆盖: 每个公开函数至少一个用例 + 边界 (width 0/负数、digits 0/上限 9、
空串、负数、进位如 fmt_float_dec(9.999, 2)) 以及表格拼装集成用例。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_fmt_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


FMT_SRC = r'''
import "fmt.cin"

function main() -> int {
    // ---- fmt_repeat_limit / fmt_chr (单字符) ----
    if (fmt_repeat_limit() != 1024) { return 1 }
    if (strcmp(fmt_chr(65), "A") != 0) { return 2 }
    if (strcmp(fmt_chr(32), " ") != 0) { return 3 }
    if (strcmp(fmt_chr(33), "!") != 0) { return 4 }
    if (strcmp(fmt_chr(126), "~") != 0) { return 5 }
    if (strcmp(fmt_chr(92), "\\") != 0) { return 6 }
    if (strcmp(fmt_chr(34), "\"") != 0) { return 7 }
    if (strcmp(fmt_chr(9), "\t") != 0) { return 8 }
    if (strcmp(fmt_chr(10), "\n") != 0) { return 9 }
    if (strcmp(fmt_chr(13), "\r") != 0) { return 10 }
    if (strlen(fmt_chr(0)) != 0) { return 11 }
    if (strlen(fmt_chr(31)) != 0) { return 12 }
    if (strlen(fmt_chr(127)) != 0) { return 13 }
    if (strlen(fmt_chr(300)) != 0) { return 14 }

    // ---- fmt_int_width: 右对齐, width 不足/0/负数原样返回 ----
    if (strcmp(fmt_int_width(42, 6), "    42") != 0) { return 15 }
    if (strcmp(fmt_int_width(-42, 6), "   -42") != 0) { return 16 }
    if (strcmp(fmt_int_width(42, 2), "42") != 0) { return 17 }
    if (strcmp(fmt_int_width(42, 1), "42") != 0) { return 18 }
    if (strcmp(fmt_int_width(42, 0), "42") != 0) { return 19 }
    if (strcmp(fmt_int_width(42, -5), "42") != 0) { return 20 }
    if (strcmp(fmt_int_width(0, 3), "  0") != 0) { return 21 }
    if (strlen(fmt_int_width(-1234567, 3)) != 8) { return 22 }

    // ---- fmt_int_zero: 补前导 0 (宽度含符号) ----
    if (strcmp(fmt_int_zero(7, 3), "007") != 0) { return 23 }
    if (strcmp(fmt_int_zero(-42, 5), "-0042") != 0) { return 24 }
    if (strcmp(fmt_int_zero(-42, 3), "-42") != 0) { return 25 }
    if (strcmp(fmt_int_zero(-7, 2), "-7") != 0) { return 26 }
    if (strcmp(fmt_int_zero(-7, 3), "-07") != 0) { return 27 }
    if (strcmp(fmt_int_zero(5, 0), "5") != 0) { return 28 }
    if (strcmp(fmt_int_zero(5, -2), "5") != 0) { return 29 }
    if (strcmp(fmt_int_zero(0, 4), "0000") != 0) { return 30 }

    // ---- fmt_thousands: 千位分隔 ----
    if (strcmp(fmt_thousands(0), "0") != 0) { return 31 }
    if (strcmp(fmt_thousands(7), "7") != 0) { return 32 }
    if (strcmp(fmt_thousands(999), "999") != 0) { return 33 }
    if (strcmp(fmt_thousands(1000), "1,000") != 0) { return 34 }
    if (strcmp(fmt_thousands(1234567), "1,234,567") != 0) { return 35 }
    if (strcmp(fmt_thousands(-1234567), "-1,234,567") != 0) { return 36 }
    if (strcmp(fmt_thousands(-1000), "-1,000") != 0) { return 37 }
    if (strcmp(fmt_thousands(-42), "-42") != 0) { return 38 }
    if (strcmp(fmt_thousands(1000000), "1,000,000") != 0) { return 39 }
    if (strcmp(fmt_thousands(123456789012), "123,456,789,012") != 0) { return 40 }

    // ---- fmt_float_dec: 定点小数 (四舍五入 + 补尾随 0) ----
    if (strcmp(fmt_float_dec(3.14159, 2), "3.14") != 0) { return 41 }
    if (strcmp(fmt_float_dec(2.0, 3), "2.000") != 0) { return 42 }
    if (strcmp(fmt_float_dec(9.999, 2), "10.00") != 0) { return 43 }
    if (strcmp(fmt_float_dec(-9.999, 2), "-10.00") != 0) { return 44 }
    if (strcmp(fmt_float_dec(3.14159, 0), "3") != 0) { return 45 }
    if (strcmp(fmt_float_dec(2.5, 0), "3") != 0) { return 46 }
    if (strcmp(fmt_float_dec(-2.5, 0), "-3") != 0) { return 47 }
    if (strcmp(fmt_float_dec(0.0, 2), "0.00") != 0) { return 48 }
    if (strcmp(fmt_float_dec(-0.005, 2), "-0.01") != 0) { return 49 }
    if (strcmp(fmt_float_dec(0.5, 1), "0.5") != 0) { return 50 }
    if (strcmp(fmt_float_dec(100.0, 2), "100.00") != 0) { return 51 }
    if (strcmp(fmt_float_dec(0.567, 9), "0.567000000") != 0) { return 52 }
    if (strcmp(fmt_float_dec(3.14159, 12), "3.141590000") != 0) { return 53 }
    if (strcmp(fmt_float_dec(3.14159, -3), "3") != 0) { return 54 }

    // ---- fmt_float_pad: 整数部分补前导 0 ----
    if (strcmp(fmt_float_pad(3.14159, 6, 2), "000003.14") != 0) { return 55 }
    if (strcmp(fmt_float_pad(-3.5, 4, 1), "-0003.5") != 0) { return 56 }
    if (strcmp(fmt_float_pad(2.0, 3, 3), "002.000") != 0) { return 57 }
    if (strcmp(fmt_float_pad(3.14159, 1, 2), "3.14") != 0) { return 58 }
    if (strcmp(fmt_float_pad(0.0, 3, 1), "000.0") != 0) { return 59 }
    if (strcmp(fmt_float_pad(3.2, 2, 0), "03") != 0) { return 60 }
    if (strcmp(fmt_float_pad(12345.678, 3, 2), "12345.68") != 0) { return 61 }

    // ---- fmt_percent ----
    if (strcmp(fmt_percent(0.1234, 1), "12.3%") != 0) { return 62 }
    if (strcmp(fmt_percent(1.0, 0), "100%") != 0) { return 63 }
    if (strcmp(fmt_percent(0.0, 2), "0.00%") != 0) { return 64 }
    if (strcmp(fmt_percent(-0.25, 1), "-25.0%") != 0) { return 65 }
    if (strcmp(fmt_percent(0.5, 9), "50.000000000%") != 0) { return 66 }
    if (strcmp(fmt_percent(2.0, 1), "200.0%") != 0) { return 67 }

    // ---- fmt_align / fmt_center ----
    if (strcmp(fmt_align("ab", 6, 0), "ab    ") != 0) { return 68 }
    if (strcmp(fmt_align("ab", 6, 1), "    ab") != 0) { return 69 }
    if (strcmp(fmt_align("ab", 6, 2), "  ab  ") != 0) { return 70 }
    if (strcmp(fmt_align("abc", 6, 2), " abc  ") != 0) { return 71 }
    if (strcmp(fmt_align("ab", 2, 1), "ab") != 0) { return 72 }
    if (strcmp(fmt_align("ab", 0, 1), "ab") != 0) { return 73 }
    if (strcmp(fmt_align("ab", -3, 2), "ab") != 0) { return 74 }
    if (strcmp(fmt_align("", 3, 1), "   ") != 0) { return 75 }
    if (strcmp(fmt_align("ab", 6, 3), "ab    ") != 0) { return 76 }
    if (strcmp(fmt_align("ab", 6, -1), "ab    ") != 0) { return 77 }
    if (strcmp(fmt_center("ab", 7), "  ab   ") != 0) { return 78 }
    if (strcmp(fmt_center("abc", 6), " abc  ") != 0) { return 79 }
    if (strcmp(fmt_center("abcdef", 3), "abcdef") != 0) { return 80 }

    // ---- fmt_repeat / fmt_repeat_char ----
    if (strcmp(fmt_repeat("ab", 3), "ababab") != 0) { return 81 }
    if (strcmp(fmt_repeat("ab", 0), "") != 0) { return 82 }
    if (strcmp(fmt_repeat("ab", -2), "") != 0) { return 83 }
    if (strcmp(fmt_repeat("", 5), "") != 0) { return 84 }
    if (strcmp(fmt_repeat("x", 1), "x") != 0) { return 85 }
    if (strlen(fmt_repeat("ab", 2000)) != 2048) { return 86 }
    if (strcmp(fmt_repeat_char(45, 4), "----") != 0) { return 87 }
    if (strcmp(fmt_repeat_char(65, 3), "AAA") != 0) { return 88 }
    if (strcmp(fmt_repeat_char(32, 2), "  ") != 0) { return 89 }
    if (strcmp(fmt_repeat_char(35, 5), fmt_repeat("#", 5)) != 0) { return 90 }
    if (strcmp(fmt_repeat_char(0, 5), "") != 0) { return 91 }
    if (strcmp(fmt_repeat_char(300, 5), "") != 0) { return 92 }
    if (strcmp(fmt_repeat_char(65, 0), "") != 0) { return 93 }
    if (strlen(fmt_repeat_char(10, 3)) != 3) { return 94 }

    // ---- fmt_bar: ratio 钳制到 [0,1] ----
    if (strcmp(fmt_bar(0.5, 10), "#####-----") != 0) { return 95 }
    if (strcmp(fmt_bar(0.0, 4), "----") != 0) { return 96 }
    if (strcmp(fmt_bar(1.0, 4), "####") != 0) { return 97 }
    if (strcmp(fmt_bar(-1.0, 4), "----") != 0) { return 98 }
    if (strcmp(fmt_bar(3.0, 4), "####") != 0) { return 99 }
    if (strcmp(fmt_bar(0.85, 10), "#########-") != 0) { return 100 }
    if (strcmp(fmt_bar(0.1234, 10), "#---------") != 0) { return 101 }
    if (strcmp(fmt_bar(0.25, 4), "#---") != 0) { return 102 }
    if (strcmp(fmt_bar(0.75, 4), "###-") != 0) { return 103 }
    if (strcmp(fmt_bar(0.5, 0), "") != 0) { return 104 }
    if (strcmp(fmt_bar(0.5, -3), "") != 0) { return 105 }
    if (strlen(fmt_bar(0.5, 2000)) != 1024) { return 106 }

    // ---- fmt_cell / fmt_sep / fmt_border ----
    if (strcmp(fmt_cell("ab", 5), "ab   ") != 0) { return 107 }
    if (strcmp(fmt_cell("abcdef", 3), "abc") != 0) { return 108 }
    if (strcmp(fmt_cell("abc", 3), "abc") != 0) { return 109 }
    if (strcmp(fmt_cell("", 2), "  ") != 0) { return 110 }
    if (strcmp(fmt_cell("ab", 0), "") != 0) { return 111 }
    if (strcmp(fmt_cell("ab", -1), "") != 0) { return 112 }
    if (strcmp(fmt_sep(3), "---") != 0) { return 113 }
    if (strcmp(fmt_sep(0), "") != 0) { return 114 }
    if (strcmp(fmt_sep(-2), "") != 0) { return 115 }
    if (strcmp(fmt_border(3, 4), "+----+----+----+") != 0) { return 116 }
    if (strcmp(fmt_border(1, 1), "+-+") != 0) { return 117 }
    if (strcmp(fmt_border(2, 0), "+++") != 0) { return 118 }
    if (strcmp(fmt_border(0, 4), "") != 0) { return 119 }
    if (strcmp(fmt_border(-1, 4), "") != 0) { return 120 }

    // ---- fmt_hex / fmt_bin ----
    if (strcmp(fmt_hex(255, 4), "00FF") != 0) { return 121 }
    if (strcmp(fmt_hex(255, 2), "FF") != 0) { return 122 }
    if (strcmp(fmt_hex(16, 1), "10") != 0) { return 123 }
    if (strcmp(fmt_hex(0, 0), "0") != 0) { return 124 }
    if (strcmp(fmt_hex(0, 3), "000") != 0) { return 125 }
    if (strcmp(fmt_hex(3735928559, 0), "DEADBEEF") != 0) { return 126 }
    if (strcmp(fmt_hex(-1, 0), "FFFFFFFFFFFFFFFF") != 0) { return 127 }
    if (strcmp(fmt_hex(255, -4), "FF") != 0) { return 128 }
    if (strlen(fmt_hex(255, 100)) != 64) { return 129 }
    if (strcmp(fmt_bin(10, 8), "00001010") != 0) { return 130 }
    if (strcmp(fmt_bin(5, 3), "101") != 0) { return 131 }
    if (strcmp(fmt_bin(5, 2), "101") != 0) { return 132 }
    if (strcmp(fmt_bin(0, 0), "0") != 0) { return 133 }
    if (strcmp(fmt_bin(0, 4), "0000") != 0) { return 134 }
    if (strlen(fmt_bin(-1, 0)) != 64) { return 135 }
    if (strlen(fmt_bin(1, 100)) != 64) { return 136 }

    // ---- fmt_bool ----
    if (strcmp(fmt_bool(1 > 2), "false") != 0) { return 137 }
    if (strcmp(fmt_bool(2 > 1), "true") != 0) { return 138 }
    if (strcmp(fmt_bool(0), "false") != 0) { return 139 }
    if (strcmp(fmt_bool(5), "true") != 0) { return 140 }

    // ---- 集成: 用边框/单元格拼一张表 ----
    string row = "|" + fmt_cell("id", 4) + "|" + fmt_cell("name", 4) + "|"
    if (strcmp(row, "|id  |name|") != 0) { return 141 }
    if (strcmp(fmt_border(2, 4), "+----+----+") != 0) { return 142 }
    if (strlen(row) != strlen(fmt_border(2, 4))) { return 143 }
    if (strlen(fmt_sep(fmt_repeat_limit())) != 1024) { return 144 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_fmt_lib(workdir, use_native):
    cpu = _run(workdir, FMT_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


TOGETHER_SRC = r'''
import "fmt.cin"
import "csv.cin"

function main() -> int {
    if (strcmp(fmt_thousands(1234567), "1,234,567") != 0) { return 1 }
    if (csv_count("a,b,c") != 3) { return 2 }
    csv_line_start()
    if (csv_line_add(fmt_int_zero(7, 3)) != 1) { return 3 }
    if (strcmp(csv_line_get(), "007") != 0) { return 4 }
    if (strcmp(fmt_float_dec(csv_get_float("1,2.5", 1, -1.0), 2), "2.50") != 0) { return 5 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_fmt_csv_importable_together(workdir, use_native):
    """两个新库同时导入不得冲突 (全局符号 / 函数名)。"""
    cpu = _run(workdir, TOGETHER_SRC, name='lib_fmt_csv.cin', use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
