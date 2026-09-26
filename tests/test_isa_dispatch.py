"""ISA 完整性: Opcode 枚举、分组计数、dispatch 注册, 与仓库内生成物一致性。

注: ISA 指令表文档 (`docs/ISA.md` / `docs/reference/isa.md`) 随文档站迁至
独立仓库 Code-CIN-Docs, 不在这里校验 (生成脚本 script/gen_isa_docs.py 仍保留)。
"""

import os
import subprocess
import sys

from codecin import Config
from codecin.cpu import CPU
from codecin.isa import Opcode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = range(0, 28)
ARM64 = range(28, 68)
FP = range(68, 78)
VECTOR = range(78, 84)
RISCV = range(84, 111)
SYS = range(111, 112)


def _count(r):
    return sum(1 for m in Opcode if m.value in r)


def test_opcode_total_is_112():
    members = list(Opcode)
    assert len(members) == 112
    values = [m.value for m in members]
    assert len(set(values)) == len(values), "Opcode 数值重复"


def test_group_counts():
    assert _count(BASE) == 28
    assert _count(ARM64) == 40        # 修正: 实际 40 条 (原文档误写 28)
    assert _count(FP) == 10
    assert _count(VECTOR) == 6
    assert _count(RISCV) == 27        # 修正: 实际 27 条 (原文档误写 28)
    assert _count(SYS) == 1


def _make_dispatch():
    return CPU(Config(interactive_mode=False, log_level='ERROR',
                      use_native=False))._dispatch


def test_dispatch_auto_registration_matches_opcode_enum():
    """建议 7: 每个 Opcode 都有处理器且 dispatch 不包含多余指令名。"""
    dispatch = _make_dispatch()
    assert set(Opcode.__members__) == set(dispatch)


def test_wfe_wfi_sev_are_nop_aliases():
    cpu = CPU(Config(interactive_mode=False, log_level='ERROR', use_native=False))
    for name in ('WFE', 'WFI', 'SEV'):
        assert cpu._dispatch[name] == cpu._op_nop


def test_gen_native_isa_up_to_date():
    """codecin/native/isa_gen.go 必须与 isa.py 同步 (建议 10)。"""
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, 'script', 'gen_native_isa.py'),
                        '--check'], capture_output=True, text=True,
                       cwd=ROOT)
    assert r.returncode == 0, f"isa_gen.go 过期: {r.stdout} {r.stderr}"
