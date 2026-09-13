"""ISA 单一真源守卫 (docs/SUGGESTIONS.md §3.4 / 批次 C)。

codecin/isa.py 是数值真源, 但每个操作码的**语义表**仍散落在多处手写:
  * codecin/stats.py   InstructionProfiler.latency (周期成本)
  * codecin/jit.py     _JIT_OPS (可 JIT 的指令)
  * codecin/isa.py     Constants.ARG_COUNTS (参数个数)
  * codecin/native/engine/isa_gen.go (由生成器产出, 另有 --check)
本测试保证这些表不会漂移 (打错字/漏项/写了不存在的指令名)。
"""

import pytest

from codecin.isa import Constants, Opcode
from codecin.jit import _JIT_OPS
from codecin.stats import InstructionProfiler

VALID = {op.name for op in Opcode}


def test_arg_counts_covers_every_opcode():
    missing = sorted(op.name for op in Opcode if op not in Constants.ARG_COUNTS)
    assert not missing, f'ARG_COUNTS 缺少: {missing}'


def test_arg_counts_has_no_stale_entries():
    stale = sorted(op.name for op in Constants.ARG_COUNTS
                   if op.name not in VALID)
    assert not stale, f'ARG_COUNTS 含不存在的指令: {stale}'


def test_stats_latency_names_are_valid_opcodes():
    bad = sorted(name for name in InstructionProfiler().latency
                 if name not in VALID)
    assert not bad, f'stats.latency 含不存在的指令名: {bad}'


def test_jit_op_names_are_valid_opcodes():
    bad = sorted(name for name in _JIT_OPS if name not in VALID)
    assert not bad, f'jit._JIT_OPS 含不存在的指令名: {bad}'


@pytest.mark.parametrize('opcode', sorted(VALID))
def test_every_opcode_has_arg_count(opcode):
    names = {op.name for op in Constants.ARG_COUNTS}
    assert opcode in names, f'{opcode} 未在 ARG_COUNTS 中声明'
