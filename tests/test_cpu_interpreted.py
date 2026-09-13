"""解释器指令级黄金测试 (指令元组直接注入, 与汇编器解耦)。"""

import pytest

from codecin.errors import ExecutionError
from tests.helpers import imm, mem, new_cpu, reg, run_program


def test_mov_addi_sub_golden():
    # MOV X0,#6 -> ADDI X0,X0,#36 (42) -> SUB X0,#20 (22)
    cpu = run_program([
        ('MOV', [reg(0), imm(6)]),
        ('ADDI', [reg(0), reg(0), imm(36)]),
        ('SUB', [reg(0), imm(20)]),
        ('HALT', []),
    ])
    assert cpu.regs.read(0) == 22
    assert cpu.pc == 4


def test_xzr_write_ignored():
    cpu = run_program([
        ('MOV', [reg(31), imm(99)]),
        ('HALT', []),
    ])
    assert cpu.regs.read(31) == 0


def test_arithmetic_chain_golden():
    cpu = run_program([
        ('MOV', [reg(0), imm(7)]),
        ('ADDI', [reg(0), reg(0), imm(3)]),     # 10
        ('MUL', [reg(0), imm(10)]),             # 10 * 10 = 100
        ('SUB', [reg(0), imm(50)]),             # 100 - 50 = 50
        ('HALT', []),
    ])
    assert cpu.regs.read(0) == 50


def test_ld_sd_roundtrip():
    cpu = run_program([
        ('MOV', [reg(0), imm(0x200)]),          # X0 = 地址
        ('MOV', [reg(1), imm(0xDEADBEEF)]),     # X1 = 数据
        ('SD', [reg(1), mem(0, 0x200)]),        # mem[0x200] = X1 (8B)
        ('LD', [reg(2), mem(0, 0x200)]),
        ('HALT', []),
    ])
    assert cpu.regs.read(2) == 0xDEADBEEF


def test_stack_push_pop():
    cpu = run_program([
        ('MOV', [reg(0), imm(42)]),
        ('PUSH', [reg(0)]),
        ('MOV', [reg(1), imm(7)]),
        ('POP', [reg(2)]),
        ('HALT', []),
    ])
    assert cpu.regs.read(2) == 42
    # PUSH/POP 配对后 SP 应恢复初值 ((mem_size-8) & ~0x7)
    assert cpu.sp == (cpu.config.mem_size - 8) & ~0x7


def test_vector_add_lane0():
    cpu = new_cpu()
    cpu.instructions = [
        ('VADD', [reg(0), reg(0), reg(1)]),
        ('HALT', []),
    ]
    cpu.vec_regs.write_scalar(0, 1.5)
    cpu.vec_regs.write_scalar(1, 2.25)
    cpu.pc = 0
    cpu.run()
    assert cpu.vec_regs.read_scalar(0) == pytest.approx(3.75)


def test_unimplemented_opcode_raises():
    cpu = new_cpu()
    with pytest.raises(ExecutionError):
        cpu.execute('NOPE', [])


def test_max_instructions_stops():
    cpu = new_cpu(max_instructions=5)
    # 无 HALT 的纯 ADDI 程序
    cpu.instructions = [('ADDI', [reg(0), reg(0), imm(1)])] * 100
    cpu.pc = 0
    cpu.run()
    assert cpu.stats.instruction_count <= 5
