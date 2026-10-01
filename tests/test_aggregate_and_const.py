"""第三轮新功能与聚合初始化: struct 花括号初始化 / const / string += / exit()。

这些用例同时是**回归护栏**: 修复前的实际行为记录在每条用例的注释里。

背景 (修复前实测, 三条路径"一致地错"):
  * `P p = {3, 4}` 把字面量写进"槽"(该槽存的是堆对象指针), 第 2 个 3/4 会写到
    `FP + 0`, 直接覆盖**保存的帧指针** —— 静默内存破坏;
  * `P o = {3, 4}` (全局) 覆盖全局槽里的对象指针, 读出来是地址垃圾;
  * `P ps[3]` 的成员访问把"值内嵌"的元素槽当指针解引用, 于是 `ps[0].y = 1`
    写到**绝对地址 8**, 静默破坏数据段 (字符串字面量就住在那里);
  * `P p = {1, 2, 3}` 字段不足时: 解释器越界写(返回 65536), 原生 VM 抛
    ExecutionError —— 同一程序两条路径结论不同;
  * `struct Out { In i; int c }` 的 `c` 被排在 `i.b` 上 (嵌套 struct 字段只按 1 槽推进);
  * `{{5, 6}, 7}` 里尾部的 `7` 被解析器**静默丢弃**;
  * 局部 `const int N = 4` 抛裸 `KeyError: 'const'` (未处理的 Python 异常)。
"""

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_source

PATHS = (False, True)
PATH_IDS = ('interp', 'native')


def expr_type_kind(err: str) -> str:
    return err.splitlines()[0]


# --------------------------------------------------------------------------
# const 命名常量
# --------------------------------------------------------------------------

CONST_CASES = [
    ('global_int', 'const int N = 4\nfunction main() -> int { return N }', 4),
    ('local_int', 'function main() -> int { const int N = 4; return N }', 4),
    ('constant_expression',
     'const int A = 2\nconst int B = A * 3 + 1\nfunction main() -> int { return B }',
     7),
    ('global_array_size',
     'const int N = 5\nint a[N]\nfunction main() -> int { a[4] = 9; return a[4] }', 9),
    ('local_array_size',
     'function main() -> int { const int N = 3; int a[N]; a[2] = 7; return a[2] }', 7),
    ('array_size_expression',
     'function main() -> int { int a[2 + 3]; a[4] = 6; return a[4] }', 6),
    ('array_size_from_enum',
     'enum E { K = 2 }\nconst int N = K + 3\n'
     'function main() -> int { int a[N]; a[4] = 8; return a[4] }', 8),
    ('float_const',
     'const float PI = 3.5\nfunction main() -> int { return PI * 2 }', 7),
    ('bool_const',
     'const bool YES = true\nfunction main() -> int { if (YES) { return 1 } return 0 }', 1),
    ('string_const',
     'const string NAME = "cin"\nfunction main() -> int { return strlen(NAME) }', 3),
    ('used_in_expression',
     'const int N = 10\nfunction main() -> int { return N * 2 + N }', 30),
    ('shadows_nothing',
     'const int N = 1\nfunction main() -> int { int N = 5; return N }', 5),
    ('global_name_collides_with_const',
     'const int N = 1\nfunction main() -> int { return N }', 1),
]


@pytest.mark.parametrize('name,src,expected', CONST_CASES,
                         ids=[c[0] for c in CONST_CASES])
@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_const_declarations(name, src, expected, use_native):
    assert run_cin_source(src, use_native=use_native).regs.read(0) == expected


