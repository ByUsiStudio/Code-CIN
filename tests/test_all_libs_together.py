"""全部纯 CIN 标准库**同时导入**的集成护栏。

背景: SUGGESTIONS_ROUND3 §10 把「给新增的库补一条『全部库同时 import』的合并用例」
列为仍未一次覆盖的项 —— 各库的作者只验证了自己那一批的符号不冲突, 没有人验证过
"全部一起 import 会不会撞名 / 撞全局变量 / 撞数据段"。本文件补上这条。

约定:
  * 参与合并的是**纯 CIN 库**（只调用语言内建, 原生引擎单路径）;
  * `io` / `gui` / `termux` / `key` 依赖宿主能力, 只做 import（不调用）, 单独一组;
  * `test.cin` 与 `time.cin` 共用 `t_` 前缀, 文档明确写了"勿同时导入", 因此排除
    `test.cin`（它是断言工具, 不是被调用的功能库）。
"""

import os

import pytest

from tests.helpers import run_cin_file

#: 库 -> 一段"调用该库一个代表性接口"的 CIN 代码 (失败点由顺序决定)
PURE_LIB_CALLS = [
    ('array.cin', 'int a3[3] = {1, 2, 3}\n'
                  '    if (a_sum(a3, 3) != 6) { return %d }'),
    ('bigint.cin', 'BigInt bi = bi_from_int(7)\n'
                   '    if (strlen(bi_to_str(bi)) != 1) { return %d }'),
    ('bits.cin', 'if (bits_popcount(0xFF) != 8) { return %d }'),
    ('bitset.cin', 'bs_reset()\n    bs_set(3)\n'
                   '    if (bs_test(3) != 1) { return %d }'),
    ('codec.cin', 'if (strcmp(codec_rot13("Hello"), "Uryyb") != 0) { return %d }'),
    ('combin.cin', 'if (comb_gcd(48, 18) != 6) { return %d }'),
    ('conv.cin', 'if (strcmp(c_to_hex(255), "FF") != 0) { return %d }'),
    ('cppstd.cin', 'if (stl_max(3, 9) != 9) { return %d }'),
    ('cstd.cin', 'if (libc_isdigit(\'7\') != 1) { return %d }'),
    ('csv.cin', 'if (csv_count("a,b,c") != 3) { return %d }'),
    ('dp.cin', 'if (dp_fib(10) != 55) { return %d }'),
    ('fmt.cin', 'if (strcmp(fmt_thousands(1234567), "1,234,567") != 0) { return %d }'),
    ('frac.cin', 'Frac fx = fr_make(1, 2)\n    if (fr_num(fx) != 1) { return %d }'),
    ('gostd.cin', 'if (go_strings_has_prefix("cin", "ci") != 1) { return %d }'),
    ('graph.cin', 'graph_reset(3, 1)\n    if (graph_add_edge(0, 1, 5) != 1) { return %d }'),
    ('hash.cin', 'if (hash_djb2("abc") != hash_djb2("abc")) { return %d }'),
    ('heap.cin', 'heap_reset(0)\n    if (heap_push(5) != 1) { return %d }'),
    ('json.cin', 'if (strlen(j_str("{\\"a\\":\\"b\\"}", "a")) == 0) { return %d }'),
    ('math.cin', 'if (i_clamp(15, 0, 10) != 10) { return %d }'),
    ('matrix.cin', 'int m2[4] = {1, 0, 0, 1}\n'
                   '    if (mat_trace(m2, 2) != 2) { return %d }'),
    ('path.cin', 'if (strcmp(path_str_basename("/a/b/c.txt"), "c.txt") != 0) '
                 '{ return %d }'),
    ('queue.cin', 'queue_clear()\n    if (queue_push(1) != 1) { return %d }'),
    ('rand.cin', 'srand(1)\n    if (r_range(0, 10) < 0) { return %d }'),
    ('set.cin', 'set_reset(0)\n    if (set_add(0, 5) != 1) { return %d }'),
    ('sort.cin', 'int s4[4] = {3, 1, 4, 2}\n    sort_bubble(s4, 4)\n'
                 '    if (s4[0] != 1) { return %d }'),
    ('stat.cin', 'int st[3] = {1, 2, 3}\n    if (stat_sum(st, 3) != 6) { return %d }'),
    ('str.cin', 'if (strcmp(s_upper("ab"), "AB") != 0) { return %d }'),
    ('text.cin', 'if (strcmp(txt_reverse("abc"), "cba") != 0) { return %d }'),
    ('time.cin', 'if (t_hms(0) == "") { return %d }'),
    ('token.cin', 'if (tok_count("a,b", ",") != 2) { return %d }'),
    ('tree.cin', 'tree_reset()\n    if (tree_insert(5) != 1) { return %d }'),
    ('unionfind.cin', 'uf_reset(4)\n    if (uf_union(0, 1) != 1) { return %d }'),
    ('validate.cin', 'if (val_is_int("42") != 1) { return %d }'),
    ('vec.cin', 'float v3[3] = {1.0, 2.0, 3.0}\n    if (v_sum(v3, 3) != 6.0) '
                '{ return %d }'),
]

def _source(libs):
    """生成 import 列表 + 依次调用（返回第一个失败点编号）。"""
    imports = '\n'.join(f'import "{name}"' for name, _ in libs)
    body = []
    for i, (_, call) in enumerate(libs, start=1):
        body.append('    ' + (call % i))
    return f'{imports}\n\nfunction main() -> int {{\n' + '\n'.join(body) + \
           '\n    return 0\n}'

def _run(workdir, src, name, **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    return run_cin_file(path, **cfg)

def test_all_pure_libs_importable_together(workdir):
    """30+ 个纯 CIN 库同时导入且各调用一个接口, 原生引擎执行必须返回 0。"""
    cpu = _run(workdir, _source(PURE_LIB_CALLS), 'all_libs.cin')
    assert not cpu.execution_failed
    code = cpu.regs.read(0)
    if code:
        idx = code - 1
        who = PURE_LIB_CALLS[idx][0] if 0 <= idx < len(PURE_LIB_CALLS) else '?'
        pytest.fail(f'合并导入失败于第 {code} 项: {who}')

def test_pure_lib_list_matches_directory():
    """新库加入 codecin/lib 时必须同步进本用例, 否则这条护栏会悄悄失效。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    actual = {n for n in os.listdir(os.path.join(root, 'codecin', 'lib'))
              if n.endswith('.cin')}
    # 依赖宿主能力的库不在本用例里(它们另有 test_cin_host*.py /
    # test_ffi_net.py 覆盖)
    host_libs = {'io.cin', 'gui.cin', 'termux.cin', 'key.cin',
                 'ffi.cin', 'net.cin'}
    # test.cin 与 time.cin 共用 t_ 前缀, 文档要求勿同时导入
    covered = {name for name, _ in PURE_LIB_CALLS} | host_libs | {'test.cin'}
    assert actual == covered, (
        f'标准库清单与本用例不同步: 目录多出 {sorted(actual - covered)}, '
        f'本用例多出 {sorted(covered - actual)}')
