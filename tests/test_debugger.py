"""调试器: 断点 continue 豁免 (建议 5) 与统一交互会话 (建议 6)。"""

import builtins

from codecin.debugger import ConditionalBreakpoint, DebugServer
from tests.helpers import imm, new_cpu, reg

# pc0/1 为 ADDI, pc2 为 HALT
PROGRAM = [
    ('ADDI', [reg(0), reg(0), imm(1)]),
    ('ADDI', [reg(0), reg(0), imm(2)]),
    ('HALT', []),
]


def _load(**cfg):
    cpu = new_cpu(**cfg)
    cpu.instructions = [tuple(i) for i in PROGRAM]
    cpu.entry_pc = 0
    cpu.pc = 0
    return cpu


def _patch_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(it))


def test_breakpoint_continue_resumes_without_rehit(monkeypatch):
    """建议 5: 断点处 continue 至少执行一条指令, 不立即重入调试器。"""
    _patch_input(monkeypatch, ['c'])
    cpu = _load()
    cpu.add_breakpoint(0)
    entries = []

    orig = cpu.debug_command_loop
    def counting():
        entries.append(1)
        orig()
    cpu.debug_command_loop = counting

    cpu.run()
    assert cpu.regs.read(0) == 3
    assert len(entries) == 1, "continue 后不得再次命中同一断点"


def test_breakpoint_on_later_instruction(monkeypatch):
    _patch_input(monkeypatch, ['c'])
    cpu = _load()
    cpu.add_breakpoint(1)
    cpu.run()
    # 先执行 pc0, 在 pc1 停一次, continue 后执行到 HALT
    assert cpu.regs.read(0) == 3
    assert cpu.pc == 3


def test_step_mode_unified_command_set(monkeypatch):
    """建议 6: --step 使用与断点一致的命令集 (s 单步 / c 继续)。"""
    _patch_input(monkeypatch, ['s', 's', 'c'])
    cpu = _load(step_mode=True)
    cpu.run()
    assert cpu.regs.read(0) == 3


def test_step_mode_quit_is_graceful(monkeypatch):
    """建议 6: quit 不再调用 sys.exit(0)。"""
    _patch_input(monkeypatch, ['s', 'q'])
    cpu = _load(step_mode=True)
    cpu.run()                       # 不应抛 SystemExit
    assert cpu.running is False


def test_conditional_breakpoint_eval(monkeypatch):
    """条件断点: 条件满足才暂停 (X0==1 时命中 pc1)。"""
    _patch_input(monkeypatch, ['c'])
    cpu = _load()
    server = DebugServer(cpu)
    cpu.debug_server = server
    server.conditional_breakpoints.append(ConditionalBreakpoint(address=1, condition='regs[0] == 1'))
    cpu.breakpoints.add(1)
    cpu.run()
    assert cpu.regs.read(0) == 3