@pytest.mark.parametrize('bad,needle', [
    ('function main() -> int { const int N = 1 + ; return 0 }', 'error'),
    ('function main() -> int { const int N = 0; N = 2; return 0 }',
     'Cannot assign to const'),
    ('const int A = 1\nconst int A = 2\nfunction main() -> int { return 0 }',
     'Duplicate const'),
    ('function main() -> int { int x = 3; int a[x]; return 0 }',
     "non-constant name 'x'"),
    ('function main() -> int { int a[2 - 5]; return 0 }',
     'array size must be non-negative'),
    ('const float F = 1.5\nfunction main() -> int { int a[F]; return 0 }',
     'array size must be an integer constant'),
])
def test_const_and_dim_errors_are_clean(bad, needle):
    """常量/维度错误必须是 CompilerError, 不能是裸 Python 异常。"""
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(bad)
    assert needle in str(ei.value)


def test_const_division_by_zero_is_reported():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'const int N = 4 / 0\nfunction main() -> int { return N }')
    assert 'divides by zero' in str(ei.value)


# --------------------------------------------------------------------------
# struct 聚合初始化 (回归护栏)
# --------------------------------------------------------------------------

STRUCT_CASES = [
    # 修复前: 返回 0, 并覆盖保存的帧指针
    ('local_literal',
     'struct P { int x; int y }\n'
     'function main() -> int { P p = {3, 4}; return p.x * 10 + p.y }', 34),
    # 修复前: 未列出的字段不是保证的 0 (依赖堆恰好干净)
    ('partial_literal',
     'struct P { int x; int y; int z }\n'
     'function main() -> int { P p = {5}; return p.x * 100 + p.z }', 500),
    # 修复前: 返回 43980465111040 (对象指针被字面量覆盖)
    ('global_literal',
     'struct P { int x; int y }\nP o = {3, 4}\n'
     'function main() -> int { return o.x * 10 + o.y }', 34),
    # 修复前: 返回 560 (c 被排在 i.b 上)
    ('nested_literal',
     'struct I { int a; int b }\nstruct O { I i; int c }\n'
     'function main() -> int { O o = {{5, 6}, 7}; '
     'return o.i.a * 100 + o.i.b * 10 + o.c }', 567),
    ('local_array_literal',
     'struct P { int x; int y }\n'
     'function main() -> int { P ps[2] = {{1, 2}, {3, 4}}; '
     'return ps[1].x * 10 + ps[1].y }', 34),
    # 修复前: 返回 6473924464345088
    ('global_array_literal',
     'struct P { int x; int y }\nP ps[2] = {{1, 2}, {3, 4}}\n'
     'function main() -> int { return ps[1].x * 10 + ps[1].y }', 34),
    # 修复前: ps[0].y 被 ps[1].x 别名覆盖, 且成员访问写到绝对地址 0/8
    ('array_element_isolation',
     'struct P { int x; int y }\n'
     'function main() -> int { P ps[3]; ps[0].y = 111; ps[1].x = 555; '
     'return ps[0].y }', 111),
    ('array_element_stride',
     'struct P { int a; int b; int c }\n'
     'function main() -> int { P ps[3]; ps[0].b = 7; ps[1].a = 10; '
     'return ps[0].b }', 7),
    # 成员访问不得破坏数据段里的字符串字面量
    ('array_access_keeps_literals',
     'struct P { int x; int y }\n'
     'function main() -> int { P ps[2]; ps[1].x = 5; '
     'string s = "hello"; return strlen(s) * 10 + ps[1].x }', 55),
    ('default_zero',
     'struct P { int x; float f; bool b; string s }\n'
     'function main() -> int { P p; if (p.x != 0) { return 1 } '
     'if (p.b) { return 2 } if (strlen(p.s) != 0) { return 3 } return 0 }', 0),
    ('string_field',
     'struct P { string s; int n }\n'
     'function main() -> int { P p = {"ab", 3}; return strlen(p.s) * 10 + p.n }', 23),
    ('float_field',
     'struct P { float f; int n }\n'
     'function main() -> int { P p = {2.5, 4}; return p.f * 2 + p.n }', 9),
    ('expressions_as_initializers',
     'struct P { int x; int y }\n'
     'function main() -> int { int k = 4; P p = {k + 1, k * 2}; '
     'return p.x * 10 + p.y }', 58),
    ('nested_array_literal_trailing',
     'struct I { int a; int b }\nstruct O { I i; int c }\n'
     'function main() -> int { O o = {{1, 2}, 3}; '
     'return o.i.a * 100 + o.i.b * 10 + o.c }', 123),
]


