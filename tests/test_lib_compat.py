r"""C/C++/Go 兼容层标准库 (cstd / cppstd / gostd) 与新宿主内建测试。

覆盖点:
  1. cstd.cin (libc_*): <ctype.h> / <string.h> / <stdlib.h> / <math.h> 语义
  2. cppstd.cin (stl_*): std::string / vector / stack / queue / utility
  3. gostd.cin (go_*): strings / strconv / math / slices
  4. arg_count / arg (SYS 129..130): 命令行参数管道 (CLI `--` 传参 ->
     codecin_set_args -> 引擎 ProgramArgs)
  5. input_str (SYS 131): 预读缓冲行读 (与解释器 input() 的管道语义一致)
  6. 新内建无原生库时可编译解析

import "x.cin" 由文件加载器展开 (load_program_source_mapped), 因此库测试
走临时文件 + run_cin_file; 内建测试走内存编译 (build_cpu 模式, 与
tests/test_p0_fixes.py 的约定一致)。

失败时 main 返回非 0 错误码 (第 N 项检查失败 -> 返回编码 N), 便于定位。
"""

import os

import pytest

from codecin import CPU, Config, native
from codecin.cin import CINCompiler
from tests.helpers import run_cin_file

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")

PATHS = (False, True)
PATH_IDS = ('interp', 'native')


def build_cpu(src: str, use_native: bool, **cfg_kwargs) -> CPU:
    res = CINCompiler().compile_source(src)
    cfg = Config(interactive_mode=False, log_level='ERROR',
                 use_native=use_native, **cfg_kwargs)
    cpu = CPU(cfg)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    return cpu


def _write_workdir(workdir, name, src):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    return path


def _run_ok(cpu):
    assert not cpu.execution_failed, '程序执行失败 (ExecutionError 被 CPU.run 吞掉)'
    assert cpu.regs.read(0) == 0, (
        f"检查失败, 错误码 {cpu.regs.read(0)} (见测试源码注释)")


# ====================================================================
# 1. cstd.cin — C 兼容层
# ====================================================================

