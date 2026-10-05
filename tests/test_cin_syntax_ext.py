r"""CIN 语法扩展回归测试 (本次新增的语法特性)。

覆盖点:
  1. enum         —— 默认自增 / 显式赋值后继续自增 / 常量表达式初始化 /
                     枚举类型声明变量 / 成员用作 case 标签 / 各类错误
  2. 范围 for     —— int/float/string/struct 元素数组遍历 / break / continue /
                     嵌套 / 循环变量在体内可读 / 错误用法
  3. for 初始化子句用赋值表达式
  4. switch 增强  —— 闭区间 case / 多值 case / 混用 / 负区间 / 贯穿语义 / 错误
  5. 字符串转义   —— \x / \u / \U / \a\b\f\v\0 的字节语义 / 错误
  6. 多参数 print/println

每条语义用例在 Go 原生引擎 (唯一执行路径) 上跑一遍。

断言约定 (与 tests/test_switch_semantics.py 一致):
  * stdout 断言: cpu._capture_output = True 后读 ''.join(cpu.output_buffer)
  * 返回值断言: cpu.regs.read(0)
"""

import pytest

from codecin import CPU, Config, native
from codecin.cin import CINCompiler
from codecin.errors import CompilerError

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")

def run_cin(src: str):
    """编译并运行 CIN 源码, 返回 (x0, stdout, native_used)。

    注意: CPU.run() 会把执行期异常记日志后吞掉, 因此这里显式检查
    execution_failed, 否则"程序执行失败但 x0 恰好是期望值"会变成假通过。
    """
    res = CINCompiler().compile_source(src)
    cfg = Config(log_level='ERROR')
    cpu = CPU(cfg)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    cpu.run()
    assert not cpu.execution_failed, \
        '程序执行失败 (ExecutionError 被 CPU.run 吞掉)'
    return cpu.regs.read(0), ''.join(cpu.output_buffer), cpu.native_used

# ====================================================================
# 1. enum
# ====================================================================

ENUM_DEFAULT = r'''
enum E { A, B, C }
function main() -> int {
    if (A != 0) { return 1 }
    if (B != 1) { return 2 }
    if (C != 2) { return 3 }
    return 0
}'''

ENUM_EXPLICIT_AND_EXPR = r'''
enum E {
    A,               // 0 (默认自增)
    B = 5,           // 显式赋值
    C,               // 6 (前一个 + 1)
    D = C + 1,       // 7 (引用已定义成员)
    M = 0x10 | 0x01, // 17 (常量表达式)
    NEG = -2,        // -2 (负值)
    NN = ~0,         // -1 (位取反)
    SH = 1 << 5,     // 32
    Q = 9 / 2,       // 4 (向零截断整数除)
    R = 9 % 4        // 1
}
function main() -> int {
    if (A != 0) { return 1 }
    if (B != 5) { return 2 }
    if (C != 6) { return 3 }
    if (D != 7) { return 4 }
    if (M != 17) { return 5 }
    if (NEG != -2) { return 6 }
    if (NN != -1) { return 7 }
    if (SH != 32) { return 8 }
    if (Q != 4) { return 9 }
    if (R != 1) { return 10 }
    E e = C                    // 枚举类型声明等价 int
    if (e != 6) { return 11 }
    if (e + A != C) { return 12 }
    return 0
}'''

ENUM_AS_CASE = r'''
enum E { A, B, C }
function main() -> int {
    int x = C
    int r = 0
    switch (x) {
        case A: r = 10 break
        case B: r = 20 break
        case C: r = 30 break
        default: r = 40
    }
    println("r=", r)
    return 0
}'''

ENUM_IN_GLOBAL = r'''
enum E { A = 3, B }
int g = B                      // 枚举成员可用于全局初始化
function main() -> int {
    int local = A
    if (g != 4) { return 1 }
    if (local != 3) { return 2 }
    return 0
}'''

# ====================================================================
# 2. 范围 for
# ====================================================================

RF_INT_SUM = r'''
function main() -> int {
    int a[4] = {1, 2, 3, 4}
    int sum = 0
    for (int v : a) { sum = sum + v }
    return sum
}'''

