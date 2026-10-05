"""UCBC 字节码反汇编 (A3): .bin -> .asm 风格文本清单。

支持三种输入:
  - CPUSA v3 容器 (.bin, `--compile-only` 产物, v5.9.0 段式格式):
    头 50B + 段表 + UCBC 段
  - CPUSA v2 容器 (遗留): 头 34B + 内存镜像 + UCBC 段
  - 裸 UCBC 段 (magic 'UCBC')

CLI: codecin --disasm prog.bin
"""

import os
import struct
from typing import Any, List, Tuple

from .errors import CPUSimulatorError
from .isa import Constants

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]

#: CPUSA v2 容器头长度 (MAGIC5 + ver1 + mem_size u32 + entry u32 + sp u64
#: + bc_len u32 + 保留 8B)。
_BIN_V2_HEADER = 34

#: CPUSA v3 容器头长度 (与 crom.BIN_V3_HEADER 一致: mem_size u64 / entry u32 /
#: sp u64 / heap_base u64 / bc_len u32 / seg_count u32 / 保留 8B)。
_BIN_V3_HEADER = 50


def fmt_operand(op: tuple) -> str:
    """格式化操作数为汇编文本 (自 debugger.fmt_operand 迁移)。"""
    kind = op[0]
    if kind == 'reg':
        return f"X{op[1]}"
    if kind == 'vec':
        return f"V{op[1]}"
    if kind == 'veclane':
        return f"V{op[1]}.{op[2]}"
    if kind == 'imm':
        return f"#{op[1]}"
    if kind == 'mem':
        base, off = op[1], op[2]
        if base >= 0:
            return f"[X{base}, #{off}]" if off else f"[X{base}]"
        return f"[#{off}]"
    if kind == 'cond':
        return op[1]
    if kind == 'str':
        return f"\"@{op[1]}\""
    return str(op)


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


def _extract_from_bin_v3(data: bytes):
    """解析 CPUSA v3 容器 (段式), 返回 (bytecode, mem_size, entry, sp)。"""
    mem_size = struct.unpack_from('<Q', data, 6)[0]
    entry = struct.unpack_from('<I', data, 14)[0]
    sp = struct.unpack_from('<Q', data, 18)[0]
    bc_len = struct.unpack_from('<I', data, 34)[0]
    seg_count = struct.unpack_from('<I', data, 38)[0]
    # 段表: 每段 addr u64 + len u64 + data
    pos = _BIN_V3_HEADER
    for _ in range(seg_count):
        if pos + 16 > len(data):
            raise CPUSimulatorError("--disasm: 段表不完整 (文件损坏?)")
        seg_len = struct.unpack_from('<Q', data, pos + 8)[0]
        pos += 16 + seg_len
    if pos + bc_len > len(data):
        raise CPUSimulatorError("--disasm: 字节码不完整 (文件损坏?)")
    return data[pos:pos + bc_len], mem_size, entry, sp


def _extract_from_bin_v2(data: bytes):
    """解析 CPUSA v2 容器 (遗留), 返回 (bytecode, mem_size, entry, sp)。"""
    mem_size = struct.unpack_from('<I', data, 6)[0]
    entry = struct.unpack_from('<I', data, 10)[0]
    sp = struct.unpack_from('<Q', data, 14)[0]
    bc_len = struct.unpack_from('<I', data, 22)[0]
    return (data[_BIN_V2_HEADER + mem_size:
                 _BIN_V2_HEADER + mem_size + bc_len],
            mem_size, entry, sp)


def _extract_from_cpusa(data: bytes):
    """按版本分发解析 CPUSA 容器; 非 CPUSA 返回 None。"""
    if data[:5] != Constants.MAGIC_NUMBER:
        return None
    version = data[5]
    if version == Constants.BIN_VERSION:
        return _extract_from_bin_v3(data)
    if version == Constants.BIN_VERSION_V2:
        return _extract_from_bin_v2(data)
    raise CPUSimulatorError(
        f"--disasm: unsupported binary version {version}")


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
            "先用 `codecin src.cin --compile-only -o out.bin` 生成")
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
