"""官方标准库 set.cin (整数集合位图) 测试。

覆盖: 每个公开函数至少一个用例 + 空集 / 单元素 / 满集 / 元素越界 /
which 越界 / 位图跨字 / 三个槽互相独立 / 原地集合运算 等边界。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_set_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


SET_SRC = '''
import "set.cin"

function main() -> int {
    // ---- 常量 ----
    if (set_max() != 127) { return 1 }
    if (set_capacity() != 128) { return 2 }
    if (set_slots() != 3) { return 3 }

    // ---- 空集 (全局数据区默认全零) ----
    if (set_size(0) != 0) { return 4 }
    if (set_size(1) != 0) { return 5 }
    if (set_size(2) != 0) { return 6 }
    if (set_is_empty(0) != 1) { return 7 }
    if (set_is_full(0) != 0) { return 8 }
    if (set_min(0) != -1) { return 9 }
    if (set_max_value(0) != -1) { return 10 }
    if (set_count_range(0, 0, 127) != 0) { return 11 }
    if (set_contains(0, 5) != 0) { return 12 }
    if (set_remove(0, 5) != 0) { return 13 }
    if (set_is_subset(0, 1) != 1) { return 14 }
    if (set_equals(0, 1) != 1) { return 15 }
    if (strcmp(set_to_str(0), "{}") != 0) { return 16 }

    // ---- 单元素 ----
    if (set_add(0, 5) != 1) { return 17 }
    if (set_add(0, 5) != 0) { return 18 }
    if (set_contains(0, 5) != 1) { return 19 }
    if (set_contains(0, 4) != 0) { return 20 }
    if (set_size(0) != 1) { return 21 }
    if (set_is_empty(0) != 0) { return 22 }
    if (set_min(0) != 5) { return 23 }
    if (set_max_value(0) != 5) { return 24 }
    if (set_count_range(0, 0, 127) != 1) { return 25 }
    if (strcmp(set_to_str(0), "{5}") != 0) { return 26 }
    if (set_remove(0, 5) != 1) { return 27 }
    if (set_remove(0, 5) != 0) { return 28 }
    if (set_size(0) != 0) { return 29 }
    if (set_is_empty(0) != 1) { return 30 }

    // ---- 位图跨字: 0 / 63 / 64 / 127 ----
    if (set_add(0, 0) != 1) { return 31 }
    if (set_add(0, 63) != 1) { return 32 }
    if (set_add(0, 64) != 1) { return 33 }
    if (set_add(0, 127) != 1) { return 34 }
    if (set_size(0) != 4) { return 35 }
    if (set_min(0) != 0) { return 36 }
    if (set_max_value(0) != 127) { return 37 }
    if (strcmp(set_to_str(0), "{0, 63, 64, 127}") != 0) { return 38 }
    if (set_count_range(0, 0, 62) != 1) { return 39 }
    if (set_count_range(0, 0, 63) != 2) { return 40 }
    if (set_count_range(0, 63, 64) != 2) { return 41 }
    if (set_count_range(0, 64, 126) != 1) { return 42 }
    if (set_count_range(0, 64, 127) != 2) { return 43 }
    if (set_count_range(0, 100, 5) != 0) { return 44 }
    if (set_count_range(0, -10, 0) != 1) { return 45 }
    if (set_count_range(0, 120, 999) != 1) { return 46 }
    if (set_count_range(0, -5, -1) != 0) { return 47 }
    if (set_count_range(0, 200, 300) != 0) { return 48 }

    // ---- 元素越界: 不写内存 ----
    if (set_add(0, -1) != 0) { return 49 }
    if (set_add(0, 128) != 0) { return 50 }
    if (set_remove(0, -1) != 0) { return 51 }
    if (set_remove(0, 128) != 0) { return 52 }
    if (set_contains(0, -1) != 0) { return 53 }
    if (set_contains(0, 128) != 0) { return 54 }
    if (set_size(0) != 4) { return 55 }

    // ---- which 越界 ----
    if (set_reset(-1) != 0) { return 56 }
    if (set_reset(3) != 0) { return 57 }
    if (set_clear(3) != 0) { return 58 }
    if (set_fill(3) != 0) { return 59 }
    if (set_add(3, 1) != 0) { return 60 }
    if (set_add(-1, 1) != 0) { return 61 }
    if (set_remove(3, 1) != 0) { return 62 }
    if (set_contains(3, 1) != 0) { return 63 }
    if (set_size(3) != -1) { return 64 }
    if (set_size(-1) != -1) { return 65 }
    if (set_is_empty(3) != -1) { return 66 }
    if (set_is_full(3) != 0) { return 67 }
    if (set_min(3) != -1) { return 68 }
    if (set_max_value(3) != -1) { return 69 }
    if (set_count_range(3, 0, 127) != -1) { return 70 }
    if (strcmp(set_to_str(3), "") != 0) { return 71 }
    if (set_copy_into(3, 0) != 0) { return 72 }
    if (set_copy_into(0, 3) != 0) { return 73 }
    if (set_union_into(3, 0, 1) != 0) { return 74 }
    if (set_union_into(0, 3, 1) != 0) { return 75 }
    if (set_union_into(0, 0, 3) != 0) { return 76 }
    if (set_intersect_into(3, 0, 1) != 0) { return 77 }
    if (set_diff_into(3, 0, 1) != 0) { return 78 }
    if (set_is_subset(0, 3) != 0) { return 79 }
    if (set_is_subset(3, 0) != 0) { return 80 }
    if (set_equals(0, 3) != 0) { return 81 }
    if (set_size(0) != 4) { return 82 }

    // ---- A / B 两个集合互相独立 ----
    if (set_add(1, 1) != 1) { return 83 }
    if (set_add(1, 2) != 1) { return 84 }
    if (set_size(1) != 2) { return 85 }
    if (set_size(0) != 4) { return 86 }
    if (set_contains(0, 1) != 0) { return 87 }
    if (set_contains(1, 0) != 0) { return 88 }
    if (set_contains(1, 127) != 0) { return 89 }
    if (strcmp(set_to_str(1), "{1, 2}") != 0) { return 90 }
    if (set_is_subset(1, 0) != 0) { return 91 }
    if (set_is_subset(0, 1) != 0) { return 92 }
    if (set_equals(0, 1) != 0) { return 93 }

    // ---- set_copy_into ----
    if (set_copy_into(2, 1) != 1) { return 94 }
    if (set_equals(1, 2) != 1) { return 95 }
    if (set_size(2) != 2) { return 96 }
    if (set_copy_into(1, 1) != 1) { return 97 }
    if (set_size(1) != 2) { return 98 }
    if (set_copy_into(1, 0) != 1) { return 99 }
    if (set_size(1) != 4) { return 100 }
    if (set_equals(0, 1) != 1) { return 101 }
    if (set_copy_into(1, 2) != 1) { return 102 }
    if (set_size(1) != 2) { return 103 }

    // ---- 集合运算 ----
    set_reset_all()
    if (set_size(0) != 0 || set_size(1) != 0 || set_size(2) != 0) { return 104 }
    if (set_add(0, 0) != 1) { return 105 }
    if (set_add(0, 63) != 1) { return 106 }
    if (set_add(0, 64) != 1) { return 107 }
    if (set_add(0, 127) != 1) { return 108 }
    if (set_add(1, 1) != 1) { return 109 }
    if (set_add(1, 2) != 1) { return 110 }
    if (set_add(1, 64) != 1) { return 111 }
    if (set_union_into(2, 0, 1) != 1) { return 112 }
    if (set_size(2) != 6) { return 113 }
    if (strcmp(set_to_str(2), "{0, 1, 2, 63, 64, 127}") != 0) { return 114 }
    if (set_intersect_into(2, 0, 1) != 1) { return 115 }
    if (strcmp(set_to_str(2), "{64}") != 0) { return 116 }
    if (set_diff_into(2, 0, 1) != 1) { return 117 }
    if (strcmp(set_to_str(2), "{0, 63, 127}") != 0) { return 118 }
    if (set_diff_into(2, 1, 0) != 1) { return 119 }
    if (strcmp(set_to_str(2), "{1, 2}") != 0) { return 120 }
    if (set_union_into(0, 0, 1) != 1) { return 121 }
    if (set_size(0) != 6) { return 122 }
    if (set_equals(0, 0) != 1) { return 123 }
    if (set_intersect_into(1, 0, 1) != 1) { return 124 }
    if (strcmp(set_to_str(1), "{1, 2, 64}") != 0) { return 125 }
    if (set_diff_into(0, 0, 1) != 1) { return 126 }
    if (strcmp(set_to_str(0), "{0, 63, 127}") != 0) { return 127 }
    if (set_union_into(0, 0, 0) != 1) { return 128 }
    if (strcmp(set_to_str(0), "{0, 63, 127}") != 0) { return 129 }
    if (set_intersect_into(0, 0, 0) != 1) { return 130 }
    if (strcmp(set_to_str(0), "{0, 63, 127}") != 0) { return 131 }
    if (set_diff_into(0, 0, 0) != 1) { return 132 }
    if (strcmp(set_to_str(0), "{}") != 0) { return 133 }

    // ---- 子集 / 相等 ----
    set_reset_all()
    if (set_add(0, 1) != 1) { return 134 }
    if (set_add(0, 2) != 1) { return 135 }
    if (set_add(0, 3) != 1) { return 136 }
    if (set_add(1, 1) != 1) { return 137 }
    if (set_add(1, 2) != 1) { return 138 }
    if (set_is_subset(1, 0) != 1) { return 139 }
    if (set_is_subset(0, 1) != 0) { return 140 }
    if (set_is_subset(0, 0) != 1) { return 141 }
    if (set_is_subset(2, 0) != 1) { return 142 }
    if (set_is_subset(0, 2) != 0) { return 143 }
    if (set_equals(0, 1) != 0) { return 144 }
    if (set_equals(1, 1) != 1) { return 145 }
    if (set_equals(2, 2) != 1) { return 146 }
    if (set_equals(2, 0) != 0) { return 147 }
    if (set_copy_into(2, 0) != 1) { return 148 }
    if (set_equals(0, 2) != 1) { return 149 }
    if (set_is_subset(0, 2) != 1) { return 150 }
    if (set_is_subset(2, 1) != 0) { return 151 }

    // ---- 满集 ----
    if (set_fill(0) != 1) { return 152 }
    if (set_size(0) != 128) { return 153 }
    if (set_is_full(0) != 1) { return 154 }
    if (set_is_empty(0) != 0) { return 155 }
    if (set_min(0) != 0) { return 156 }
    if (set_max_value(0) != 127) { return 157 }
    if (set_count_range(0, 0, 127) != 128) { return 158 }
    if (set_count_range(0, 64, 127) != 64) { return 159 }
    if (set_count_range(0, 127, 127) != 1) { return 160 }
    if (set_count_range(0, 128, 200) != 0) { return 161 }
    if (set_contains(0, 10) != 1) { return 162 }
    if (set_add(0, 10) != 0) { return 163 }
    if (set_contains(0, 10) != 1) { return 164 }
    if (set_is_subset(1, 0) != 1) { return 165 }
    if (set_fill(1) != 1) { return 166 }
    if (set_equals(0, 1) != 1) { return 167 }
    if (set_union_into(2, 0, 1) != 1) { return 168 }
    if (set_size(2) != 128) { return 169 }
    if (set_intersect_into(2, 0, 1) != 1) { return 170 }
    if (set_size(2) != 128) { return 171 }
    if (set_diff_into(2, 0, 1) != 1) { return 172 }
    if (set_size(2) != 0) { return 173 }
    if (strcmp(set_to_str(2), "{}") != 0) { return 174 }

    // ---- set_reset / set_clear / set_reset_all ----
    if (set_reset(0) != 1) { return 175 }
    if (set_size(0) != 0) { return 176 }
    if (set_is_full(0) != 0) { return 177 }
    if (set_fill(0) != 1) { return 178 }
    if (set_clear(0) != 1) { return 179 }
    if (set_size(0) != 0) { return 180 }
    set_reset_all()
    if (set_size(0) != 0 || set_size(1) != 0 || set_size(2) != 0) { return 181 }

    // ---- 逐位填满再逐位清空 (跨字计数正确性) ----
    for (int i = 0; i < 128; i = i + 1) {
        if (set_add(0, i) != 1) { return 182 }
    }
    if (set_size(0) != 128) { return 183 }
    if (set_is_full(0) != 1) { return 184 }
    for (int i = 0; i < 128; i = i + 1) {
        if (set_contains(0, i) != 1) { return 185 }
    }
    for (int i = 0; i < 128; i = i + 1) {
        if (set_add(0, i) != 0) { return 186 }
    }
    for (int i = 0; i < 128; i = i + 1) {
        if (set_remove(0, i) != 1) { return 187 }
    }
    if (set_size(0) != 0) { return 188 }
    if (set_is_full(0) != 0) { return 189 }
    for (int i = 0; i < 128; i = i + 1) {
        if (set_remove(0, i) != 0) { return 190 }
    }

    // ---- 奇数 / 偶数模式 ----
    for (int i = 1; i < 128; i = i + 2) {
        set_add(0, i)
    }
    if (set_size(0) != 64) { return 191 }
    if (set_min(0) != 1) { return 192 }
    if (set_max_value(0) != 127) { return 193 }
    if (set_count_range(0, 0, 0) != 0) { return 194 }
    if (set_count_range(0, 1, 1) != 1) { return 195 }
    if (set_count_range(0, 64, 127) != 32) { return 196 }
    set_reset(0)
    for (int i = 0; i < 128; i = i + 2) {
        set_add(0, i)
    }
    if (set_size(0) != 64) { return 197 }
    if (set_min(0) != 0) { return 198 }
    if (set_max_value(0) != 126) { return 199 }
    if (set_count_range(0, 64, 127) != 32) { return 200 }

    // ---- set_to_str 细节 ----
    set_reset(0)
    set_add(0, 127)
    set_add(0, 0)
    set_add(0, 1)
    if (strcmp(set_to_str(0), "{0, 1, 127}") != 0) { return 201 }
    set_reset(0)
    set_add(0, 63)
    if (strcmp(set_to_str(0), "{63}") != 0) { return 202 }
    set_reset(0)
    if (strcmp(set_to_str(0), "{}") != 0) { return 203 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_set_lib(workdir, use_native):
    cpu = _run(workdir, SET_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