RF_FLOAT_SUM = r'''
function main() -> int {
    float f[3] = {1.5, 2.25, 3.25}
    float sum = 0.0
    for (float v : f) { sum = sum + v }
    if (sum != 7.0) { return 1 }
    return 0
}'''

RF_STRING_LEN = r'''
function main() -> int {
    string s[3] = {"a", "bb", "ccc"}
    int n = 0
    for (string w : s) { n = n + strlen(w) }
    return n
}'''

RF_STRUCT_FIELD = r'''
struct P { int x }
function main() -> int {
    P p0
    P p1
    P p2
    p0.x = 4
    p1.x = 5
    p2.x = 6
    P arr[3]
    arr[0] = p0
    arr[1] = p1
    arr[2] = p2
    int sum = 0
    for (P q : arr) { sum = sum + q.x }
    return sum
}'''

RF_BREAK_CONTINUE = r'''
function main() -> int {
    int a[5] = {1, 2, 3, 4, 5}
    int sum = 0
    for (int v : a) {
        if (v == 3) { continue }
        if (v == 5) { break }
        sum = sum + v
    }
    return sum
}'''

RF_NESTED = r'''
function main() -> int {
    int a[3] = {1, 2, 3}
    int b[3] = {10, 20, 30}
    int sum = 0
    for (int x : a) {
        for (int y : b) {
            sum = sum + x * y
        }
    }
    return sum
}'''

RF_SWITCH_CONTINUE = r'''
function main() -> int {
    int a[5] = {1, 2, 3, 4, 5}
    int sum = 0
    for (int v : a) {
        switch (v) {
            case 3: continue
            default: sum = sum + v
        }
    }
    return sum
}'''

RF_GLOBAL_ARRAY = r'''
int g[3] = {2, 4, 6}
function main() -> int {
    int sum = 0
    for (int v : g) { sum = sum + v }
    return sum
}'''

RF_VAR_READABLE = r'''
function main() -> int {
    int a[3] = {7, 8, 9}
    for (int v : a) { println("v=", v) }
    return 0
}'''

# ====================================================================
# 3. for 初始化子句赋值
# ====================================================================

FOR_INIT_ASSIGN = r'''
function main() -> int {
    int i = 0
    int n = 5
    int sum = 0
    for (i = 0; i < n; i = i + 1) { sum = sum + i }
    if (i != 5) { return 1 }
    int n2 = 0
    for (i = 0; i < 3; i += 1) { n2 = n2 + 1 }
    if (i != 3) { return 2 }
    return sum + n2
}'''

# ====================================================================
# 4. switch 增强
# ====================================================================

SW_RANGE_BOUNDARY = r'''
function main() -> int {
    int i = 0
    int hits = 0
    while (i < 8) {
        switch (i) {
            case 3..5: hits = hits + 1 break
            default: break
        }
        i = i + 1
    }
    println("hits=", hits)
    return 0
}'''

SW_MULTI_VALUE = r'''
function main() -> int {
    int i = 0
    int hits = 0
    while (i < 6) {
        switch (i) {
            case 1, 3: hits = hits + 1 break
            case 2, 4: hits = hits + 10 break
            default: break
        }
        i = i + 1
    }
    println("hits=", hits)
    return 0
}'''

SW_MIXED = r'''
function main() -> int {
    int i = 0
    int hits = 0
    while (i < 8) {
        switch (i) {
            case 1, 4..6: hits = hits + 7 break
            default: break
        }
        i = i + 1
    }
    println("hits=", hits)
    return 0
}'''

SW_NEGATIVE_RANGE = r'''
function probe(int x) -> int {
    switch (x) {
        case -5..-3: return 1
        default: return 0
    }
}
function main() -> int {
    int hits = 0
    if (probe(-5) == 1) { hits = hits + 1 }
    if (probe(-4) == 1) { hits = hits + 1 }
    if (probe(-3) == 1) { hits = hits + 1 }
    if (probe(-2) == 0) { hits = hits + 1 }
    if (probe(-6) == 0) { hits = hits + 1 }
    println("hits=", hits)
    return 0
}'''

SW_FALLTHROUGH = r'''
function main() -> int {
    int x = 1
    int r = 0
    switch (x) {
        case 1: r = 1
        case 2: r = r + 10
        default: r = r + 100
    }
    println("r=", r)
    return 0
}'''

