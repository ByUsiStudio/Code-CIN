"""官方标准库 csv.cin (单行 CSV 解析与生成) 测试。

覆盖: 每个公开函数至少一个用例 + 边界 (空行、只有分隔符、尾随分隔符、
引号字段、字段内含逗号/引号、越界下标、超长行缓冲上限、自定义分隔符)。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_csv_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


CSV_SRC = r'''
import "csv.cin"

// 浮点比较辅助 (避免依赖 float_to_str 的具体格式)
function close_enough(float a, float b) -> int {
    float d = a - b
    if (d < 0.0) { d = -d }
    if (d < 0.000001) { return 1 }
    return 0
}

function main() -> int {
    // ---- csv_buf_capacity ----
    if (csv_buf_capacity() != 1024) { return 1 }

    // ---- csv_count: 空行 / 只有分隔符 / 尾随分隔符 ----
    if (csv_count("") != 0) { return 2 }
    if (csv_count("a") != 1) { return 3 }
    if (csv_count("a,b,c") != 3) { return 4 }
    if (csv_count(",") != 2) { return 5 }
    if (csv_count("a,") != 2) { return 6 }
    if (csv_count(",a") != 2) { return 7 }
    if (csv_count("a,,b") != 3) { return 8 }
    if (csv_count(",,,") != 4) { return 9 }
    if (csv_count(" ") != 1) { return 10 }
    if (csv_count("\"a,b\",c") != 2) { return 11 }
    if (csv_count("\"\"") != 1) { return 12 }
    if (csv_count("\"a\"\"b\",c") != 2) { return 13 }

    // ---- csv_get: 取值 / 越界 / 引号 / 未闭合引号 ----
    if (strcmp(csv_get("a,b,c", 0), "a") != 0) { return 14 }
    if (strcmp(csv_get("a,b,c", 2), "c") != 0) { return 15 }
    if (strcmp(csv_get("a,,b", 1), "") != 0) { return 16 }
    if (strcmp(csv_get("a,", 1), "") != 0) { return 17 }
    if (strcmp(csv_get("a,b", 5), "") != 0) { return 18 }
    if (strcmp(csv_get("a,b", -1), "") != 0) { return 19 }
    if (strcmp(csv_get("", 0), "") != 0) { return 20 }
    if (strcmp(csv_get("\"a,b\",c", 0), "a,b") != 0) { return 21 }
    if (strcmp(csv_get("\"a,b\",c", 1), "c") != 0) { return 22 }
    if (strcmp(csv_get("\"a\"\"b\",c", 0), "a\"b") != 0) { return 23 }
    if (strcmp(csv_get(" x , y ", 0), " x ") != 0) { return 24 }
    if (strcmp(csv_get(" x , y ", 1), " y ") != 0) { return 25 }
    if (strcmp(csv_get(" \"a\" , b", 0), "a") != 0) { return 26 }
    if (strcmp(csv_get("\"\"", 0), "") != 0) { return 27 }
    if (strcmp(csv_get("\"a\"x,b", 0), "a") != 0) { return 28 }
    if (strcmp(csv_get("\"abc", 0), "abc") != 0) { return 29 }
    if (strcmp(csv_get("\"\"\"\"", 0), "\"") != 0) { return 30 }

    // ---- csv_has: 存在性 (空字段也算存在) ----
    if (csv_has("a,b", 0) != 1) { return 31 }
    if (csv_has("a,b", 1) != 1) { return 32 }
    if (csv_has("a,b", 2) != 0) { return 33 }
    if (csv_has("a,b", -1) != 0) { return 34 }
    if (csv_has("a,", 1) != 1) { return 35 }
    if (csv_has("a,,b", 1) != 1) { return 36 }
    if (csv_has("", 0) != 0) { return 37 }

    // ---- csv_get_int ----
    if (csv_get_int("1,42,x", 1, -1) != 42) { return 38 }
    if (csv_get_int("1,42,x", 2, -1) != -1) { return 39 }
    if (csv_get_int("1,42,x", 9, -1) != -1) { return 40 }
    if (csv_get_int("-7", 0, 0) != -7) { return 41 }
    if (csv_get_int(" 8 ", 0, 0) != 8) { return 42 }
    if (csv_get_int("+9", 0, 0) != 9) { return 43 }
    if (csv_get_int("3.5", 0, -1) != -1) { return 44 }
    if (csv_get_int("", 0, -1) != -1) { return 45 }
    if (csv_get_int("007", 0, -1) != 7) { return 46 }
    if (csv_get_int("a,,b", 1, 5) != 5) { return 47 }
    if (csv_get_int("+", 0, -1) != -1) { return 48 }
    if (csv_get_int("-", 0, -1) != -1) { return 49 }
    if (csv_get_int("\"42\"", 0, -1) != 42) { return 50 }

    // ---- csv_get_float (含 fallback 与非法格式) ----
    if (close_enough(csv_get_float("1,1.5", 1, -1.0), 1.5) == 0) { return 51 }
    if (close_enough(csv_get_float("-2.25", 0, 0.0), -2.25) == 0) { return 52 }
    if (close_enough(csv_get_float(".5", 0, -1.0), 0.5) == 0) { return 53 }
    if (close_enough(csv_get_float("5.", 0, -1.0), 5.0) == 0) { return 54 }
    if (close_enough(csv_get_float("+0.75", 0, 0.0), 0.75) == 0) { return 55 }
    if (close_enough(csv_get_float("1e3", 0, -1.0), -1.0) == 0) { return 56 }
    if (close_enough(csv_get_float("1.2.3", 0, -1.0), -1.0) == 0) { return 57 }
    if (close_enough(csv_get_float("x", 0, -9.5), -9.5) == 0) { return 58 }
    if (close_enough(csv_get_float("", 0, 3.5), 3.5) == 0) { return 59 }
    if (close_enough(csv_get_float("1,2.5", 5, 7.0), 7.0) == 0) { return 60 }
    if (close_enough(csv_get_float("1,2.5", -1, 7.0), 7.0) == 0) { return 61 }
    if (close_enough(csv_get_float("1,\"2.5\"", 1, -1.0), 2.5) == 0) { return 62 }

    // ---- csv_trim_cells ----
    if (strcmp(csv_trim_cells(" a , b "), "a,b") != 0) { return 63 }
    if (strcmp(csv_trim_cells("  "), "") != 0) { return 64 }
    if (strcmp(csv_trim_cells(""), "") != 0) { return 65 }
    if (strcmp(csv_trim_cells("a,,b"), "a,,b") != 0) { return 66 }
    if (strcmp(csv_trim_cells("a ,"), "a,") != 0) { return 67 }
    if (strcmp(csv_trim_cells("\"a, b\" , c"), "\"a, b\",c") != 0) { return 68 }
    if (strcmp(csv_trim_cells(" \" x \" "), "\" x \"") != 0) { return 69 }
    if (strcmp(csv_trim_cells("\tx\t,y"), "x,y") != 0) { return 70 }
    if (strcmp(csv_get(csv_trim_cells(" A , B "), 0), "A") != 0) { return 71 }
    if (strcmp(csv_get(csv_trim_cells(" A , B "), 1), "B") != 0) { return 72 }
    if (csv_count(csv_trim_cells(" A , B ")) != 2) { return 73 }

    // ---- csv_escape: 只转义不加引号 ----
    if (strcmp(csv_escape("ab"), "ab") != 0) { return 74 }
    if (strcmp(csv_escape(""), "") != 0) { return 75 }
    if (strcmp(csv_escape("a\"b"), "a\"\"b") != 0) { return 76 }
    if (strcmp(csv_escape("\"\""), "\"\"\"\"") != 0) { return 77 }
    if (strcmp(csv_escape("a\"b\"c"), "a\"\"b\"\"c") != 0) { return 78 }

    // ---- csv_quote: 按需加引号 ----
    if (strcmp(csv_quote(""), "") != 0) { return 79 }
    if (strcmp(csv_quote("ab"), "ab") != 0) { return 80 }
    if (strcmp(csv_quote("a,b"), "\"a,b\"") != 0) { return 81 }
    if (strcmp(csv_quote("a\"b"), "\"a\"\"b\"") != 0) { return 82 }
    if (strcmp(csv_quote(" a"), "\" a\"") != 0) { return 83 }
    if (strcmp(csv_quote("a "), "\"a \"") != 0) { return 84 }
    if (strcmp(csv_quote(" "), "\" \"") != 0) { return 85 }
    if (strcmp(csv_quote("a\nb"), "\"a\nb\"") != 0) { return 86 }
    if (strcmp(csv_quote("a\tb"), "a\tb") != 0) { return 87 }

    // ---- csv_line_start / csv_line_add / csv_line_get / csv_line_count ----
    csv_line_start()
    if (csv_line_count() != 0) { return 88 }
    if (strcmp(csv_line_get(), "") != 0) { return 89 }
    if (csv_line_add("") != 1) { return 90 }
    if (csv_line_count() != 1) { return 91 }
    if (strlen(csv_line_get()) != 0) { return 92 }
    if (csv_count(csv_line_get()) != 0) { return 93 }

    csv_line_start()
    if (csv_line_add("a") != 1) { return 94 }
    if (csv_line_add("b,c") != 1) { return 95 }
    if (csv_line_add("c\"d") != 1) { return 96 }
    if (csv_line_add(" e ") != 1) { return 97 }
    if (csv_line_add("") != 1) { return 98 }
    if (csv_line_count() != 5) { return 99 }
    if (strcmp(csv_line_get(), "a,\"b,c\",\"c\"\"d\",\" e \",") != 0) { return 100 }
    if (csv_count(csv_line_get()) != 5) { return 101 }
    if (strcmp(csv_get(csv_line_get(), 0), "a") != 0) { return 102 }
    if (strcmp(csv_get(csv_line_get(), 1), "b,c") != 0) { return 103 }
    if (strcmp(csv_get(csv_line_get(), 2), "c\"d") != 0) { return 104 }
    if (strcmp(csv_get(csv_line_get(), 3), " e ") != 0) { return 105 }
    if (strcmp(csv_get(csv_line_get(), 4), "") != 0) { return 106 }
    if (csv_has(csv_line_get(), 4) != 1) { return 107 }
    if (csv_has(csv_line_get(), 5) != 0) { return 108 }

    // ---- 超长行: 缓冲满时返回 0 且不越界写 ----
    string chunk = "0123456789"
    for (int i = 0; i < 5; i = i + 1) {
        chunk = chunk + chunk
    }
    if (strlen(chunk) != 320) { return 109 }
    csv_line_start()
    if (csv_line_add(chunk) != 1) { return 110 }
    if (csv_line_add(chunk) != 1) { return 111 }
    if (csv_line_add(chunk) != 1) { return 112 }
    if (strlen(csv_line_get()) != 962) { return 113 }
    if (csv_line_add(chunk) != 0) { return 114 }
    if (strlen(csv_line_get()) != 962) { return 115 }
    if (csv_line_count() != 3) { return 116 }
    if (csv_line_add(substr(chunk, 0, 61)) != 1) { return 117 }
    if (strlen(csv_line_get()) != 1024) { return 118 }
    if (csv_line_add("z") != 0) { return 119 }
    if (strlen(csv_line_get()) != 1024) { return 120 }
    if (csv_line_count() != 4) { return 121 }
    if (csv_count(csv_line_get()) != 4) { return 122 }
    if (csv_has(csv_line_get(), 3) != 1) { return 123 }
    if (csv_has(csv_line_get(), 4) != 0) { return 124 }

    // ---- csv_raw_count / csv_raw_get: 自定义分隔符, 不做引号处理 ----
    if (csv_raw_count("a;b;c", 59) != 3) { return 125 }
    if (csv_raw_count("a;b;c", 44) != 1) { return 126 }
    if (csv_raw_count("", 59) != 0) { return 127 }
    if (csv_raw_count(";", 59) != 2) { return 128 }
    if (csv_raw_count("a;b", 0) != 1) { return 129 }
    if (csv_raw_count("a;b", -3) != 1) { return 130 }
    if (csv_raw_count("a\tb", 9) != 2) { return 131 }
    if (csv_raw_count("\"a;b\"", 59) != 2) { return 132 }
    if (strcmp(csv_raw_get("a;b;c", 59, 0), "a") != 0) { return 133 }
    if (strcmp(csv_raw_get("a;b;c", 59, 1), "b") != 0) { return 134 }
    if (strcmp(csv_raw_get("a;b;c", 59, 2), "c") != 0) { return 135 }
    if (strcmp(csv_raw_get("a;b;c", 59, 3), "") != 0) { return 136 }
    if (strcmp(csv_raw_get("a;b;c", 59, -1), "") != 0) { return 137 }
    if (strcmp(csv_raw_get("a,b", 44, 1), "b") != 0) { return 138 }
    if (strcmp(csv_raw_get("x;", 59, 1), "") != 0) { return 139 }
    if (strcmp(csv_raw_get("a;b", 0, 0), "a;b") != 0) { return 140 }
    if (strcmp(csv_raw_get("a;b", 0, 1), "") != 0) { return 141 }
    if (strcmp(csv_raw_get("", 59, 0), "") != 0) { return 142 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_csv_lib(workdir, use_native):
    cpu = _run(workdir, CSV_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


TOGETHER_SRC = r'''
import "csv.cin"
import "fmt.cin"

function main() -> int {
    if (csv_count("a,b,c") != 3) { return 1 }
    if (strcmp(csv_get("a,\"b,c\"", 1), "b,c") != 0) { return 2 }
    csv_line_start()
    if (csv_line_add(fmt_int_zero(7, 3)) != 1) { return 3 }
    if (csv_line_add(fmt_float_dec(2.5, 1)) != 1) { return 4 }
    if (strcmp(csv_line_get(), "007,2.5") != 0) { return 5 }
    if (csv_get_int(csv_line_get(), 0, -1) != 7) { return 6 }
    if (strcmp(fmt_thousands(csv_get_int("x,1234567", 1, 0)), "1,234,567") != 0) { return 7 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_csv_fmt_importable_together(workdir, use_native):
    """两库同时导入不得冲突 (全局符号 / 函数名)。"""
    cpu = _run(workdir, TOGETHER_SRC, name='lib_csv_fmt.cin', use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
