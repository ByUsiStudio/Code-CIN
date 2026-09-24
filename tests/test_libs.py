"""官方标准库 (codecin/lib/*.cin) 测试。"""

import os

import pytest

from codecin import native
from tests.helpers import run_cin_file

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")


def _run(workdir, source, name='lib_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


def test_array_lib(workdir):
    src = '''
import "array.cin"
function main() -> int {
    int a[6] = {4, 8, 1, 8, 3, 6}
    if (a_sum(a, 6) != 30) { return 1 }
    if (a_max(a, 6) != 8) { return 2 }
    if (a_min(a, 6) != 1) { return 3 }
    if (a_find(a, 6, 3) != 4) { return 4 }
    if (a_count(a, 6, 8) != 2) { return 5 }
    if (a_index_of_min(a, 6) != 2) { return 6 }
    if (a_contains(a, 6, 99) != 0) { return 7 }
    a_reverse(a, 6)
    if (a[0] != 6 || a[5] != 4) { return 8 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_sort_lib(workdir):
    src = '''
import "sort.cin"
function main() -> int {
    int a[7] = {9, 2, 7, 1, 8, 3, 5}
    sort_quick_all(a, 7)
    if (sort_is_sorted(a, 7) != 1) { return 1 }
    if (a[0] != 1 || a[6] != 9) { return 2 }
    if (bin_search(a, 7, 7) != 4) { return 3 }
    if (bin_search(a, 7, 100) != -1) { return 4 }
    int b[5] = {5, 4, 3, 2, 1}
    sort_bubble(b, 5)
    if (sort_is_sorted(b, 5) != 1) { return 5 }
    int c[5] = {5, 4, 3, 2, 1}
    sort_selection(c, 5)
    if (sort_is_sorted(c, 5) != 1) { return 6 }
    int d[5] = {5, 4, 3, 2, 1}
    sort_insertion(d, 5)
    if (sort_is_sorted(d, 5) != 1) { return 7 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_conv_lib(workdir):
    src = '''
import "conv.cin"
function main() -> int {
    if (strcmp(c_to_hex(255), "FF") != 0) { return 1 }
    if (strcmp(c_to_hex(0), "0") != 0) { return 2 }
    if (c_parse_hex("0x1F") != 31) { return 3 }
    if (strcmp(c_to_bin(10), "1010") != 0) { return 4 }
    if (c_parse_bin("0b1010") != 10) { return 5 }
    if (strcmp(c_pad_int(7, 3), "007") != 0) { return 6 }
    if (strcmp(c_pad_left("ab", 4, "-"), "--ab") != 0) { return 7 }
    if (strcmp(c_pad_right("ab", 4, "."), "ab..") != 0) { return 8 }
    if (strcmp(c_repeat("xy", 3), "xyxyxy") != 0) { return 9 }
    if (c_parse_float("-2.5") != -2.5) { return 10 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_vec_lib(workdir):
    src = '''
import "vec.cin"
function main() -> int {
    float v[5] = {2.0, 4.0, 4.0, 4.0, 6.0}
    if (v_sum(v, 5) != 20.0) { return 1 }
    if (v_mean(v, 5) != 4.0) { return 2 }
    if (v_max(v, 5) != 6.0) { return 3 }
    if (v_min(v, 5) != 2.0) { return 4 }
    if (v_dot(v, v, 5) != 88.0) { return 5 }
    if (v_norm(v, 5) != sqrt(88.0)) { return 6 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_rand_lib_in_range(workdir):
    src = '''
import "rand.cin"
function main() -> int {
    srand(12345)
    for (int i = 0; i < 50; i = i + 1) {
        int v = r_range(10, 20)
        if (v < 10 || v > 20) { return 1 }
        float f = r_float()
        if (f < 0.0 || f >= 1.0) { return 2 }
    }
    int a[4] = {1, 2, 3, 4}
    r_shuffle(a, 4)
    int s = 0
    for (int i = 0; i < 4; i = i + 1) { s = s + a[i] }
    if (s != 10) { return 3 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_json_lib(workdir):
    src = '''
import "json.cin"
function main() -> int {
    string j = "{\\"s\\":\\"hi\\",\\"n\\":-7,\\"b\\":false,\\"f\\":1.25}"
    if (strcmp(j_str(j, "s"), "hi") != 0) { return 1 }
    if (j_int(j, "n") != -7) { return 2 }
    if (j_bool(j, "b") != 0) { return 3 }
    if (j_float(j, "f") != 1.25) { return 4 }
    if (j_has(j, "s") != 1) { return 5 }
    if (j_has(j, "zzz") != 0) { return 6 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_time_lib(workdir):
    src = '''
import "time.cin"
function main() -> int {
    if (strcmp(t_hms(3661), "01:01:01") != 0) { return 1 }
    if (strcmp(t_ms(125), "02:05") != 0) { return 2 }
    if (strcmp(t_human(90061), "1d 1h 1m 1s") != 0) { return 3 }
    if (strcmp(t_human(5), "5s") != 0) { return 4 }
    if (t_now() <= 0) { return 5 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0


def test_test_lib(workdir):
    src = '''
import "test.cin"
function main() -> int {
    t_reset()
    t_eq_int(1 + 1, 2, "add")
    t_eq_str("a", "a", "str")
    t_near(1.0, 1.0001, 0.01, "near")
    t_true(1, "true")
    t_false(0, "false")
    return t_report()
}'''
    # 全部通过时 t_report 返回 0
    assert _run(workdir, src).regs.read(0) == 0


@needs_native
def test_io_lib(workdir):
    p = os.path.join(workdir, 'io_lib.txt').replace('\\', '/')
    sub = os.path.join(workdir, 'io_sub').replace('\\', '/')
    src = f'''
import "io.cin"
function main() -> int {{
    if (io_write("{p}", "a\\nb\\nc") != 0) {{ return 1 }}
    if (io_exists("{p}") != 1) {{ return 2 }}
    if (io_size("{p}") != 5) {{ return 3 }}
    if (io_line_count(io_read("{p}")) != 3) {{ return 4 }}
    if (strcmp(io_get_line(io_read("{p}"), 1), "b") != 0) {{ return 5 }}
    if (mkdir("{sub}") != 0) {{ return 6 }}
    if (strcmp(io_basename("{p}"), "io_lib.txt") != 0) {{ return 7 }}
    if (strcmp(io_join("x", "y"), "x/y") != 0) {{ return 8 }}
    if (io_remove("{p}") != 0) {{ return 9 }}
    return 0
}}'''
    assert _run(workdir, src, use_native=True).regs.read(0) == 0


@needs_native
def test_gui_lib(workdir):
    p = os.path.join(workdir, 'gui_lib.png').replace('\\', '/')
    src = f'''
import "gui.cin"
function main() -> int {{
    int a[5] = {{3, 7, 2, 9, 5}}
    g_bar_chart(a, 5, 50, 30)
    if (g_save("{p}") != 0) {{ return 1 }}
    if (g_rgb(255, 0, 0) != 0xFF0000) {{ return 2 }}
    g_line_chart(a, 5, 40, 20)
    if (g_save("{p}") != 0) {{ return 3 }}
    return 0
}}'''
    assert _run(workdir, src, use_native=True).regs.read(0) == 0
    assert os.path.getsize(p) > 0


@needs_native
def test_termux_lib_present(workdir):
    src = '''
import "termux.cin"
function main() -> int {
    int ok = tx_ok()
    if (ok == 0) { return 10 }
    if (ok == 1) { return 20 }
    return 30
}'''
    # 非 Termux 环境返回 10; Termux 环境返回 20; 均视为正常
    assert _run(workdir, src, use_native=True).regs.read(0) in (10, 20)