# ====================================================================
# 5. 字符串转义
# ====================================================================

ESCAPE_BYTES = r'''
function main() -> int {
    string zh = "\u4e2d\u6587"
    if (strlen(zh) != 6) { return 1 }         // UTF-8 逐字节长度
    string hex = "\xE4\xB8\xAD"
    if (strlen(hex) != 3) { return 2 }
    if (hex[0] != 0xE4) { return 3 }
    if (hex[1] != 0xB8) { return 4 }
    if (hex[2] != 0xAD) { return 5 }
    string u = "\u4e2d"
    if (u[0] != 0xE4) { return 6 }
    if (u[1] != 0xB8) { return 7 }
    if (u[2] != 0xAD) { return 8 }
    if (strcmp(hex, u) != 0) { return 9 }     // \xHH 与 \uHHHH 逐字节一致
    if ("\u4e2d"[0] != 0xE4) { return 10 }    // 字面量直接下标
    if (strcmp("\x41", "A") != 0) { return 11 }
    if (strlen("\x41") != 1) { return 12 }
    return 0
}'''

ESCAPE_CONTROL = r'''
function main() -> int {
    if (strlen("a\0b") != 1) { return 1 }     // NUL 截断
    if ("\a"[0] != 7) { return 2 }
    if ("\b"[0] != 8) { return 3 }
    if ("\f"[0] != 12) { return 4 }
    if ("\v"[0] != 11) { return 5 }
    if ("\t"[0] != 9) { return 6 }
    if ("\r"[0] != 13) { return 7 }
    if ("\n"[0] != 10) { return 8 }
    if ("\\"[0] != 92) { return 9 }
    if ("\""[0] != 34) { return 10 }
    if ("\0"[0] != 0) { return 11 }
    if (strlen("\u0041") != 1) { return 12 }  // ASCII 码点 -> 1 字节
    if ("\u0041"[0] != 0x41) { return 13 }
    return 0
}'''

# ====================================================================
# 6. 多参数 print / println
# ====================================================================

PRINT_MULTI = r'''
function main() -> int {
    print("a", 1, true)          // 无分隔符: a1true
    print("\n")
    print("a")                   // 逐段拼接: 结果必须一致
    print(1)
    print(true)
    print("\n")
    println()                    // 仅一个换行
    print("\n")
    println("solo")
    println("solo")
    println("n=", 7)
    println("n=" + int_to_str(7))
    return 0
}'''

PRINT_MULTI_EXPECTED = "a1true\na1true\n\n\nsolo\nsolo\nn=7\nn=7\n"

# ====================================================================
# 用例表
# ====================================================================

RETURN_CASES = [
    ('enum_default_autoincrement', ENUM_DEFAULT, 0),
    ('enum_explicit_and_expr', ENUM_EXPLICIT_AND_EXPR, 0),
    ('enum_in_global_init', ENUM_IN_GLOBAL, 0),
    ('rangefor_int_sum', RF_INT_SUM, 10),
    ('rangefor_float_sum', RF_FLOAT_SUM, 0),
    ('rangefor_string_len', RF_STRING_LEN, 6),
    ('rangefor_struct_field', RF_STRUCT_FIELD, 15),
    ('rangefor_break_continue', RF_BREAK_CONTINUE, 7),
    ('rangefor_nested', RF_NESTED, 360),
    ('rangefor_switch_continue', RF_SWITCH_CONTINUE, 12),
    ('rangefor_global_array', RF_GLOBAL_ARRAY, 12),
    ('for_init_assign', FOR_INIT_ASSIGN, 13),
    ('escape_hex_and_unicode', ESCAPE_BYTES, 0),
    ('escape_control_chars', ESCAPE_CONTROL, 0),
]

OUT_CASES = [
    ('enum_as_case_label', ENUM_AS_CASE, "r=30\n"),
    ('rangefor_var_readable', RF_VAR_READABLE, "v=7\nv=8\nv=9\n"),
    ('switch_range_boundary', SW_RANGE_BOUNDARY, "hits=3\n"),
    ('switch_multi_value', SW_MULTI_VALUE, "hits=22\n"),
    ('switch_mixed_multi_and_range', SW_MIXED, "hits=28\n"),
    ('switch_negative_range', SW_NEGATIVE_RANGE, "hits=5\n"),
    ('switch_fallthrough', SW_FALLTHROUGH, "r=111\n"),
    ('print_multi_args', PRINT_MULTI, PRINT_MULTI_EXPECTED),
]

