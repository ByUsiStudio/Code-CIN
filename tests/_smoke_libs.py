"""烟测: 三兼容层新增函数 (解释/原生路径, 传 --native 走原生); 退出码 0 = 全过 (非 0 为失败项编号)。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import run_cin_file

SRC = '''\
import "cstd.cin"
import "cppstd.cin"
import "gostd.cin"
function main() -> int {
    // cstd
    if (libc_iscntrl(0) != 1) { return 1 }
    if (libc_isgraph(' ') != 0) { return 2 }
    if (libc_isblank(9) != 1) { return 3 }
    if (libc_ispunct('#') != 1) { return 4 }
    if (libc_ispunct('a') != 0) { return 5 }
    if (libc_toascii('z') != 'z') { return 6 }
    if (strcmp(libc_strlwr("AbC"), "abc") != 0) { return 7 }
    if (strcmp(libc_strupr("AbC"), "ABC") != 0) { return 8 }
    if (libc_strspn("aabbcc", "ab") != 4) { return 9 }
    if (libc_strcspn("aabbcc", "c") != 4) { return 10 }
    if (libc_strpbrk("xyz12", "9871") != 3) { return 11 }
    if (libc_labs(-42) != 42) { return 12 }
    if (libc_round(-2.5) != -3.0) { return 13 }
    if (libc_round(2.5) != 3.0) { return 14 }
    if (libc_trunc(-2.7) != -2.0) { return 15 }
    if (libc_putchar('!') != 33) { return 16 }
    if (libc_fabs(libc_sin(0.0)) > 0.000001) { return 17 }
    // cppstd
    if (stl_str_starts_with("hello", "he") != 1) { return 20 }
    if (stl_str_ends_with("hello", "lo") != 1) { return 21 }
    if (stl_str_find_first_of("hello", "ol") != 2) { return 22 }
    if (stl_str_find_last_of("hello", "l") != 3) { return 23 }
    if (stl_str_at("abc", 1) != 'b') { return 24 }
    if (strcmp(stl_str_insert("helo", 2, "l"), "hello") != 0) { return 25 }
    if (strcmp(stl_str_erase("hello", 1, 2), "hlo") != 0) { return 26 }
    if (strcmp(stl_str_replace("hello", 1, 2, "ey"), "heylo") != 0) { return 27 }
    // cppstd vector
    int v[8]
    int n = 0
    n = stl_vec_push_back(v, n, 1)
    n = stl_vec_push_back(v, n, 3)
    n = stl_vec_insert(v, n, 1, 2)
    if (n != 3) { return 28 }
    if (v[1] != 2) { return 29 }
    if (stl_vec_find(v, n, 3) != 2) { return 30 }
    if (stl_vec_count(v, n, 9) != 0) { return 31 }
    n = stl_vec_erase(v, n, 0)
    if (n != 2 || v[0] != 2) { return 32 }
    stl_sort_desc(v, n)
    if (v[0] != 3) { return 33 }
    if (stl_clamp(7, 1, 5) != 5) { return 34 }
    // gostd
    if (go_strings_last_index("aXbX", "X") != 3) { return 40 }
    if (go_strings_index_any("abc", "cb") != 1) { return 41 }
    if (strcmp(go_strings_trim_left("aab!", "a"), "b!") != 0) { return 42 }
    if (strcmp(go_strings_trim_right("abb!", "b!"), "a") != 0) { return 43 }
    if (strcmp(go_strings_trim_prefix("prefix:x", "prefix:"), "x") != 0) { return 44 }
    if (strcmp(go_strings_trim_suffix("x:suffix", ":suffix"), "x") != 0) { return 45 }
    if (strcmp(go_format_int(255, 16), "ff") != 0) { return 46 }
    if (strcmp(go_format_int(-8, 2), "-1000") != 0) { return 47 }
    if (go_parse_int("-ff", 16) != -255) { return 48 }
    if (go_parse_int("101", 2) != 5) { return 49 }
    if (go_math_round(-2.5) != -3.0) { return 50 }
    if (go_math_trunc(2.9) != 2.0) { return 51 }
    int a[3] = {1, 2, 3}
    int b[3] = {0, 0, 0}
    if (go_slices_clone(b, a, 3) != 3) { return 52 }
    if (go_slices_equal(a, b, 3) != 1) { return 53 }
    if (go_slices_last_index(a, 3, 3) != 2) { return 54 }
    go_slices_sort(b, 3)
    if (b[0] != 1) { return 55 }
    return 0
}
'''

def main():
    workdir = tempfile.mkdtemp(prefix='smoke_libs_')
    path = os.path.join(workdir, 'prog.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(SRC)
    use_native = '--native' in sys.argv[1:]
    cpu = run_cin_file(path, use_native=use_native)
    code = cpu.regs.read(0)
    print(f"exit={code}")
    return 0 if (code == 0 and not cpu.execution_failed) else 1

if __name__ == '__main__':
    sys.exit(main())
