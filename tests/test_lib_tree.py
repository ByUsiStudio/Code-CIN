"""官方标准库 tree.cin (定长二叉搜索树) 测试。

覆盖: 每个公开函数至少一个用例 + 空树 / 单元素 / 满树 / 重复插入 / 重置 等边界。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。

注意: CIN 的字符串 `==` / `!=` 比较的是**指针**而不是内容,
因此串比较统一使用 `strcmp(a, b) != 0`, 空串判定使用 `strlen(s) != 0`。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_tree_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

TREE_SRC = '''
import "tree.cin"

function main() -> int {
    // ---- 空树边界 ----
    tree_reset()
    if (tree_capacity() != 64) { return 1 }
    if (tree_size() != 0) { return 2 }
    if (tree_is_empty() != 1) { return 3 }
    if (tree_min() != 0) { return 4 }
    if (tree_max() != 0) { return 5 }
    if (tree_height() != 0) { return 6 }
    if (tree_count_leaves() != 0) { return 7 }
    if (strlen(tree_inorder_str()) != 0) { return 8 }
    if (strlen(tree_preorder_str()) != 0) { return 9 }
    if (strlen(tree_postorder_str()) != 0) { return 10 }
    if (tree_contains(5) != 0) { return 11 }

    // ---- 单元素 ----
    if (tree_insert(50) != 1) { return 12 }
    if (tree_size() != 1) { return 13 }
    if (tree_is_empty() != 0) { return 14 }
    if (tree_min() != 50 || tree_max() != 50) { return 15 }
    if (tree_height() != 1) { return 16 }
    if (tree_count_leaves() != 1) { return 17 }
    if (strcmp(tree_inorder_str(), "50") != 0) { return 18 }
    if (strcmp(tree_preorder_str(), "50") != 0) { return 19 }
    if (strcmp(tree_postorder_str(), "50") != 0) { return 20 }
    if (tree_contains(50) != 1) { return 21 }

    // ---- 常规结构 + 重复插入 ----
    if (tree_insert(30) != 1) { return 22 }
    if (tree_insert(70) != 1) { return 23 }
    if (tree_insert(20) != 1) { return 24 }
    if (tree_insert(40) != 1) { return 25 }
    if (tree_insert(60) != 1) { return 26 }
    if (tree_insert(80) != 1) { return 27 }
    if (tree_insert(30) != 0) { return 28 }
    if (tree_insert(50) != 0) { return 29 }
    if (tree_size() != 7) { return 30 }
    if (tree_min() != 20 || tree_max() != 80) { return 31 }
    if (tree_height() != 3) { return 32 }
    if (tree_count_leaves() != 4) { return 33 }
    if (strcmp(tree_inorder_str(), "20 30 40 50 60 70 80") != 0) { return 34 }
    if (strcmp(tree_preorder_str(), "50 30 20 40 70 60 80") != 0) { return 35 }
    if (strcmp(tree_postorder_str(), "20 40 30 60 80 70 50") != 0) { return 36 }
    if (tree_contains(40) != 1) { return 37 }
    if (tree_contains(41) != 0) { return 38 }

    // ---- 满树 (64 个节点) ----
    for (int i = 100; i < 157; i = i + 1) {
        if (tree_insert(i) != 1) { return 39 }
    }
    if (tree_size() != 64) { return 40 }
    if (tree_is_empty() != 0) { return 41 }
    if (tree_insert(999) != 0) { return 42 }
    if (tree_insert(20) != 0) { return 43 }
    if (tree_size() != 64) { return 44 }
    if (tree_min() != 20) { return 45 }
    if (tree_max() != 156) { return 46 }
    if (tree_count_leaves() < 1) { return 47 }
    if (tree_height() < 1) { return 48 }
    if (strlen(tree_inorder_str()) < 1) { return 49 }

    // ---- clear / reset ----
    tree_clear()
    if (tree_size() != 0) { return 50 }
    if (tree_is_empty() != 1) { return 51 }
    if (strlen(tree_inorder_str()) != 0) { return 52 }
    if (strlen(tree_preorder_str()) != 0) { return 53 }
    if (strlen(tree_postorder_str()) != 0) { return 54 }
    if (tree_min() != 0 || tree_max() != 0 || tree_height() != 0) { return 55 }
    if (tree_contains(20) != 0) { return 56 }
    if (tree_insert(5) != 1) { return 57 }
    if (strcmp(tree_inorder_str(), "5") != 0) { return 58 }

    tree_reset()
    if (tree_size() != 0 || tree_is_empty() != 1) { return 59 }

    // ---- 退化(有序插入)后的高度与遍历 ----
    for (int i = 1; i <= 10; i = i + 1) {
        if (tree_insert(i) != 1) { return 60 }
    }
    if (tree_min() != 1 || tree_max() != 10) { return 61 }
    if (tree_height() != 10) { return 62 }
    if (tree_count_leaves() != 1) { return 63 }
    if (strcmp(tree_inorder_str(), "1 2 3 4 5 6 7 8 9 10") != 0) { return 64 }
    if (strcmp(tree_preorder_str(), "1 2 3 4 5 6 7 8 9 10") != 0) { return 65 }
    if (strcmp(tree_postorder_str(), "10 9 8 7 6 5 4 3 2 1") != 0) { return 66 }

    // ---- 负数键 ----
    tree_clear()
    if (tree_insert(-5) != 1) { return 67 }
    if (tree_insert(-10) != 1) { return 68 }
    if (tree_insert(0) != 1) { return 69 }
    if (tree_min() != -10 || tree_max() != 0) { return 70 }
    if (strcmp(tree_inorder_str(), "-10 -5 0") != 0) { return 71 }
    return 0
}'''

def test_tree_lib(workdir):
    cpu = _run(workdir, TREE_SRC)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
