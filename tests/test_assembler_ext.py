"""汇编器语法增强: .equ 常量、立即数/偏移表达式、符号算术、数据段表达式。"""

import pytest

from codecin.assembler import Assembler
from codecin.errors import AssemblerError
from codecin.memory import FastMemory
from tests.helpers import asm_program

ASM_EQU = """
.equ N, 5
.equ BASE, 100
.equ OFF, 2*4+1
.text
main:
    addi x0, x0, BASE
    addi x0, x0, N
    addi x0, x0, OFF
    addi x0, x0, 0x10
    addi x0, x0, 0b1
    addi x0, x0, (N*2)
    mov x1, buf
    mov x2, 77
    sd x2, [x1, OFF]
    ld x5, [x1, OFF]
    halt
.data
buf: .dq 0
"""


def _assemble(source: str):
    asm = Assembler(FastMemory(4096))
    return asm.assemble_source(source)


def test_equ_in_instruction_operands():
    instructions, labels, _ = _assemble(ASM_EQU)
    assert labels['main'] == 0
    # addi rd, rs, imm -> 立即数按顺序为 100, 5, 9, 0x10, 1, 10
    imms = [ins[1][2][1] for ins in instructions[:6]]
    assert imms == [100, 5, 9, 16, 1, 10]


def test_equ_with_comma_separator():
    instructions, _, _ = _assemble(".equ SIZE, 8\naddi x0, x0, SIZE\nhalt\n")
    assert instructions[0][1][2][1] == 8


def test_symbol_arithmetic_operand():
    instructions, _, _ = _assemble(
        ".equ A, 10\n.equ B, 2\naddi x0, x0, A*B+B+1\nhalt\n")
    assert instructions[0][1][2][1] == 23


def test_forward_equ_reference_is_error():
    with pytest.raises(AssemblerError):
        _assemble(".equ X, Y+1\naddi x0, x0, X\nhalt\n")


def test_undefined_equ_symbol_is_error():
    with pytest.raises(AssemblerError):
        _assemble("addi x0, x0, UNDEF\nhalt\n")


def test_data_directive_expression():
    asm = Assembler(FastMemory(4096))
    instructions, labels, dlabels = asm.assemble_source(
        ".equ K, 10\n.data\narr: .dq K, K*2+1, 0x20\n")
    # data labels 地址从 0 开始, 每槽 8 字节
    assert dlabels['arr'] == 0
    assert asm.memory.read_qword(0) == 10
    assert asm.memory.read_qword(8) == 21
    assert asm.memory.read_qword(16) == 32


def test_asm_end_to_end_interpreter_and_native(workdir):
    cpu = asm_program(ASM_EQU, workdir)
    cpu.run()
    assert cpu.regs.read(0) == 141
    assert cpu.regs.read(5) == 77
    cpu2 = asm_program(ASM_EQU, workdir, name='prog2.asm', use_native=True)
    cpu2.run()
    assert cpu2.regs.read(0) == 141
    assert cpu2.regs.read(5) == 77


def test_label_plus_offset_operand(workdir):
    src = """.text
main:
    mov x0, target + 1
    halt
target:
    nop
"""
    cpu = asm_program(src, workdir)
    cpu.run()
    # target 位于 index 2 (main 两指令后)
    assert cpu.regs.read(0) == 3


def test_equ_must_be_dot_prefixed():
    # PL 关键字 'set' 是 MOV, 不能当作 .equ 误解析
    instructions, _, _ = _assemble("set x0, 5\nhalt\n")
    assert instructions[0][0] == 'MOV'


# --- 立即数后缀与进制前缀的交互 (回归: #0x1F 曾被解析成 1) ---

@pytest.mark.parametrize('literal,expected', [
    ('#0x1F', 31),            # 尾字母 F 是十六进制数字, 不是类型后缀
    ('#0x1FF', 511),
    ('#0x0F', 15),
    ('#0xABCDEF', 11259375),
    ('#0x1F0', 496),          # 尾字符非 F, 旧实现本来就正确
    ('#0xFF', 255),
    ('#0xF', 15),
    ('#0xFFu', 255),          # 真正的 u 后缀仍要剥离
    ('#0x1FL', 31),           # L 不是十六进制数字, 可安全剥离
    ('#0xABCDEFu', 11259375),
    ('-0x1F', -31),
    ('#16f', 16),             # 十进制: f 是类型后缀
    ('#0b1111', 15),
    ('#0o17', 15),
    ('#0', 0),
])
def test_parse_immediate_radix_and_suffix(literal, expected):
    assert Assembler.parse_immediate(literal) == expected


def test_hex_immediate_keeps_trailing_f_in_operand():
    """指令操作数里的 #0x1F 必须按 31 编码 (曾因剥后缀变成 1)。"""
    src = """.text
main:
    mov x0, 0x1F
    halt
"""
    instructions, _, _ = _assemble(src)
    assert instructions[0] == ('MOV', [('reg', 0), ('imm', 31)])
