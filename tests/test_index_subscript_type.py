"""数组/字符串下标必须是 int 上下文 (issue #1)。

CIN 规定 `/` 恒为浮点除法 (整数除法用 `idiv`), 因此 `a[(lo + hi) / 2]`
这类常见写法的下标表达式类型是 float。下标与赋值/传参/返回一样是 int
上下文, 编译器必须把 float 隐式截断为 int。

修复前 `_gen_index` 直接把这个 float 的 IEEE-754 位模式当成字节偏移乘 8,
于是任何 float 下标都会算出 0x0379_8000_0000_0000 一类的高位垃圾地址,
运行时抛 "address ... out of bounds" —— 标准快速排序因此无法运行。
"""

import pytest

from codecin import native as native_mod
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(native_mod.get_engine() is None,
                                  reason="native library not built")

# issue #1 的最小复现: 用 `/` 取枢轴下标的 Hoare 快排。
QUICKSORT_SRC = """
int G_C[500]

function qrec(int lo, int hi) -> void {
    if (lo >= hi) { return }
    int p = G_C[(lo + hi) / 2]
    int i = lo
    int j = hi
    while (i <= j) {
        while (i <= j && G_C[i] < p) { i = i + 1 }
        while (i <= j && G_C[j] > p) { j = j - 1 }
        if (i <= j) {
            int t = G_C[i]
            G_C[i] = G_C[j]
            G_C[j] = t
            i = i + 1
            j = j - 1
        }
    }
    qrec(lo, j)
    qrec(i, hi)
}

function main() -> int {
    for (int i = 0; i < 500; i++) {
        G_C[i] = (i * 7919 + 13) % 10007
    }
    qrec(0, 499)
    for (int i = 0; i < 499; i++) {
        if (G_C[i] > G_C[i + 1]) { return 1000000000 + i }  // 未排序: 失败哨兵
    }
    return G_C[0] * 10007 + G_C[499]
}
"""


def _data():
    return sorted((i * 7919 + 13) % 10007 for i in range(500))


def _expected():
    d = _data()
    return d[0] * 10007 + d[-1]


def test_quicksort_with_float_subscript_interpreter():
    """issue #1: 浮点下标取枢轴的快排必须正常排序, 不再越界。"""
    assert run_cin_source(QUICKSORT_SRC, use_native=False).regs.read(0) == _expected()


def test_quicksort_with_float_subscript_jit():
    assert run_cin_source(QUICKSORT_SRC, use_native=False,
                          enable_jit=True).regs.read(0) == _expected()


@needs_native
def test_quicksort_with_float_subscript_native():
    assert run_cin_source(QUICKSORT_SRC, use_native=True).regs.read(0) == _expected()


def test_float_subscript_truncates_toward_zero():
    """float 下标按截断 (向零取整) 转 int, 与 `int x = 1.9` 一致。"""
    src = """
function main() -> int {
    int a[4]
    a[0] = 10
    a[1] = 20
    a[2] = 30
    a[3] = 40
    float f = 1.9
    int r = a[f]                // -> a[1] = 20 (左值之外的读取路径)
    float g = 3.0
    a[g - 0.5] = 99             // -> a[2] = 99 (左值地址路径)
    string s = "ABC"
    int ch = s[1.9]             // -> s[1] = 'B' = 66 (字符串下标)
    return r + a[2] + ch        // 20 + 99 + 66 = 185
}"""
    assert run_cin_source(src, use_native=False).regs.read(0) == 185


def test_float_subscript_negative_truncates_toward_zero():
    """-0.5 截断为 0 (不是 -1): 截断语义必须发生在边界检查之前。"""
    src = """
function main() -> int {
    int a[4]
    a[0] = 7
    float f = -0.5
    return a[f]
}"""
    assert run_cin_source(src, use_native=False).regs.read(0) == 7


def test_float_subscript_out_of_range_trips_bounds_check(capsys):
    """float 下标先转 int 再查边界: 9.5 -> 9 >= 4 必须中止而非越界写。"""
    src = "function main() -> int { int a[4]\nfloat f = 9.5\na[f] = 1\nreturn 0 }\n"
    run_cin_source(src, bounds_check=True)
    assert 'length' in capsys.readouterr().out


def test_int_subscript_unchanged():
    """int 下标的代码生成不受影响 (idiv 是标准库惯用写法)。"""
    src = """
function main() -> int {
    int a[5]
    for (int i = 0; i < 5; i++) { a[i] = i * i }
    int m = idiv(0 + 4, 2)
    return a[m]
}"""
    assert run_cin_source(src, use_native=False).regs.read(0) == 4


def test_float_subscript_does_not_corrupt_memory():
    """修复前该程序会抛出越界地址 (垃圾高位 0x0379...), 现在必须正常结束。"""
    src = """
function main() -> int {
    int a[8]
    a[3] = 42
    return a[2 + 1.0]
}"""
    cpu = run_cin_source(src, use_native=False)
    assert cpu.regs.read(0) == 42
    assert cpu.execution_failed is False
