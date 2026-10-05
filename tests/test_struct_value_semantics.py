"""struct 值语义护栏: 整体赋值 / 数组元素赋值 / 嵌套字段赋值 = 值拷贝。

背景 (修复前实测, 三条路径"一致地错"):
  * `P a = {1, 2}; P b = a; b.x = 7; return a.x`        -> 修复前 7, 应为 1
  * `P ps[2] = {{1,2},{3,4}}; ps[1] = ps[0]; ps[1].x = 9; return ps[0].x`
                                                        -> 修复前 9, 应为 1
  * `O o = {{1,2},3}; I k = {5,6}; o.i = k; k.a = 9; return o.i.a`
                                                        -> 修复前 9, 应为 5
  原因: 槽里存的是堆对象指针, 而 `gen_decl` 的 `init` 分支与 `_gen_assign` 都
  只是 `SD` 存指针 => **别名**, 与 `docs/language/structs.md` 第 157 行
  「整体赋值 `P b = a` | 值拷贝 (逐槽复制), 之后互不影响」的承诺相反。

本文件按文档构造护栏, 两个方向都要守住:
  1. 整体赋值 / 数组元素赋值 / 嵌套 struct 字段赋值 == **值拷贝** (互不影响);
  2. 传参 / 数组参数元素 == **引用可见** (文档第 154/161 行的刻意选择,
     函数内 `p.x = 99` 调用方必须可见) —— 不要"顺手"改成值拷贝。
  3. 返回 == 每次调用新对象 (与文档"值拷贝返回"等价的可观测行为)。

断言风格与 `tests/test_aggregate_and_const.py` 一致: CIN 的 `main()` 返回 0
表示通过, 非 0 是失败点编号; 由 pytest 侧比对期望值。
"""

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_source

def _main_only(body: str, prelude: str = '') -> str:
    return f'{prelude}function main() -> int {{ {body} }}'

P2 = 'struct P { int x; int y }\n'
P3 = 'struct P { int a; int b; int c }\n'
IN = 'struct I { int a; int b }\n'
OUT = 'struct O { I i; int c }\n'

# ==========================================================================
# 1. 整体赋值 = 值拷贝
# ==========================================================================

ASSIGN_CASES = [
    # --- 声明即初始化 `P b = a` (gen_decl 的 init 分支) ---
    ('decl_init_copy',
     _main_only('P a = {1, 2}; P b = a; b.x = 7; return a.x', P2), 1),
    ('decl_init_copy_other_field',
     _main_only('P a = {1, 2}; P b = a; b.y = 7; return a.y', P2), 2),
    ('decl_init_copy_sees_value',
     _main_only('P a = {1, 2}; P b = a; return b.x * 10 + b.y', P2), 12),
    ('decl_init_from_partial_literal',
     _main_only('P a = {5}; P b = a; b.b = 9; return a.b', P3), 0),
    ('decl_init_three_slots',
     _main_only('P a = {1, 2, 3}; P b = a; b.c = 9; return a.c', P3), 3),
    # --- 先声明后赋值 `b = a` (_gen_assign) ---
    ('plain_assign_copy',
     _main_only('P a = {1, 2}; P b; b = a; b.x = 7; return a.x', P2), 1),
    ('plain_assign_copy_both_fields',
     _main_only('P a = {1, 2}; P b; b = a; b.x = 8; b.y = 9; '
                'return a.x * 10 + a.y', P2), 12),
    ('plain_assign_gets_value',
     _main_only('P a = {3, 4}; P b; b = a; return b.x * 10 + b.y', P2), 34),
    # --- 目标先有值, 被整体覆盖 ---
    ('assign_overwrites_all_slots',
     _main_only('P a = {1, 2}; P b = {7, 8}; b = a; b.x = 5; '
                'return b.x * 100 + b.y * 10 + a.x', P2), 521),
    # --- 自赋值不得破坏自身 (源与目标同一对象) ---
    ('self_assign_is_noop',
     _main_only('P a = {1, 2}; a = a; return a.x * 10 + a.y', P2), 12),
    ('self_assign_after_field_write',
     _main_only('P a = {1, 2}; a.x = 5; a = a; return a.x * 10 + a.y', P2), 52),
    # --- 链式赋值 (赋值表达式的值必须仍是可用的 struct 指针) ---
    ('chained_assign_both_copied',
     _main_only('P a = {1, 2}; P b; P c; b = c = a; c.x = 8; '
                'return a.x * 100 + b.x * 10 + c.x', P2), 118),
    ('chained_assign_reads_back',
     _main_only('P a = {1, 2}; P b; P c; b = c = a; return b.x * 10 + b.y', P2), 12),
    # --- 全局 <-> 局部 ---
    ('global_to_local_copy',
     _main_only('P b = g; b.x = 7; return g.x', P2 + 'P g = {1, 2}\n'), 1),
    ('local_to_global_copy',
     _main_only('P a = {1, 2}; g = a; g.x = 7; return a.x', P2 + 'P g\n'), 1),
    ('global_to_global_copy',
     _main_only('h = g; h.x = 7; return g.x', P2 + 'P g = {1, 2}\nP h\n'), 1),
    # --- 返回的 struct 赋值给变量: 后续写入不影响另一次调用的结果 ---
    ('returned_value_copied_into_var',
     _main_only('P a = mk(1); P b = mk(2); a.x = 5; return b.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v + 1; return p }\n'),
     2),
    ('returned_value_plain_assign',
     _main_only('P a; P b; a = mk(1); b = mk(2); b.x = 9; '
                'return a.x * 10 + b.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v + 1; return p }\n'),
     19),
    ('returned_values_are_distinct_objects',
     _main_only('P a = mk(3); P b = a; b.x = 4; return a.x * 10 + b.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v; return p }\n'),
     34),
]

