"""三执行路径一致性: 解释器 / JIT / Go 原生必须产生相同终态。"""

import pytest

from codecin import native as native_mod

from tests.helpers import new_cpu, snapshot

needs_native = pytest.mark.skipif(native_mod.get_engine() is None,
                                  reason="native library not built")

# 全部指令均在三条路径 (解释/JIT/原生) 支持范围内
PROGRAM = [
    ('MOV', [('reg', 0), ('imm', 100)]),
    ('ADDI', [('reg', 0), ('reg', 0), ('imm', 2)]),     # 102
    ('ADDI', [('reg', 0), ('reg', 0), ('imm', 3)]),     # 105
    ('MOV', [('reg', 1), ('reg', 0)]),                  # 105
    ('NOP', []),
    ('PUSH', [('reg', 1)]),
    ('MOV', [('reg', 1), ('imm', 7)]),
    ('POP', [('reg', 2)]),                              # 105
    ('HALT', []),
]


def _load(program, **cfg):
    cpu = new_cpu(**cfg)
    cpu.instructions = [tuple(op) for op in program]
    cpu.entry_pc = 0
    cpu.pc = 0
    return cpu


def test_interpreter_matches_jit():
    a = _load(PROGRAM)
    a.run()
    b = _load(PROGRAM, enable_jit=True)
    b.run()
    assert snapshot(a) == snapshot(b)
    assert b.jit is not None and b.jit.compilation_count >= 1


def test_interpreter_matches_native_or_fallback():
    a = _load(PROGRAM)
    a.run()
    b = _load(PROGRAM, use_native=True)
    b.run()
    sa, sb = snapshot(a), snapshot(b)
    # 内存快照比对 (原生/解释均将栈位恢复初值; 此处只比确定性状态)
    for key in ('pc', 'sp', 'heap', 'regs', 'vec', 'flags'):
        assert sa[key] == sb[key], f"state differs on {key}"
    # 原生库可用时必须真正走原生; 缺失时静默回退解释
    if native_mod.get_engine() is None:
        assert b.native_used is False
    else:
        assert b.native_used is True


@needs_native
def test_native_run_does_not_record_per_instruction():
    """原生路径必须批量写入统计, 不得按指令数在 Python 里逐条记账。

    旧实现在 _apply_native_state 里对每条指令调用一次
    Statistics.record_instruction('?'), 实测让原生路径慢数十倍
    (620k 指令: 记账循环本身就要 0.8s, 而整个原生执行只需 ~14ms),
    且算出的 opcode 直方图随即被 clear 丢弃。
    """
    cpu = _load(PROGRAM, use_native=True)
    calls = []
    original = cpu.stats.record_instruction
    cpu.stats.record_instruction = lambda op: (calls.append(op), original(op))
    cpu.run()
    assert cpu.native_used is True
    assert calls == [], f"原生路径逐条记账了 {len(calls)} 次"


@needs_native
def test_native_run_bulk_stats_are_consistent():
    """批量记账后统计口径仍与逐条记账一致。"""
    steps = len(PROGRAM)
    a = _load(PROGRAM, use_native=True)
    a.run()
    assert a.stats.instruction_count == steps
    assert a.stats.opcode_count == {}                 # 原生路径无逐条 opcode 明细
    assert a.stats.hot_instructions['?'] == steps     # 与旧实现累计值一致
    assert a.stats.performance_counters.counters['instructions'] == steps

