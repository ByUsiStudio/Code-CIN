"""官方标准库 heap.cin (定长二叉堆) 测试。

覆盖: 每个公开函数至少一个用例 + 空堆 / 单元素 / 满堆 / build 参数钳制 /
replace 空堆 / heap_sort 边界 (n <= 1、重复值、负数、64 元素) 等。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_heap_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

HEAP_SRC = '''
import "heap.cin"

function main() -> int {
    if (heap_capacity() != 64) { return 1 }

    // ---- 空堆 (最小堆) ----
    heap_reset(0)
    if (heap_size() != 0) { return 2 }
    if (heap_is_empty() != 1) { return 3 }
    if (heap_peek() != 0) { return 4 }
    if (heap_pop() != 0) { return 5 }
    if (heap_is_valid() != 1) { return 6 }

    // ---- 最小堆: 逐个 push / pop ----
    if (heap_push(5) != 1) { return 7 }
    if (heap_size() != 1) { return 8 }
    if (heap_is_empty() != 0) { return 9 }
    if (heap_peek() != 5) { return 10 }
    if (heap_push(3) != 1) { return 11 }
    if (heap_push(8) != 1) { return 12 }
    if (heap_push(1) != 1) { return 13 }
    if (heap_push(9) != 1) { return 14 }
    if (heap_push(2) != 1) { return 15 }
    if (heap_size() != 6) { return 16 }
    if (heap_peek() != 1) { return 17 }
    if (heap_is_valid() != 1) { return 18 }
    if (heap_pop() != 1) { return 19 }
    if (heap_pop() != 2) { return 20 }
    if (heap_pop() != 3) { return 21 }
    if (heap_pop() != 5) { return 22 }
    if (heap_pop() != 8) { return 23 }
    if (heap_pop() != 9) { return 24 }
    if (heap_is_empty() != 1) { return 25 }
    if (heap_pop() != 0) { return 26 }
    if (heap_size() != 0) { return 27 }

    // ---- 最大堆 ----
    heap_reset(1)
    int v[7] = {5, 3, 8, 1, 9, 2, 7}
    for (int i = 0; i < 7; i = i + 1) {
        if (heap_push(v[i]) != 1) { return 28 }
    }
    if (heap_peek() != 9) { return 29 }
    if (heap_is_valid() != 1) { return 30 }
    if (heap_pop() != 9) { return 31 }
    if (heap_pop() != 8) { return 32 }
    if (heap_pop() != 7) { return 33 }
    if (heap_pop() != 5) { return 34 }
    if (heap_pop() != 3) { return 35 }
    if (heap_pop() != 2) { return 36 }
    if (heap_pop() != 1) { return 37 }
    if (heap_is_empty() != 1) { return 38 }

    // ---- heap_reset 切换模式会清空 ----
    heap_push(4)
    heap_reset(0)
    if (heap_size() != 0) { return 39 }

    // ---- heap_build: 最小堆 / 最大堆 ----
    int a[6] = {4, 8, 1, 8, 3, 6}
    heap_build(a, 6, 0)
    if (heap_size() != 6) { return 40 }
    if (heap_peek() != 1) { return 41 }
    if (heap_is_valid() != 1) { return 42 }
    if (heap_pop() != 1) { return 43 }
    if (heap_pop() != 3) { return 44 }
    if (heap_pop() != 4) { return 45 }
    if (heap_pop() != 6) { return 46 }
    if (heap_pop() != 8) { return 47 }
    if (heap_pop() != 8) { return 48 }
    if (heap_is_empty() != 1) { return 49 }

    heap_build(a, 6, 1)
    if (heap_size() != 6) { return 50 }
    if (heap_peek() != 8) { return 51 }
    if (heap_is_valid() != 1) { return 52 }
    if (heap_pop() != 8) { return 53 }
    if (heap_pop() != 8) { return 54 }
    if (heap_pop() != 6) { return 55 }

    // ---- heap_build 参数钳制 ----
    heap_build(a, 0, 0)
    if (heap_size() != 0 || heap_is_empty() != 1) { return 56 }
    if (heap_is_valid() != 1) { return 57 }
    heap_build(a, -5, 0)
    if (heap_size() != 0) { return 58 }
    heap_build(a, 1, 1)
    if (heap_size() != 1 || heap_peek() != 4) { return 59 }
    heap_build(a, 1, 0)
    if (heap_size() != 1 || heap_peek() != 4) { return 60 }

    // ---- heap_replace ----
    heap_reset(0)
    if (heap_replace(7) != 0) { return 61 }
    if (heap_size() != 1 || heap_peek() != 7) { return 62 }
    if (heap_push(3) != 1) { return 63 }
    if (heap_push(9) != 1) { return 64 }
    if (heap_replace(4) != 3) { return 65 }
    if (heap_peek() != 4) { return 66 }
    if (heap_size() != 3) { return 67 }
    if (heap_is_valid() != 1) { return 68 }

    heap_reset(1)
    heap_push(1)
    heap_push(5)
    if (heap_replace(2) != 5) { return 69 }
    if (heap_peek() != 2) { return 70 }
    if (heap_replace(9) != 2) { return 71 }
    if (heap_peek() != 9) { return 72 }
    if (heap_is_valid() != 1) { return 73 }

    // ---- 满堆 (64 个元素) ----
    heap_reset(0)
    for (int i = 0; i < 64; i = i + 1) {
        if (heap_push(100 - i) != 1) { return 74 }
    }
    if (heap_size() != 64) { return 75 }
    if (heap_is_valid() != 1) { return 76 }
    if (heap_peek() != 37) { return 77 }
    if (heap_push(0) != 0) { return 78 }
    if (heap_size() != 64) { return 79 }
    if (heap_peek() != 37) { return 80 }
    for (int i = 0; i < 64; i = i + 1) {
        if (heap_pop() != 37 + i) { return 81 }
    }
    if (heap_is_empty() != 1) { return 82 }
    if (heap_is_valid() != 1) { return 83 }

    // ---- heap_sort: 就地升序 ----
    int s[8] = {5, 3, 8, 1, 6, 2, 7, 4}
    heap_sort(s, 8)
    for (int i = 0; i < 8; i = i + 1) {
        if (s[i] != i + 1) { return 84 }
    }
    int one[1] = {42}
    heap_sort(one, 1)
    if (one[0] != 42) { return 85 }
    heap_sort(one, 0)
    if (one[0] != 42) { return 86 }
    heap_sort(one, -3)
    if (one[0] != 42) { return 87 }
    int d[6] = {0, -5, 3, -5, 3, 0}
    heap_sort(d, 6)
    if (d[0] != -5 || d[1] != -5 || d[2] != 0 || d[3] != 0) { return 88 }
    if (d[4] != 3 || d[5] != 3) { return 89 }

    int big[64]
    for (int i = 0; i < 64; i = i + 1) {
        big[i] = (i * 37 + 11) % 64
    }
    heap_sort(big, 64)
    for (int i = 1; i < 64; i = i + 1) {
        if (big[i - 1] > big[i]) { return 90 }
    }
    if (big[0] < 0 || big[63] > 63) { return 91 }

    // ---- heap_sort 不破坏堆状态 ----
    heap_reset(1)
    heap_push(42)
    heap_push(7)
    heap_sort(s, 8)
    if (heap_size() != 2) { return 92 }
    if (heap_peek() != 42) { return 93 }
    if (heap_pop() != 42) { return 94 }
    if (heap_pop() != 7) { return 95 }
    return 0
}'''

def test_heap_lib(workdir):
    cpu = _run(workdir, HEAP_SRC)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0

TOGETHER_SRC = '''
import "tree.cin"
import "unionfind.cin"
import "heap.cin"

function main() -> int {
    tree_reset()
    if (tree_insert(2) != 1) { return 1 }
    if (tree_insert(1) != 1) { return 2 }
    if (tree_insert(3) != 1) { return 3 }
    if (strcmp(tree_inorder_str(), "1 2 3") != 0) { return 4 }
    if (tree_size() != 3) { return 5 }

    uf_reset(4)
    if (uf_union(0, 3) != 1) { return 6 }
    if (uf_connected(0, 3) != 1) { return 7 }
    if (uf_count() != 3) { return 8 }

    heap_reset(0)
    if (heap_push(9) != 1) { return 9 }
    if (heap_push(4) != 1) { return 10 }
    if (heap_peek() != 4) { return 11 }
    if (heap_pop() != 4) { return 12 }
    if (tree_size() != 3) { return 13 }
    return 0
}'''

def test_new_libs_are_importable_together(workdir):
    """三个新库同时导入不得冲突 (全局符号/数组名/函数名)。"""
    cpu = _run(workdir, TOGETHER_SRC, name='lib_tree_uf_heap.cin')
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