@pytest.mark.parametrize('name,src,expected', ASSIGN_CASES,
                         ids=[c[0] for c in ASSIGN_CASES])
def test_struct_whole_assignment_is_value_copy(name, src, expected):
    assert run_cin_source(src).regs.read(0) == expected

# ==========================================================================
# 2. 数组元素赋值 = 值拷贝 (元素槽里存的是各自的堆对象指针)
# ==========================================================================

ARRAY_CASES = [
    ('elem_to_elem_copy',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[1] = ps[0]; ps[1].x = 9; '
                'return ps[0].x', P2), 1),
    ('elem_to_elem_copy_y',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[1] = ps[0]; ps[1].y = 9; '
                'return ps[0].y', P2), 2),
    ('elem_to_elem_sees_value',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[1] = ps[0]; '
                'return ps[1].x * 10 + ps[1].y', P2), 12),
    ('elem_to_local_copy',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; P q; q = ps[0]; q.x = 9; '
                'return ps[0].x', P2), 1),
    ('local_to_elem_copy',
     _main_only('P ps[2]; P q = {1, 2}; ps[1] = q; q.x = 9; return ps[1].x', P2), 1),
    ('elem_to_elem_three_slots',
     _main_only('P ps[2] = {{1, 2, 3}, {4, 5, 6}}; ps[1] = ps[0]; ps[1].c = 9; '
                'return ps[0].c', P3), 3),
    ('elem_self_assign_noop',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[1] = ps[1]; '
                'return ps[1].x * 10 + ps[1].y', P2), 34),
    ('elem_unchanged_after_copy',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[0] = ps[1]; ps[0].x = 9; '
                'return ps[1].x', P2), 3),
    ('all_three_elems_isolated',
     _main_only('P ps[3]; ps[0].x = 1; ps[1] = ps[0]; ps[2] = ps[1]; '
                'ps[2].x = 9; return ps[0].x * 100 + ps[1].x * 10 + ps[2].x', P2),
     119),
    ('global_elem_to_local',
     _main_only('P q; q = gs[0]; q.x = 9; return gs[0].x', P2 + 'P gs[2]\n'), 0),
    ('local_to_global_elem',
     _main_only('P q = {1, 2}; gs[1] = q; q.x = 9; return gs[1].x', P2 +
                'P gs[2]\n'), 1),
    ('global_elem_to_global_elem',
     _main_only('gs[1] = gs[0]; gs[1].x = 9; return gs[0].x', P2 +
                'P gs[2]\n'), 0),
    ('index_expression_target',
     _main_only('P ps[3]; int i = 2; ps[0].x = 1; ps[i] = ps[0]; ps[i].x = 9; '
                'return ps[0].x', P2), 1),
    ('index_expression_source',
     _main_only('P ps[3]; int i = 1; ps[i].x = 5; P q; q = ps[i]; q.x = 9; '
                'return ps[1].x', P2), 5),
    ('copy_into_array_then_compound_write',
     _main_only('P ps[2]; ps[0].x = 3; ps[1] = ps[0]; ps[1].x += 10; '
                'return ps[0].x * 10 + ps[1].x', P2), 43),
]

