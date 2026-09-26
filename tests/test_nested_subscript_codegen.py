"""嵌套下标/调用的地址计算 (临时值必须溢出到栈)。

编译器没有寄存器分配器, 约定是: **任何需要跨另一个子表达式存活的临时值
都必须存栈**, 因为子表达式 (嵌套下标、函数调用、min/max、复合赋值…) 会
随意覆盖 x1..x6。

`_gen_index` 曾经把数组/字符串基址"暂存"在 x3, 于是内层下标把 x3 改成自己
的基址, 外层 ADD 就用错了基址 —— `A[B[i]]` 被算成 `&B[0]`: 读取静默错值,
赋值静默写坏别的内存 (不报错、不越界)。同一类缺陷还有:

  * `_gen_assign`: 待写入的值存在 x2, 之后才求左值地址 (A[f(i)] = v);
  * `gen_init_2d_literal`: 行指针数组 (x4) 与当前行 (x5) 跨元素表达式存活。

这些用例覆盖整类缺陷, 且在解释器 / JIT / 原生三条路径上都必须一致。
"""

import pytest

from codecin import native as native_mod
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(native_mod.get_engine() is None,
                                  reason="native library not built")

#: (名称, CIN 源码, main 返回值期望)
CASES = [
    ('nested_index_read', """
int A[8]
int B[8]
function main() -> int {
    for (int i = 0; i < 8; i++) { B[i] = 7 - i }
    A[0] = 100
    A[7] = 700
    return A[B[7]]
}""", 100),

    ('nested_index_write', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 3
    A[3] = 0
    A[B[0]] = 77
    return A[3]
}""", 77),

    ('call_in_lvalue_index', """
int A[8]
function idx(int n) -> int { int t = n * 2
return t }
function main() -> int {
    A[6] = 0
    A[idx(3)] = 42
    return A[6]
}""", 42),

    ('call_on_rhs', """
int A[8]
function val(int n) -> int { int t = n + 1
return t }
function main() -> int {
    A[2] = val(41)
    return A[2]
}""", 42),

    ('nested_string_index', """
int B[4]
function main() -> int {
    string s = "ABC"
    B[0] = 1
    return s[B[0]]
}""", 66),

    ('three_level_nested', """
int A[8]
int B[8]
int C[8]
function main() -> int {
    C[0] = 2
    B[2] = 5
    A[5] = 91
    return A[B[C[0]]]
}""", 91),

    ('nested_index_in_2d_row', """
int B[4]
function main() -> int {
    int m[3][4]
    B[0] = 2
    m[2][3] = 55
    return m[B[0]][3]
}""", 55),

    ('nested_index_as_inner_of_2d', """
int B[4]
function main() -> int {
    int m[3][4]
    B[0] = 3
    m[1][3] = 66
    return m[1][B[0]]
}""", 66),

    ('nested_on_rhs_of_assign', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 5
    A[5] = 66
    A[1] = A[B[0]]
    return A[1]
}""", 66),

    ('compound_through_nested_index', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 4
    A[4] = 10
    A[B[0]] += 5
    return A[4]
}""", 15),

    ('struct_inline_array_nested', """
int B[4]
struct P { int arr[4] }
function main() -> int {
    P p
    B[0] = 2
    p.arr[2] = 33
    return p.arr[B[0]]
}""", 33),

    ('ptrarray_2d_literal_with_calls', """
function g(int n) -> int { int t = n * 10
return t }
function main() -> int {
    int[][] m = {{g(1), g(2)}, {g(3), g(4)}}
    return m[0][0] * 1000 + m[0][1] * 100 + m[1][0] * 10 + m[1][1]
}""", 12340),

    ('ptrarray_row_with_nested_index', """
int B[4]
function main() -> int {
    int[][] m = {{1, 2, 3}, {4, 5, 6}}
    B[0] = 1
    return m[B[0]][2]
}""", 6),

    ('float_nested_index', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 6
    A[3] = 44
    return A[B[0] / 2]
}""", 44),

    ('nested_index_in_condition', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 3
    A[3] = 7
    if (A[B[0]] == 7) { return 1 }
    return 2
}""", 1),

    ('nested_index_through_builtin', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 3
    A[3] = 10
    return idiv(A[B[0]], 2)
}""", 5),

    ('nested_index_through_strlen', """
int A[8]
int B[8]
function main() -> int {
    B[0] = 3
    A[3] = 12345
    return strlen(int_to_str(A[B[0]]))
}""", 5),
]

NAMES = [c[0] for c in CASES]


@pytest.mark.parametrize('name,src,want', CASES, ids=NAMES)
def test_nested_subscript_interpreter(name, src, want):
    assert run_cin_source(src, use_native=False).regs.read(0) == want


@pytest.mark.parametrize('name,src,want', CASES, ids=NAMES)
def test_nested_subscript_jit(name, src, want):
    cpu = run_cin_source(src, use_native=False, enable_jit=True)
    assert cpu.regs.read(0) == want


@needs_native
@pytest.mark.parametrize('name,src,want', CASES, ids=NAMES)
def test_nested_subscript_native(name, src, want):
    assert run_cin_source(src, use_native=True).regs.read(0) == want


def test_nested_write_does_not_touch_neighbour_array():
    """写 A[B[i]] 不能落到 B 的内存上 (修复前 A/B 基址被混淆)。"""
    src = """
int A[4]
int B[4]
function main() -> int {
    B[0] = 1
    B[1] = 9
    A[1] = 5
    A[B[0]] = 100
    return B[1] * 1000 + A[1]
}"""
    # B[1] 必须仍是 9, A[1] 必须是 100
    assert run_cin_source(src, use_native=False).regs.read(0) == 9100
