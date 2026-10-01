"""官方标准库 unionfind.cin (并查集) 测试。

覆盖: 每个公开函数至少一个用例 + 未初始化 / 单元素 / 满容量 / 越界编号 /
n 钳制 / 重复合并 等边界。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_unionfind_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


UF_SRC = '''
import "unionfind.cin"

function main() -> int {
    if (uf_capacity() != 64) { return 1 }

    // ---- 未初始化: uf_n = 0, 一切编号非法 ----
    if (uf_count() != 0) { return 2 }
    if (uf_find(0) != -1) { return 3 }
    if (uf_find(-1) != -1) { return 4 }
    if (uf_size(0) != 0) { return 5 }
    if (uf_rank(0) != -1) { return 6 }
    if (uf_union(0, 1) != 0) { return 7 }
    if (uf_connected(0, 1) != 0) { return 8 }

    // ---- 初始化后每个元素自成一集合 ----
    uf_reset(5)
    if (uf_count() != 5) { return 9 }
    for (int i = 0; i < 5; i = i + 1) {
        if (uf_find(i) != i) { return 10 }
        if (uf_size(i) != 1) { return 11 }
        if (uf_rank(i) != 0) { return 12 }
    }
    // 越界编号
    if (uf_find(5) != -1) { return 13 }
    if (uf_rank(5) != -1) { return 14 }
    if (uf_size(5) != 0) { return 15 }
    if (uf_connected(0, 5) != 0) { return 16 }
    if (uf_union(0, 5) != 0) { return 17 }

    // ---- 合并 (含重复合并) ----
    if (uf_union(0, 1) != 1) { return 18 }
    if (uf_union(0, 1) != 0) { return 19 }
    if (uf_union(1, 0) != 0) { return 20 }
    if (uf_connected(0, 1) != 1) { return 21 }
    if (uf_connected(1, 0) != 1) { return 22 }
    if (uf_count() != 4) { return 23 }
    if (uf_size(0) != 2 || uf_size(1) != 2) { return 24 }
    if (uf_find(0) != uf_find(1)) { return 25 }

    if (uf_union(2, 3) != 1) { return 26 }
    if (uf_union(3, 4) != 1) { return 27 }
    if (uf_count() != 2) { return 28 }
    if (uf_size(2) != 3 || uf_size(4) != 3) { return 29 }
    if (uf_connected(2, 4) != 1) { return 30 }
    if (uf_connected(0, 2) != 0) { return 31 }
    if (uf_union(1, 4) != 1) { return 32 }
    if (uf_count() != 1) { return 33 }
    if (uf_size(3) != 5 || uf_size(0) != 5) { return 34 }
    if (uf_connected(0, 4) != 1) { return 35 }

    // ---- 按秩合并: 两棵同秩树合并后根秩增加 ----
    uf_reset(4)
    if (uf_union(0, 1) != 1) { return 36 }
    if (uf_union(2, 3) != 1) { return 37 }
    if (uf_rank(0) != 1) { return 38 }
    if (uf_union(0, 2) != 1) { return 39 }
    if (uf_rank(0) < 2) { return 40 }
    if (uf_size(0) != 4) { return 41 }
    if (uf_find(3) != uf_find(0)) { return 42 }

    // ---- n 钳制 ----
    uf_reset(0)
    if (uf_count() != 0) { return 43 }
    if (uf_find(0) != -1) { return 44 }
    uf_reset(-7)
    if (uf_count() != 0) { return 45 }
    uf_reset(1000)
    if (uf_count() != 64) { return 46 }
    if (uf_find(63) != 63) { return 47 }
    if (uf_find(64) != -1) { return 48 }
    if (uf_size(63) != 1) { return 49 }

    // ---- 单元素 ----
    uf_reset(1)
    if (uf_count() != 1) { return 50 }
    if (uf_find(0) != 0) { return 51 }
    if (uf_size(0) != 1) { return 52 }
    if (uf_union(0, 0) != 0) { return 53 }
    if (uf_connected(0, 0) != 1) { return 54 }
    if (uf_count() != 1) { return 55 }
    if (uf_union(0, 1) != 0) { return 56 }

    // ---- 满容量: 全部并成一个集合 ----
    uf_reset(64)
    for (int i = 1; i < 64; i = i + 1) {
        if (uf_union(0, i) != 1) { return 57 }
    }
    if (uf_count() != 1) { return 58 }
    for (int i = 0; i < 64; i = i + 1) {
        if (uf_connected(0, i) != 1) { return 59 }
        if (uf_size(i) != 64) { return 60 }
    }
    if (uf_union(0, 63) != 0) { return 61 }
    if (uf_count() != 1) { return 62 }

    // ---- 越界参数不改变状态 ----
    if (uf_union(-1, 0) != 0) { return 63 }
    if (uf_union(0, 64) != 0) { return 64 }
    if (uf_union(-1, 99) != 0) { return 65 }
    if (uf_connected(-1, 64) != 0) { return 66 }
    if (uf_count() != 1) { return 67 }
    if (uf_size(64) != 0) { return 68 }
    if (uf_rank(-2) != -1) { return 69 }

    // ---- 重新 reset 后完全回到初始状态 ----
    uf_reset(3)
    if (uf_count() != 3) { return 70 }
    for (int i = 0; i < 3; i = i + 1) {
        if (uf_find(i) != i) { return 71 }
        if (uf_size(i) != 1) { return 72 }
        if (uf_rank(i) != 0) { return 73 }
    }
    // 1 元素链: 反复 union 也不改变分量数
    uf_reset(2)
    if (uf_union(0, 1) != 1) { return 74 }
    if (uf_union(1, 0) != 0) { return 75 }
    if (uf_count() != 1) { return 76 }
    if (uf_size(0) != 2 || uf_size(1) != 2) { return 77 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_unionfind_lib(workdir, use_native):
    cpu = _run(workdir, UF_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