@pytest.mark.parametrize('name,src,expected', ARRAY_CASES,
                         ids=[c[0] for c in ARRAY_CASES])
def test_struct_array_element_assignment_is_value_copy(name, src, expected):
    assert run_cin_source(src).regs.read(0) == expected

# ==========================================================================
# 3. 嵌套 struct 字段赋值 = 值拷贝 (字段是值内嵌, 字段地址就是对象地址)
# ==========================================================================

NESTED_CASES = [
    ('nested_field_from_local',
     _main_only('O o; I k = {5, 6}; o.i = k; k.a = 9; return o.i.a', IN + OUT), 5),
    ('nested_field_from_local_b',
     _main_only('O o; I k = {5, 6}; o.i = k; k.b = 9; return o.i.b', IN + OUT), 6),
    ('nested_field_to_local',
     _main_only('O o = {{1, 2}, 3}; I k; k = o.i; k.a = 9; return o.i.a',
                IN + OUT), 1),
    ('nested_field_to_nested_field',
     _main_only('O p = {{1, 2}, 3}; O q = {{7, 8}, 9}; p.i = q.i; q.i.a = 4; '
                'return p.i.a', IN + OUT), 7),
    ('nested_field_copy_keeps_sibling',
     _main_only('O o = {{1, 2}, 3}; I k = {5, 6}; o.i = k; return o.c', IN + OUT), 3),
    ('nested_field_repeated_assign',
     _main_only('O o; I k = {1, 1}; o.i = k; k.a = 2; o.i = k; k.a = 3; '
                'return o.i.a', IN + OUT), 2),
    ('nested_field_from_literal_init',
     _main_only('O o = {{1, 2}, 3}; I k = {5, 6}; o.i = k; '
                'return o.i.a * 10 + o.i.b', IN + OUT), 56),
    ('local_from_nested_field_then_write',
     _main_only('O o = {{1, 2}, 3}; I k = o.i; k.a = 9; '
                'return o.i.a * 10 + k.a', IN + OUT), 19),
    # 多层嵌套: 赋值链上每一层都必须是值拷贝
    ('deep_chain_field_assign',
     _main_only('Out a; In k = {5, 9}; a.m.i = k; k.v = 1; return a.m.i.v',
                'struct In { int v; int w }\nstruct Mid { In i; int u }\n'
                'struct Out { Mid m; int z }\n'), 5),
    ('deep_whole_assign_copy',
     _main_only('Out a; Out b; a.m.i.v = 5; a.m.u = 6; a.z = 7; b = a; '
                'b.m.i.v = 9; return a.m.i.v * 100 + a.m.u * 10 + a.z',
                'struct In { int v; int w }\nstruct Mid { In i; int u }\n'
                'struct Out { Mid m; int z }\n'), 567),
    ('deep_mid_field_assign_copy',
     _main_only('Out a; Mid m; m.i.v = 3; m.u = 4; a.m = m; m.i.v = 9; '
                'return a.m.i.v * 10 + a.m.u',
                'struct In { int v; int w }\nstruct Mid { In i; int u }\n'
                'struct Out { Mid m; int z }\n'), 34),
    ('deep_local_from_mid_field',
     _main_only('Out a; a.m.i.v = 4; a.m.u = 5; Mid m = a.m; m.u = 9; '
                'return a.m.u * 10 + m.u',
                'struct In { int v; int w }\nstruct Mid { In i; int u }\n'
                'struct Out { Mid m; int z }\n'), 59),
    # 数组元素里的内嵌 struct 字段
    ('array_elem_nested_field_assign',
     _main_only('Box bs[2]; I k = {7, 8}; bs[1].i = k; k.a = 1; '
                'return bs[1].i.a * 10 + bs[1].i.b', IN +
                'struct Box { I i; int n }\n'), 78),
    ('array_elem_nested_field_to_local',
     _main_only('Box bs[2]; bs[0].i.a = 3; bs[0].i.b = 4; I k = bs[0].i; '
                'k.a = 9; return bs[0].i.a * 10 + k.a', IN +
                'struct Box { I i; int n }\n'), 39),
    ('array_elem_whole_assign_copy',
     # 三层花括号字面量 `{{{5},6},7}` 解析器不支持, 这里用逐字段写入构造
     _main_only('Box bs[2]; bs[0].i.a = 1; bs[0].i.b = 2; bs[0].n = 3; '
                'bs[1].i.a = 4; bs[1].i.b = 5; bs[1].n = 6; bs[1] = bs[0]; '
                'bs[1].i.a = 9; bs[1].n = 8; '
                'return bs[0].i.a * 100 + bs[0].n', IN +
                'struct Box { I i; int n }\n'), 103),
    ('array_elem_whole_assign_copy_target',
     _main_only('Box bs[2]; bs[0].i.a = 1; bs[0].i.b = 2; bs[0].n = 3; '
                'bs[1] = bs[0]; bs[1].i.a = 9; '
                'return bs[1].i.a * 100 + bs[1].i.b * 10 + bs[1].n', IN +
                'struct Box { I i; int n }\n'), 923),
]