ERROR_CASES = [
    # enum
    ('enum_duplicate_member', 'enum E { A, A }', 'duplicate enum member'),
    ('enum_duplicate_across_enums', 'enum E { A }\nenum F { A }',
     'duplicate enum member'),
    ('enum_unknown_name', 'enum E { A = NOPE }', 'unknown name'),
    ('enum_empty', 'enum E { }', 'empty enum'),
    ('enum_div_zero', 'enum E { A = 1 / 0 }', 'divides by zero'),
    ('enum_assign_member',
     'enum E { A, B }\nfunction main() -> int {\n    A = 1\n    return 0\n}',
     'cannot assign to enum member'),
    # 范围 for
    ('rangefor_ptr_array_param', r'''
function f(int[] a) -> int {
    int s = 0
    for (int v : a) { s = s + v }
    return s
}
function main() -> int {
    int a[2] = {1, 2}
    return f(a)
}''', 'fixed-size array'),
    ('rangefor_multidim', r'''
function main() -> int {
    int m[2][3]
    for (int v : m) { }
    return 0
}''', 'multi-dimensional'),
    # switch
    ('switch_empty_range', r'''
function main() -> int {
    int x = 3
    switch (x) {
        case 5..1: return 1
        default: return 0
    }
}''', 'empty case range'),
    ('switch_nonconstant_case', r'''
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case y: return 1
        default: return 0
    }
}''', 'constant'),
    # 字符串转义
    ('escape_u_too_short', 'function main() -> int { string s = "\\u12" return 0 }',
     'hex digits'),
    ('escape_x_without_digits',
     'function main() -> int { string s = "\\x" return 0 }',
     'hex digit'),
    ('escape_U_out_of_range',
     'function main() -> int { string s = "\\U00110000" return 0 }',
     'out of unicode range'),
]

@pytest.mark.parametrize('name,src,expected', RETURN_CASES,
                         ids=[c[0] for c in RETURN_CASES])
def test_syntax_returns(name, src, expected):
    """返回值语义: 原生引擎执行结果必须一致。"""
    rc, out, _native = run_cin(src)
    assert rc == expected, f'{name}: x0={rc} (期望 {expected}), stdout={out!r}'

@pytest.mark.parametrize('name,src,expected_out', OUT_CASES,
                         ids=[c[0] for c in OUT_CASES])
def test_syntax_output(name, src, expected_out):
    """stdout 语义: 原生引擎输出必须逐字节一致。"""
    rc, out, _native = run_cin(src)
    assert rc == 0, f'{name}: x0={rc}, stdout={out!r}'
    assert out == expected_out, f'{name}: stdout={out!r}'

@pytest.mark.parametrize('name,src,needle', ERROR_CASES,
                         ids=[c[0] for c in ERROR_CASES])
def test_syntax_compile_errors(name, src, needle):
    """语法/常量求值错误必须在编译期报 CompilerError。"""
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(src)
    assert needle in str(ei.value).lower(), \
        f'{name}: 错误消息 {str(ei.value)!r} 不含 {needle!r}'

def test_rangefor_array_element_type_is_compile_error():
    r"""元素类型写成 int[]: 必须是编译错误。

    注: _is_range_for() 的前瞻只看 (类型, 变量, ':') 三个 token, 不跳过类型
    后缀 "[]", 因此 `for (int[] v : a)` 不会被识别为范围 for, 而是在按
    C 风格 for 头解析时报 "Expected SEMI but got COLON"; gen_rangefor() 里
    "range-for element type must be a scalar or struct" 分支对这种拼写不可达。
    这里只要求它是编译错误 (docs 的错误用例意图)。
    """
    src = r'''
function main() -> int {
    int a[2] = {1, 2}
    for (int[] v : a) { }
    return 0
}'''
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(src)

@needs_native
def test_native_path_is_really_used():
    """确认执行真的走了 Go 原生 VM。"""
    _rc, _out, native_used = run_cin(SW_RANGE_BOUNDARY)
    assert native_used is True, '原生引擎未生效'
