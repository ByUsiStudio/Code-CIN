"""UCBC 字节码反汇编 (A3): .bin -> .asm 风格文本清单。

支持两种输入:
  - CPUSA 容器 (.bin, `--compile-only` 产物): 头 + 内存镜像 + UCBC 段
  - 裸 UCBC 段 (magic 'UCBC')

CLI: python cpu.py --disasm prog.bin
"""

import os
import struct
from typing import Any, List, Tuple

from .debugger import fmt_operand
from .errors import CPUSimulatorError
from .isa import Constants

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]


def _format_instruction(index: int, opcode: str, args: List[Operand]) -> str:
    # 条件后缀形式: cond 操作数作为指令 mnemonic.cond 前缀
    if args and args[0][0] == 'cond':
        cond = args[0][1]
        rest = " ".join(fmt_operand(op) for op in args[1:])
        return f"{index:04x}: {opcode}.{cond} {rest}".rstrip()
    parts = [f"{index:04x}:", opcode]
    parts.extend(fmt_operand(op) for op in args)
    return " ".join(parts)


def _decode_ucbc_segment(bytecode: bytes) -> Tuple[List[Instruction], int]:
    from .native import decode_program
    return decode_program(bytecode)


def _extract_from_cpusa(data: bytes):
    """解析 CPUSA 容器, 返回 (bytecode, mem_size, entry, sp)。"""
    if data[:5] != Constants.MAGIC_NUMBER:
        return None
    version = data[5]
    if version != Constants.BIN_VERSION:
        raise CPUSimulatorError(
            f"--disasm: unsupported binary version {version}")
    mem_size = struct.unpack('<I', data[6:10])[0]
    entry = struct.unpack('<I', data[10:14])[0]
    sp = struct.unpack('<Q', data[14:22])[0]
    bc_len = struct.unpack('<I', data[22:26])[0]
    off = 34
    return data[off + mem_size: off + mem_size + bc_len], mem_size, entry, sp


def disassemble_bytes(data: bytes) -> List[str]:
    # CPUSA 容器
    cpusa = _extract_from_cpusa(data)
    if cpusa is not None:
        bytecode, mem_size, entry, sp = cpusa
        instructions, _ = _decode_ucbc_segment(bytecode)
        out = [f"; CPUSA binary: {len(instructions)} instructions, "
               f"mem={mem_size} bytes, entry=0x{entry:x}, sp=0x{sp:x}", ";"]
        for idx, (opcode, args) in enumerate(instructions):
            out.append(_format_instruction(idx, opcode, args))
        out.append("")
        return out

    # 裸 UCBC
    if data[:4] != b'UCBC':
        raise CPUSimulatorError(
            "--disasm 需要 .bin (CPUSA 容器) 或 UCBC 字节码; "
            "先用 `python cpu.py src.cin --compile-only -o out.bin` 生成")
    instructions, entry = _decode_ucbc_segment(data)
    out = [f"; UCBC disassembly: {len(instructions)} instructions, "
           f"entry=0x{entry:x}", ";"]
    for idx, (opcode, args) in enumerate(instructions):
        out.append(_format_instruction(idx, opcode, args))
    out.append("")
    return out


def disassemble_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise CPUSimulatorError(f"File '{path}' not found")
    with open(path, 'rb') as f:
        data = f.read()
    return disassemble_bytes(data)