@pytest.mark.parametrize('name,src,expected', NESTED_CASES,
                         ids=[c[0] for c in NESTED_CASES])
def test_nested_struct_field_assignment_is_value_copy(name, src, expected):
    assert run_cin_source(src).regs.read(0) == expected

# ==========================================================================
# 4. 反向护栏: 传参 / 数组参数元素 = 引用可见 (文档第 154/161 行)
#    —— 值拷贝只针对"整体赋值"; 传参刻意保持同一块存储, 不要顺手改掉。
# ==========================================================================

REFERENCE_CASES = [
    ('param_write_visible',
     _main_only('P a = {1, 2}; touch(a); return a.x', P2 +
                'function touch(P p) -> void { p.x = 7 }\n'), 7),
    ('param_both_fields_visible',
     _main_only('P a = {1, 2}; touch(a); return a.x * 10 + a.y', P2 +
                'function touch(P p) -> void { p.x = 7; p.y = 8 }\n'), 78),
    ('param_array_element_visible',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; touch(ps[0]); return ps[0].x', P2 +
                'function touch(P p) -> void { p.x = 7 }\n'), 7),
    ('param_nested_field_visible',
     _main_only('O o = {{1, 2}, 3}; touch(o.i); return o.i.a', IN + OUT +
                'function touch(I i) -> void { i.a = 7 }\n'), 7),
    ('param_local_var_elem_visible',
     _main_only('P ps[3]; ps[2].x = 1; touch(ps[2]); return ps[2].x', P2 +
                'function touch(P p) -> void { p.x = 42 }\n'), 42),
    # 整体赋值之后再传参: 拷贝出来的对象同样可被引用修改
    ('copied_var_passed_by_reference',
     _main_only('P a = {1, 2}; P b = a; touch(b); return a.x * 10 + b.x', P2 +
                'function touch(P p) -> void { p.x = 7 }\n'), 17),
]

@pytest.mark.parametrize('name,src,expected', REFERENCE_CASES,
                         ids=[c[0] for c in REFERENCE_CASES])