@pytest.mark.parametrize('name,src,expected', STRUCT_CASES,
                         ids=[c[0] for c in STRUCT_CASES])
@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_struct_aggregate_initialization(name, src, expected, use_native):
    assert run_cin_source(src, use_native=use_native).regs.read(0) == expected


@pytest.mark.parametrize('name,src,expected', STRUCT_CASES,
                         ids=[c[0] for c in STRUCT_CASES])
def test_struct_aggregate_initialization_jit(name, src, expected):
    """第三条路径: JIT 必须给出同样结果。"""
    cpu = run_cin_source(src, use_native=False, enable_jit=True)
    assert cpu.regs.read(0) == expected


@pytest.mark.parametrize('bad,needle', [
    ('struct P { int x }\nfunction main() -> int { P p = {1, 2, 3}; return 0 }',
     'Too many initializers for struct P'),
    ('struct P { int x }\nP g = {1, 2}\nfunction main() -> int { return 0 }',
     'Too many initializers for struct P'),
    ('struct P { int x }\nfunction main() -> int { P ps[1] = {{1}, {2}}; return 0 }',
     'Too many initializers for struct array'),
    ('struct I { int a }\nstruct O { I i; int c }\nfunction main() -> int { return 0 }',
     None),                      # 这一条合法: 仅确认嵌套 struct 定义可用
])
def test_struct_initializer_errors(bad, needle):
    if needle is None:
        CINCompiler().compile_source(bad)
        return
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(bad)
    assert needle in str(ei.value)


def test_struct_array_field_is_rejected_not_silently_wrong():
    """`struct Bag { int[] items }` 这类字段必须在编译期拒绝, 不能静默算错。"""
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\nstruct Bag { P items[3] }\n'
            'function main() -> int { return 0 }')
    assert 'struct array field' in str(ei.value)


def test_nested_struct_field_type_must_be_defined_first():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct O { I i }\nstruct I { int a }\n'
            'function main() -> int { return 0 }')
    assert 'unknown type' in str(ei.value)


# --------------------------------------------------------------------------
# string += 与 exit()
# --------------------------------------------------------------------------

MISC_CASES = [
    ('string_plus_equals',
     'function main() -> int { string s = "a"; s += "b"; return strlen(s) }', 2),
    ('string_plus_equals_number',
     'function main() -> int { string s = "n="; s += 42; return strlen(s) }', 4),
    ('string_plus_equals_chain',
     'function main() -> int { string s = ""; s += "ab"; s += "cd"; '
     'return strlen(s) }', 4),
    ('string_plus_equals_content',
     'function main() -> int { string s = "ab"; s += "cd"; '
     'if (strcmp(s, "abcd") == 0) { return 1 } return 0 }', 1),
    ('exit_returns_code',
     'function main() -> int { exit(3); return 0 }', 3),
    ('exit_stops_execution',
     'function main() -> int { int x = 1; exit(0); x = 2; return x }', 0),
    ('exit_from_function',
     'function stop(int c) -> void { exit(c) }\n'
     'function main() -> int { stop(7); return 0 }', 7),
]


@pytest.mark.parametrize('name,src,expected', MISC_CASES,
                         ids=[c[0] for c in MISC_CASES])
@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_string_compound_assign_and_exit(name, src, expected, use_native):
    assert run_cin_source(src, use_native=use_native).regs.read(0) == expected


@pytest.mark.parametrize('op', ['-=', '*=', '/='])
def test_string_other_compound_ops_rejected(op):
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            f'function main() -> int {{ string s = "a"; s {op} "b"; return 0 }}')
    assert 'string' in str(ei.value)


def test_exit_argument_count_checked():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'function main() -> int { exit(); return 0 }')
    assert 'exit() expects exactly 1 argument' in str(ei.value)