CSTD_SRC = '''\
import "cstd.cin"
function main() -> int {
    // ---- ctype (字符码: 0-9=48..57, A-Z=65..90, a-z=97..122) ----
    if (libc_isdigit('7') != 1) { return 1 }
    if (libc_isdigit('x') != 0) { return 2 }
    if (libc_isalpha('Q') != 1) { return 3 }
    if (libc_isalpha('3') != 0) { return 4 }
    if (libc_isalnum('_') != 0) { return 5 }
    if (libc_isspace(32) != 1) { return 6 }
    if (libc_isspace(9) != 1) { return 7 }
    if (libc_isspace('a') != 0) { return 8 }
    if (libc_isprint(126) != 1) { return 9 }
    if (libc_isprint(127) != 0) { return 10 }
    if (libc_isxdigit('f') != 1) { return 11 }
    if (libc_isxdigit('g') != 0) { return 12 }
    if (libc_toupper('a') != 'A') { return 13 }
    if (libc_toupper('3') != '3') { return 14 }
    if (libc_tolower('Q') != 'q') { return 15 }
    // ---- string ----
    if (libc_strlen("hello") != 5) { return 20 }
    if (libc_strcmp("abc", "abd") >= 0) { return 21 }
    if (libc_strcmp("abd", "abc") <= 0) { return 22 }
    if (libc_strcmp("same", "same") != 0) { return 23 }
    if (libc_strstr("hello", "ll") != 2) { return 24 }
    if (libc_strstr("hello", "zz") != -1) { return 25 }
    if (libc_strchr("hello", "l") != 2) { return 26 }
    if (libc_strrchr("hello", "l") != 3) { return 27 }
    if (libc_strrchr("hello", "z") != -1) { return 28 }
    if (libc_strncmp("hello", "help", 3) != 0) { return 29 }
    // "hell" < "help" ('l' < 'p') -> 负值
    if (libc_strncmp("hello", "help", 4) >= 0) { return 30 }
    if (libc_strcmp(libc_strrev("abc"), "cba") != 0) { return 31 }
    if (libc_strcmp(libc_strcat("foo", "bar"), "foobar") != 0) { return 32 }
    if (libc_strcmp(libc_strdup("dup"), "dup") != 0) { return 33 }
    // ---- stdlib ----
    if (libc_abs(-7) != 7) { return 40 }
    if (libc_abs(7) != 7) { return 41 }
    if (libc_atoi("42x") != 42) { return 42 }
    if (libc_max(3, 9) != 9) { return 43 }
    if (libc_min(3, 9) != 3) { return 44 }
    int a[4] = {3, 1, 4, 1}
    libc_qsort_asc(a, 4)
    if (a[0] != 1 || a[1] != 1 || a[2] != 3 || a[3] != 4) { return 45 }
    // ---- math (精确二进制值, 可直接 ==) ----
    if (libc_fabs(-2.5) != 2.5) { return 50 }
    if (libc_fmod(7.5, 2.0) != 1.5) { return 51 }
    if (libc_fmod(-7.5, 2.0) != -1.5) { return 52 }
    if (libc_floor(2.7) != 2.0) { return 53 }
    if (libc_ceil(2.1) != 3.0) { return 54 }
    if (libc_sqrt(4.0) != 2.0) { return 55 }
    if (libc_pow(2.0, 3.0) != 8.0) { return 56 }
    // ---- 内存操作 (int 槽) ----
    int b[3]
    libc_memset(b, 9, 3)
    if (b[2] != 9) { return 60 }
    libc_memcpy(b, a, 2)
    if (b[0] != 1 || b[1] != 1 || b[2] != 9) { return 61 }
    if (libc_memcmp(a, a, 4) != 0) { return 62 }
    return 0
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_cstd_libc(workdir, use_native):
    path = _write_workdir(workdir, 't_cstd.cin', CSTD_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    _run_ok(cpu)


# ====================================================================
# 2. cppstd.cin — C++ STL 兼容层
# ====================================================================

CPPSTD_SRC = '''\
import "cppstd.cin"
function main() -> int {
    // ---- std::string ----
    string s = stl_str_append("foo", "bar")
    if (stl_str_size(s) != 6) { return 1 }
    if (stl_str_length(s) != 6) { return 2 }
    if (stl_str_empty(s) != 0) { return 3 }
    if (stl_str_find(s, "ob") != 2) { return 4 }
    if (stl_str_find(s, "zz") != -1) { return 5 }
    // "foobar" 的 'o' 在下标 1 与 2, 末现是 2
    if (stl_str_rfind(s, "o") != 2) { return 6 }
    if (strcmp(stl_str_substr(s, 1, 2), "oo") != 0) { return 7 }
    if (stl_str_compare("a", "b") >= 0) { return 8 }
    if (strcmp(stl_str_c_str(s), "foobar") != 0) { return 9 }
    if (strcmp(stl_to_string(42), "42") != 0) { return 10 }
    // ---- vector<int>: 数组 + 长度游标, push/pop 返回新长度 ----
    int v[8]
    int n = 0
    n = stl_vec_push_back(v, n, 5)
    n = stl_vec_push_back(v, n, 9)
    n = stl_vec_push_back(v, n, 2)
    if (n != 3) { return 20 }
    if (stl_vec_back(v, n) != 2) { return 21 }
    if (stl_vec_front(v, n) != 5) { return 22 }
    if (stl_vec_at(v, n, 1) != 9) { return 23 }
    if (stl_vec_at(v, n, 99) != 0) { return 24 }
    if (stl_vec_sum(v, n) != 16) { return 25 }
    if (stl_vec_max(v, n) != 9) { return 26 }
    if (stl_vec_min(v, n) != 2) { return 27 }
    if (stl_vec_empty(n) != 0) { return 28 }
    stl_vec_fill(v, n, 7)
    if (stl_vec_at(v, n, 2) != 7) { return 29 }
    stl_vec_reverse(v, n)
    if (stl_vec_at(v, n, 0) != 7) { return 30 }
    n = stl_vec_pop_back(n)
    if (stl_vec_size(n) != 2) { return 31 }
    if (stl_vec_empty(n) != 0) { return 32 }
    if (stl_vec_empty(0) != 1) { return 33 }
    // ---- stack<int>: top 游标语义 ----
    int st[4]
    int top = 0
    top = stl_stack_push(st, top, 1)
    top = stl_stack_push(st, top, 2)
    if (stl_stack_top(st, top) != 2) { return 40 }
    top = stl_stack_pop(top)
    if (stl_stack_size(top) != 1) { return 41 }
    if (stl_stack_top(st, top) != 1) { return 42 }
    if (stl_stack_empty(top) != 0) { return 43 }
    if (stl_stack_empty(0) != 1) { return 44 }
    // ---- queue<int>: 非循环 head/tail ----
    int q[4]
    int head = 0
    int tail = 0
    tail = stl_queue_push(q, tail, 7)
    tail = stl_queue_push(q, tail, 8)
    if (stl_queue_front(q, head) != 7) { return 50 }
    if (stl_queue_back(q, tail) != 8) { return 51 }
    if (stl_queue_size(head, tail) != 2) { return 52 }
    head = stl_queue_pop(head)
    if (stl_queue_front(q, head) != 8) { return 53 }
    if (stl_queue_empty(head, tail) != 0) { return 54 }
    if (stl_queue_empty(tail, tail) != 1) { return 55 }
    // ---- utility / algorithm ----
    if (stl_max(3, 9) != 9) { return 60 }
    if (stl_min(3, 9) != 3) { return 61 }
    if (stl_abs(-4) != 4) { return 62 }
    stl_swap(v, 0, 1)
    if (stl_vec_at(v, n, 0) + stl_vec_at(v, n, 1) != 7) { return 63 }
    int w[4] = {5, 2, 8, 1}
    stl_sort(w, 4)
    if (w[0] != 1 || w[3] != 8) { return 64 }
    return 0
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_cppstd_stl(workdir, use_native):
    path = _write_workdir(workdir, 't_cppstd.cin', CPPSTD_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    _run_ok(cpu)


# ====================================================================
# 3. gostd.cin — Go 标准库兼容层
# ====================================================================

GOSTD_SRC = '''\
import "gostd.cin"
function main() -> int {
    // ---- strings ----
    if (go_strings_contains("hello world", "wor") != 1) { return 1 }
    if (go_strings_contains("hello", "zz") != 0) { return 2 }
    if (go_strings_index("hello", "lo") != 3) { return 3 }
    if (go_strings_index("hello", "zz") != -1) { return 4 }
    if (go_strings_contains_any("hello", "xz") != 0) { return 5 }
    if (go_strings_contains_any("hello", "xl") != 1) { return 6 }
    if (go_strings_has_prefix("hello", "he") != 1) { return 7 }
    if (go_strings_has_suffix("hello", "lo") != 1) { return 8 }
    if (go_strings_has_prefix("he", "hello") != 0) { return 9 }
    if (strcmp(go_strings_to_upper("go"), "GO") != 0) { return 10 }
    if (strcmp(go_strings_to_lower("GO"), "go") != 0) { return 11 }
    if (strcmp(go_strings_trim_space("  hi  "), "hi") != 0) { return 12 }
    if (strcmp(go_strings_repeat("ab", 3), "ababab") != 0) { return 13 }
    if (go_strings_count("banana", "an") != 2) { return 14 }
    if (go_strings_count("aaaa", "aa") != 2) { return 15 }
    if (strcmp(go_strings_replace_all("a-b-c", "-", "+"), "a+b+c") != 0) {
        return 16
    }
    if (go_strings_equal_fold("Go", "go") != 1) { return 17 }
    if (go_strings_equal_fold("Go", "go!") != 0) { return 18 }
    if (go_strings_compare("a", "b") >= 0) { return 19 }
    if (go_len("hello") != 5) { return 20 }
    // ---- strconv ----
    if (go_atoi("45") != 45) { return 30 }
    if (go_atoi("nope") != 0) { return 31 }
    if (strcmp(go_itoa(123), "123") != 0) { return 32 }
    if (strcmp(go_itoa(-7), "-7") != 0) { return 33 }
    if (strcmp(go_format_float(2.5), "2.5") != 0) { return 34 }
    // ---- math ----
    if (go_math_abs(-5) != 5) { return 40 }
    if (go_math_max(3, 9) != 9) { return 41 }
    if (go_math_min(3, 9) != 3) { return 42 }
    if (go_math_floor(2.7) != 2.0) { return 43 }
    if (go_math_ceil(2.1) != 3.0) { return 44 }
    if (go_math_sqrt(9.0) != 3.0) { return 45 }
    if (go_math_pow(2.0, 3.0) != 8.0) { return 46 }
    // ---- slices ----
    int g[3] = {4, 2, 6}
    if (go_slices_index(g, 3, 6) != 2) { return 50 }
    if (go_slices_index(g, 3, 5) != -1) { return 51 }
    if (go_slices_contains(g, 3, 5) != 0) { return 52 }
    if (go_slices_max(g, 3) != 6) { return 53 }
    if (go_slices_min(g, 3) != 2) { return 54 }
    if (go_slices_sum(g, 3) != 12) { return 55 }
    go_slices_reverse(g, 3)
    if (g[0] != 6 || g[2] != 4) { return 56 }
    return 0
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_gostd_go(workdir, use_native):
    path = _write_workdir(workdir, 't_gostd.cin', GOSTD_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    _run_ok(cpu)


# ====================================================================
# 4. 三库同载 (互不冲突, 无函数名碰撞)
# ====================================================================

ALL_LIBS_SRC = '''\
import "cstd.cin"
import "cppstd.cin"
import "gostd.cin"
function main() -> int {
    if (libc_isdigit('8') != 1) { return 1 }
    if (stl_str_empty("") != 1) { return 2 }
    if (go_strings_has_prefix("cin", "ci") != 1) { return 3 }
    return 0
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_all_compat_libs_together(workdir, use_native):
    path = _write_workdir(workdir, 't_all_libs.cin', ALL_LIBS_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    _run_ok(cpu)


# ====================================================================
# 5. arg_count / arg — 命令行参数 (需原生路径)
# ====================================================================

ARGS_SRC = '''\
function main() -> int {
    if (arg_count() != 2) { return 1 }
    if (strcmp(arg(0), "hello") != 0) { return 2 }
    if (strcmp(arg(1), "中文") != 0) { return 3 }
    if (strcmp(arg(99), "") != 0) { return 4 }
    if (strcmp(arg(-1), "") != 0) { return 5 }
    return 0
}'''


@needs_native
def test_arg_count_and_arg():
    cpu = build_cpu(ARGS_SRC, use_native=True,
                    program_args=['hello', '中文'])
    cpu.run()
    _run_ok(cpu)


ARGS_EMPTY_SRC = '''\
function main() -> int {
    if (arg_count() != 0) { return 1 }
    if (strcmp(arg(0), "") != 0) { return 2 }
    return 0
}'''


@needs_native
def test_arg_defaults_empty_without_args():
    """未注入参数时 arg_count()==0, arg(i) 为空串 (不残留上次运行的参数)。"""
    cpu = build_cpu(ARGS_EMPTY_SRC, use_native=True)
    cpu.run()
    _run_ok(cpu)


GOSTD_OS_SRC = '''\
import "gostd.cin"
function main() -> int {
    if (go_os_args_len() != 1) { return 1 }
    if (strcmp(go_os_args_get(0), "beta") != 0) { return 2 }
    return 0
}'''


@needs_native
def test_go_os_args_wrapper(workdir):
    path = _write_workdir(workdir, 't_go_os.cin', GOSTD_OS_SRC)
    cpu = run_cin_file(path, use_native=True, program_args=['beta'])
    _run_ok(cpu)


# ====================================================================
# 6. input_str — 行输入 (预读缓冲; 需原生路径)
# ====================================================================

INPUT_STR_SRC = '''\
function main() -> int {
    string a = input_str()
    string b = input_str()
    string c = input_str()
    if (strcmp(a, "hello") != 0) { return 1 }
    if (strcmp(b, "world") != 0) { return 2 }
    if (strcmp(c, "中文输入") != 0) { return 3 }
    return 0
}'''


@needs_native
def test_input_str_reads_piped_buffer():
    """input_str 经预读缓冲逐行读取, 行尾 \\n 剥离, UTF-8 保持。"""
    cpu = build_cpu(INPUT_STR_SRC, use_native=True)
    cpu.input_buffer = "hello\nworld\n中文输入\n"
    cpu.run()
    _run_ok(cpu)


# ====================================================================
# 7. 新内建无原生库时可编译 (SYS 编码存在)
# ====================================================================

def test_new_builtins_compile_without_native():
    src = '''
function main() -> int {
    int n = arg_count()
    string s = arg(0)
    string line = input_str()
    return n
}'''
    res = CINCompiler().compile_source(src)
    assert res is not None
    sys_count = sum(1 for i in res.instructions if i[0] == 'SYS')
    assert sys_count >= 3  # ARGC / ARGV / READLINE