def test_struct_parameters_stay_reference_visible(name, src, expected):
    """文档把传参定义为"引用可见"(同一块存储): 值拷贝修复不得改变它。"""
    assert run_cin_source(src).regs.read(0) == expected

# ==========================================================================
# 5. 返回值: 每次调用都是新对象 (与文档"值拷贝返回"等价的可观测行为)
# ==========================================================================

RETURN_CASES = [
    ('two_calls_independent',
     _main_only('P a = mk(1); P b = mk(2); a.x = 5; return b.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v + 1; return p }\n'),
     2),
    ('returned_then_copied',
     _main_only('P a = mk(3); P b = a; b.x = 4; return a.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v; return p }\n'), 3),
    ('returned_then_copied_reverse',
     _main_only('P a = mk(3); P b = a; b.x = 4; return b.x', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v; return p }\n'), 4),
    ('returned_value_all_fields',
     _main_only('P q = mk(7); return q.x * 10 + q.y', P2 +
                'function mk(int v) -> P { P p; p.x = v; p.y = v + 1; return p }\n'),
     78),
]

@pytest.mark.parametrize('name,src,expected', RETURN_CASES,
                         ids=[c[0] for c in RETURN_CASES])
def test_struct_return_values_are_fresh_objects(name, src, expected):
    assert run_cin_source(src).regs.read(0) == expected

# ==========================================================================
# 6. 边界检查打开时也必须是值拷贝 (值拷贝路径里的下标求值也要走 bounds-check)
# ==========================================================================

BOUNDS_CASES = [
    ('decl_init',
     _main_only('P a = {1, 2}; P b = a; b.x = 7; return a.x', P2), 1),
    ('array_elem',
     _main_only('P ps[2] = {{1, 2}, {3, 4}}; ps[1] = ps[0]; ps[1].x = 9; '
                'return ps[0].x', P2), 1),
    ('nested_field',
     _main_only('O o; I k = {5, 6}; o.i = k; k.a = 9; return o.i.a', IN + OUT), 5),
]

@pytest.mark.parametrize('name,src,expected', BOUNDS_CASES,
                         ids=[c[0] for c in BOUNDS_CASES])
def test_value_copy_with_bounds_check_enabled(name, src, expected):
    cpu = run_cin_source(src, bounds_check=True)
    assert cpu.regs.read(0) == expected

# ==========================================================================
# 7. 取址形态与错误的干净报错
# ==========================================================================

def test_struct_assign_to_const_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\nconst int N = 1\n'
            'function main() -> int { N = 2; return 0 }')
    assert 'Cannot assign to const' in str(ei.value)

def test_struct_decl_init_from_scalar_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\nfunction main() -> int { P p = 1; return 0 }')
    assert 'Cannot initialize struct' in str(ei.value)

def test_struct_assign_from_scalar_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\n'
            'function main() -> int { P p; p = 5; return 0 }')
    assert 'Cannot assign value of type' in str(ei.value)

def test_struct_assign_from_other_struct_type_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\nstruct Q { int y }\n'
            'function main() -> int { P p; Q q; p = q; return 0 }')
    assert 'different struct types' in str(ei.value)

def test_struct_decl_init_from_other_struct_type_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\nstruct Q { int y }\n'
            'function main() -> int { Q q; P p = q; return 0 }')
    assert 'struct' in str(ei.value) and 'P' in str(ei.value)

def test_copy_assign_into_scalar_array_element_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\n'
            'function main() -> int { int a[2]; P p; a[0] = p; return 0 }')
    assert 'Cannot assign struct value' in str(ei.value)

def test_struct_into_scalar_variable_is_rejected():
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'struct P { int x }\n'
            'function main() -> int { P p; int n; n = p; return 0 }')
    assert 'Cannot assign struct value' in str(ei.value)

def test_empty_struct_value_copy_is_not_silently_wrong():
    """空 struct 的槽数为 0; 整体赋值不得静默走"拷贝 0 槽"的路径。"""
    src = ('struct E {}\nfunction main() -> int { E a; E b; b = a; return 0 }')
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(src)
    assert 'empty struct' in str(ei.value)