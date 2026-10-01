"""官方标准库 bitset.cin (512 位多字位集合) 测试。

覆盖: 每个公开函数至少一个用例 + 空集 / 单个 512 位满集 / 下标越界 /
槽号越界 / 位移 0 / 64 / 512 / 负数 / 跨字位移 / 两个槽互相独立 等边界。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_bitset_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


BS_SRC = '''
import "bitset.cin"

function main() -> int {
    // ---- 常量 ----
    if (bs_bits() != 512) { return 1 }
    if (bs_words() != 8) { return 2 }
    if (bs_slots() != 2) { return 3 }

    // ---- 槽选择 ----
    if (bs_selected() != 0) { return 4 }
    if (bs_select(1) != 1) { return 5 }
    if (bs_selected() != 1) { return 6 }
    if (bs_select(0) != 1) { return 7 }
    if (bs_selected() != 0) { return 8 }
    if (bs_select(2) != 0) { return 9 }
    if (bs_selected() != 0) { return 10 }
    if (bs_select(-1) != 0) { return 11 }
    if (bs_selected() != 0) { return 12 }

    // ---- 重置 ----
    bs_reset()
    if (bs_selected() != 0) { return 13 }

    // ---- 空集 ----
    if (bs_count() != 0) { return 14 }
    if (bs_any() != 0) { return 15 }
    if (bs_none() != 1) { return 16 }
    if (bs_first_set() != -1) { return 17 }
    if (bs_last_set() != -1) { return 18 }
    if (bs_next_set(0) != -1) { return 19 }
    if (bs_next_set(511) != -1) { return 20 }
    if (bs_to_int() != 0) { return 21 }
    if (bs_get_word(0) != 0) { return 22 }
    if (bs_get_word(7) != 0) { return 23 }
    if (bs_count_word(0) != 0) { return 24 }
    if (bs_count_word(7) != 0) { return 25 }
    if (bs_equals(1) != 1) { return 26 }

    // ---- 单元素 / 边界下标 0 与 511 ----
    if (bs_test(0) != 0) { return 27 }
    if (bs_set(0) != 1) { return 28 }
    if (bs_test(0) != 1) { return 29 }
    if (bs_count() != 1) { return 30 }
    if (bs_any() != 1) { return 31 }
    if (bs_none() != 0) { return 32 }
    if (bs_first_set() != 0) { return 33 }
    if (bs_last_set() != 0) { return 34 }
    if (bs_next_set(0) != 0) { return 35 }
    if (bs_next_set(1) != -1) { return 36 }
    if (bs_count_word(0) != 1) { return 37 }
    if (bs_count_word(1) != 0) { return 38 }
    if (bs_to_int() != 1) { return 39 }
    if (bs_clear(0) != 1) { return 40 }
    if (bs_count() != 0) { return 41 }
    if (bs_clear(0) != 1) { return 42 }
    if (bs_count() != 0) { return 43 }

    if (bs_set(511) != 1) { return 44 }
    if (bs_test(511) != 1) { return 45 }
    if (bs_first_set() != 511) { return 46 }
    if (bs_last_set() != 511) { return 47 }
    if (bs_count() != 1) { return 48 }
    if (bs_count_word(7) != 1) { return 49 }
    if (bs_count_word(6) != 0) { return 50 }
    if (bs_to_int() != 0) { return 51 }
    if (bs_next_set(0) != 511) { return 52 }
    if (bs_next_set(511) != 511) { return 53 }
    if (bs_next_set(510) != 511) { return 54 }
    if (bs_toggle(511) != 1) { return 55 }
    if (bs_test(511) != 0) { return 56 }
    if (bs_count() != 0) { return 57 }
    if (bs_toggle(511) != 1) { return 58 }
    if (bs_test(511) != 1) { return 59 }
    if (bs_clear(511) != 1) { return 60 }
    if (bs_count() != 0) { return 61 }

    // ---- 下标越界: 无操作 / 失败值 ----
    if (bs_test(-1) != 0) { return 62 }
    if (bs_test(512) != 0) { return 63 }
    if (bs_set(-1) != 0) { return 64 }
    if (bs_set(512) != 0) { return 65 }
    if (bs_clear(-1) != 0) { return 66 }
    if (bs_clear(512) != 0) { return 67 }
    if (bs_toggle(-1) != 0) { return 68 }
    if (bs_toggle(512) != 0) { return 69 }
    if (bs_next_set(-1) != -1) { return 70 }
    if (bs_next_set(512) != -1) { return 71 }
    if (bs_count_word(-1) != -1) { return 72 }
    if (bs_count_word(8) != -1) { return 73 }
    if (bs_get_word(-1) != 0) { return 74 }
    if (bs_get_word(8) != 0) { return 75 }
    if (bs_set_word(-1, 1) != 0) { return 76 }
    if (bs_set_word(8, 1) != 0) { return 77 }
    if (bs_count() != 0) { return 78 }

    // ---- 单字读写 ----
    if (bs_set_word(0, 0x1234) != 1) { return 79 }
    if (bs_get_word(0) != 0x1234) { return 80 }
    if (bs_get_word(1) != 0) { return 81 }
    if (bs_count_word(0) != 5) { return 82 }
    if (bs_count() != 5) { return 83 }
    if (bs_first_set() != 2) { return 84 }
    if (bs_last_set() != 12) { return 85 }
    if (bs_set_word(7, 0x8000000000000000) != 1) { return 86 }
    if (bs_count() != 6) { return 87 }
    if (bs_last_set() != 511) { return 88 }
    if (bs_first_set() != 2) { return 89 }
    bs_clear_all()
    if (bs_count() != 0) { return 90 }

    // ---- 多字: 每个字置最低位 ----
    for (int w = 0; w < 8; w = w + 1) {
        if (bs_set(w * 64) != 1) { return 91 }
    }
    if (bs_count() != 8) { return 92 }
    if (bs_first_set() != 0) { return 93 }
    if (bs_last_set() != 448) { return 94 }
    if (bs_next_set(1) != 64) { return 95 }
    if (bs_next_set(64) != 64) { return 96 }
    if (bs_next_set(65) != 128) { return 97 }
    if (bs_next_set(448) != 448) { return 98 }
    if (bs_next_set(449) != -1) { return 99 }
    for (int w = 0; w < 8; w = w + 1) {
        if (bs_count_word(w) != 1) { return 100 }
    }
    if (bs_to_int() != 1) { return 101 }

    // ---- 置满 512 位 ----
    bs_clear_all()
    for (int i = 0; i < 512; i = i + 1) {
        if (bs_set(i) != 1) { return 102 }
    }
    if (bs_count() != 512) { return 103 }
    if (bs_first_set() != 0) { return 104 }
    if (bs_last_set() != 511) { return 105 }
    if (bs_count_word(0) != 64) { return 106 }
    if (bs_count_word(7) != 64) { return 107 }
    if (bs_next_set(500) != 500) { return 108 }
    if (bs_any() != 1) { return 109 }
    if (bs_none() != 0) { return 110 }
    if (bs_to_int() != -1) { return 111 }
    for (int i = 0; i < 512; i = i + 1) {
        if (bs_test(i) != 1) { return 112 }
    }

    // ---- bs_not: 严格按 512 位取反 ----
    bs_not()
    if (bs_count() != 0) { return 113 }
    if (bs_any() != 0) { return 114 }
    if (bs_first_set() != -1) { return 115 }
    if (bs_to_int() != 0) { return 116 }
    bs_not()
    if (bs_count() != 512) { return 117 }
    if (bs_test(0) != 1 || bs_test(511) != 1) { return 118 }
    bs_clear(0)
    bs_not()
    if (bs_count() != 1) { return 119 }
    if (bs_test(0) != 1) { return 120 }
    if (bs_test(1) != 0) { return 121 }
    if (bs_first_set() != 0) { return 122 }
    bs_clear_all()
    bs_not()
    if (bs_count() != 512) { return 123 }
    if (bs_count_word(7) != 64) { return 124 }
    if (bs_to_int() != -1) { return 125 }

    // ---- from_int / to_int (只涉及低 64 位) ----
    bs_reset()
    bs_from_int(0x1234)
    if (bs_to_int() != 0x1234) { return 126 }
    if (bs_count() != 5) { return 127 }
    if (bs_count_word(1) != 0) { return 128 }
    if (bs_last_set() != 12) { return 129 }
    bs_from_int(0)
    if (bs_count() != 0) { return 130 }
    bs_from_int(-1)
    if (bs_to_int() != -1) { return 131 }
    if (bs_count() != 64) { return 132 }
    if (bs_first_set() != 0) { return 133 }
    if (bs_last_set() != 63) { return 134 }
    if (bs_count_word(1) != 0) { return 135 }
    bs_from_int(1)
    if (bs_to_int() != 1) { return 136 }
    if (bs_last_set() != 0) { return 137 }
    bs_clear_all()
    bs_set(500)
    if (bs_to_int() != 0) { return 138 }
    if (bs_count() != 1) { return 139 }
    if (bs_last_set() != 500) { return 140 }
    if (bs_count_word(7) != 1) { return 141 }

    // ---- 两个槽互相独立; 逻辑运算写回当前槽 ----
    bs_reset()
    if (bs_select(0) != 1) { return 142 }
    if (bs_set(1) != 1) { return 143 }
    if (bs_set(2) != 1) { return 144 }
    if (bs_select(1) != 1) { return 145 }
    if (bs_set(2) != 1) { return 146 }
    if (bs_set(3) != 1) { return 147 }
    if (bs_count() != 2) { return 148 }
    if (bs_test(1) != 0) { return 149 }
    if (bs_test(2) != 1) { return 150 }
    if (bs_test(3) != 1) { return 151 }
    if (bs_equals(0) != 0) { return 152 }

    // and: 槽1 = 槽1 & 槽0 = {2,3} & {1,2} = {2}
    if (bs_and(0) != 1) { return 153 }
    if (bs_count() != 1) { return 154 }
    if (bs_test(2) != 1) { return 155 }
    if (bs_test(3) != 0) { return 156 }
    if (bs_equals(1) != 1) { return 157 }
    if (bs_equals(0) != 0) { return 158 }
    if (bs_select(0) != 1) { return 159 }
    if (bs_count() != 2) { return 160 }
    if (bs_test(1) != 1) { return 161 }
    if (bs_test(2) != 1) { return 162 }
    if (bs_test(3) != 0) { return 163 }

    // or: {1,2} | {2} = {1,2}
    if (bs_or(1) != 1) { return 164 }
    if (bs_count() != 2) { return 165 }
    if (bs_test(1) != 1) { return 166 }
    if (bs_test(2) != 1) { return 167 }

    // xor: {1,2} ^ {2} = {1}
    if (bs_xor(1) != 1) { return 168 }
    if (bs_count() != 1) { return 169 }
    if (bs_test(1) != 1) { return 170 }
    if (bs_test(2) != 0) { return 171 }
    if (bs_equals(1) != 0) { return 172 }
    if (bs_set(2) != 1) { return 173 }
    if (bs_equals(1) != 0) { return 174 }
    if (bs_select(1) != 1) { return 175 }
    if (bs_set(1) != 1) { return 176 }
    if (bs_equals(0) != 1) { return 177 }
    if (bs_select(0) != 1) { return 178 }

    // 自身运算
    if (bs_and(0) != 1) { return 179 }
    if (bs_count() != 2) { return 180 }
    if (bs_xor(0) != 1) { return 181 }
    if (bs_count() != 0) { return 182 }

    // other 越界: 无操作
    if (bs_set(5) != 1) { return 183 }
    if (bs_and(2) != 0) { return 184 }
    if (bs_or(-1) != 0) { return 185 }
    if (bs_xor(7) != 0) { return 186 }
    if (bs_equals(2) != 0) { return 187 }
    if (bs_count() != 1) { return 188 }
    if (bs_test(5) != 1) { return 189 }

    // ---- copy_from / clear_slot / clear_all ----
    if (bs_select(0) != 1) { return 190 }
    bs_from_int(0)
    if (bs_set(10) != 1) { return 191 }
    if (bs_select(1) != 1) { return 192 }
    if (bs_set(20) != 1) { return 193 }
    if (bs_copy_from(0) != 1) { return 194 }
    if (bs_count() != 1) { return 195 }
    if (bs_test(10) != 1) { return 196 }
    if (bs_test(20) != 0) { return 197 }
    if (bs_copy_from(2) != 0) { return 198 }
    if (bs_copy_from(-1) != 0) { return 199 }
    if (bs_count() != 1) { return 200 }
    if (bs_copy_from(1) != 1) { return 201 }
    if (bs_count() != 1) { return 202 }
    if (bs_clear_slot(1) != 1) { return 203 }
    if (bs_count() != 0) { return 204 }
    if (bs_select(0) != 1) { return 205 }
    if (bs_count() != 1) { return 206 }
    if (bs_clear_slot(0) != 1) { return 207 }
    if (bs_count() != 0) { return 208 }
    if (bs_clear_slot(2) != 0) { return 209 }
    if (bs_clear_slot(-1) != 0) { return 210 }

    // clear_all 只清当前槽
    bs_select(0)
    bs_set(1)
    if (bs_select(1) != 1) { return 211 }
    bs_set(1)
    bs_clear_all()
    if (bs_count() != 0) { return 212 }
    if (bs_select(0) != 1) { return 213 }
    if (bs_count() != 1) { return 214 }
    if (bs_test(1) != 1) { return 215 }

    // reset 清两个槽并把当前槽复位为 0
    if (bs_select(1) != 1) { return 216 }
    bs_reset()
    if (bs_selected() != 0) { return 217 }
    if (bs_count() != 0) { return 218 }
    if (bs_select(1) != 1) { return 219 }
    if (bs_count() != 0) { return 220 }
    if (bs_select(0) != 1) { return 221 }

    // ---- 位移: 0 / 64 / 512 / 负数 / 跨字 ----
    bs_clear_all()
    if (bs_set(0) != 1) { return 222 }
    bs_shift_left(0)
    if (bs_test(0) != 1) { return 223 }
    if (bs_count() != 1) { return 224 }
    bs_shift_left(64)
    if (bs_test(64) != 1) { return 225 }
    if (bs_test(0) != 0) { return 226 }
    if (bs_count() != 1) { return 227 }
    bs_shift_left(64)
    if (bs_test(128) != 1) { return 228 }
    if (bs_count() != 1) { return 229 }
    bs_shift_left(-1)
    if (bs_test(128) != 1) { return 230 }
    if (bs_count() != 1) { return 231 }
    bs_shift_left(512)
    if (bs_count() != 0) { return 232 }
    if (bs_any() != 0) { return 233 }
    bs_set(3)
    bs_shift_left(1000)
    if (bs_count() != 0) { return 234 }
    bs_set(3)
    bs_shift_left(-5)
    if (bs_test(3) != 1) { return 235 }
    if (bs_count() != 1) { return 236 }

    // 跨字左移 1 位: {0, 63, 64} -> {1, 64, 65}
    bs_clear_all()
    bs_set(0)
    bs_set(63)
    bs_set(64)
    bs_shift_left(1)
    if (bs_count() != 3) { return 237 }
    if (bs_test(1) != 1) { return 238 }
    if (bs_test(64) != 1) { return 239 }
    if (bs_test(65) != 1) { return 240 }
    if (bs_test(0) != 0) { return 241 }
    if (bs_test(63) != 0) { return 242 }
    if (bs_test(66) != 0) { return 243 }

    // 左移 63 位: {0, 1} -> {63, 64}
    bs_clear_all()
    bs_set(0)
    bs_set(1)
    bs_shift_left(63)
    if (bs_count() != 2) { return 244 }
    if (bs_test(63) != 1) { return 245 }
    if (bs_test(64) != 1) { return 246 }

    // 最高位左移丢失
    bs_clear_all()
    bs_set(511)
    bs_shift_left(1)
    if (bs_count() != 0) { return 247 }

    // 右移 0 / 64 / 512 / 负数
    bs_clear_all()
    bs_set(128)
    bs_shift_right(0)
    if (bs_test(128) != 1) { return 248 }
    if (bs_count() != 1) { return 249 }
    bs_shift_right(64)
    if (bs_test(64) != 1) { return 250 }
    if (bs_test(128) != 0) { return 251 }
    if (bs_count() != 1) { return 252 }
    bs_shift_right(-1)
    if (bs_test(64) != 1) { return 253 }
    if (bs_count() != 1) { return 254 }
    bs_shift_right(512)
    if (bs_count() != 0) { return 255 }
    bs_set(200)
    bs_shift_right(1000)
    if (bs_count() != 0) { return 256 }
    bs_set(200)
    bs_shift_right(-3)
    if (bs_test(200) != 1) { return 257 }
    if (bs_count() != 1) { return 258 }

    // 跨字右移 1 位: {1, 64, 65} -> {0, 63, 64}
    bs_clear_all()
    bs_set(1)
    bs_set(64)
    bs_set(65)
    bs_shift_right(1)
    if (bs_count() != 3) { return 259 }
    if (bs_test(0) != 1) { return 260 }
    if (bs_test(63) != 1) { return 261 }
    if (bs_test(64) != 1) { return 262 }
    if (bs_test(65) != 0) { return 263 }
    if (bs_test(1) != 0) { return 264 }

    // 右移 63 位: {63, 64} -> {0, 1}
    bs_clear_all()
    bs_set(63)
    bs_set(64)
    bs_shift_right(63)
    if (bs_count() != 2) { return 265 }
    if (bs_test(0) != 1) { return 266 }
    if (bs_test(1) != 1) { return 267 }

    // 最低位右移丢失
    bs_clear_all()
    bs_set(0)
    bs_shift_right(1)
    if (bs_count() != 0) { return 268 }

    // 往返: 左移 100 再右移 100 (无位溢出)
    bs_clear_all()
    bs_from_int(0xDEADBEEF)
    bs_shift_left(100)
    if (bs_last_set() != 131) { return 269 }
    if (bs_count() != 24) { return 270 }
    bs_shift_right(100)
    if (bs_to_int() != 0xDEADBEEF) { return 271 }
    if (bs_count() != 24) { return 272 }

    // 高位移出后不可恢复
    bs_clear_all()
    bs_set(500)
    bs_shift_left(20)
    if (bs_count() != 0) { return 273 }
    bs_shift_right(20)
    if (bs_count() != 0) { return 274 }

    // 右移再左移不是恒等 (bit 0 丢失)
    bs_clear_all()
    bs_set(0)
    bs_shift_right(1)
    bs_shift_left(1)
    if (bs_count() != 0) { return 275 }

    // 位移只作用于当前槽
    bs_reset()
    bs_select(0)
    bs_set(0)
    if (bs_select(1) != 1) { return 276 }
    bs_set(10)
    bs_shift_left(5)
    if (bs_test(15) != 1) { return 277 }
    if (bs_count() != 1) { return 278 }
    if (bs_select(0) != 1) { return 279 }
    if (bs_test(0) != 1) { return 280 }
    if (bs_count() != 1) { return 281 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_bitset_lib(workdir, use_native):
    cpu = _run(workdir, BS_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
