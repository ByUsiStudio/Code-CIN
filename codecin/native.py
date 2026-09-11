"""Go 原生库桥接 (c-shared)。

通过 ctypes 加载 Go 编译的共享库 (Windows: ucpu_native.dll,
Linux/Termux: libucpu_native.so, macOS: libucpu_native.dylib),
提供:
  1. 原生字节码 VM (ucpu_run)  -- 整程序高速执行
  2. CROM 压缩/解压 (ucpu_crom_pack / ucpu_crom_unpack)

库不存在或加载失败时所有接口返回 None, 调用方自动回退纯 Python。

字节码格式 (UCBC):
  头部: magic[4]='UCBC' version u8 entry u32 instr_count u32
  指令: opcode u8 argc u8
  操作数: kind u8 value i64 extra i64 (小端)
"""

import ctypes
import math
import os
import platform
import struct
import sys
from typing import Any, Dict, List, Optional, Tuple

from .isa import (BC_MAGIC, BC_VERSION, KIND_COND, KIND_FLOAT, KIND_IMM,
                  KIND_MEM, KIND_REG, KIND_STR, KIND_VEC, KIND_VECLANE,
                  Cond, Constants, Opcode)

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]

# ==================== 字节码编解码 ====================

_KIND_MAP = {
    'reg': KIND_REG, 'imm': KIND_IMM, 'vec': KIND_VEC,
    'veclane': KIND_VECLANE, 'mem': KIND_MEM, 'cond': KIND_COND,
    'float': KIND_FLOAT, 'str': KIND_STR,
}


def encode_operand(op: Operand) -> Tuple[int, int, int]:
    kind = _KIND_MAP.get(op[0])
    if kind is None:
        if op[0] == 'label':
            return KIND_IMM, 0, 0  # 未解析标签 (不应出现)
        raise ValueError(f"Cannot encode operand: {op}")
    if kind == KIND_REG:
        return kind, int(op[1]), 0
    if kind == KIND_IMM:
        return kind, int(op[1]) & 0xFFFFFFFFFFFFFFFF, 0
    if kind == KIND_VEC:
        return kind, int(op[1]), 0
    if kind == KIND_VECLANE:
        return kind, int(op[1]), int(op[2])
    if kind == KIND_MEM:
        base = op[1] if len(op) > 1 else -1
        off = op[2] if len(op) > 2 else 0
        return kind, int(base), int(off) & 0xFFFFFFFFFFFFFFFF
    if kind == KIND_COND:
        return kind, Cond.code(op[1]), 0
    if kind == KIND_FLOAT:
        return kind, struct.unpack('<Q', struct.pack('<d', float(op[1])))[0], 0
    if kind == KIND_STR:
        return kind, int(op[1]), 0
    raise ValueError(f"Cannot encode operand: {op}")


def _i64(v: int) -> int:
    v &= 0xFFFFFFFFFFFFFFFF
    return v if v < (1 << 63) else v - (1 << 64)


def decode_operand(kind: int, value: int, extra: int) -> Operand:
    if kind == KIND_REG:
        return ('reg', value)
    if kind == KIND_IMM:
        return ('imm', value)
    if kind == KIND_VEC:
        return ('vec', value)
    if kind == KIND_VECLANE:
        return ('veclane', value, extra)
    if kind == KIND_MEM:
        return ('mem', value, extra)
    if kind == KIND_COND:
        return ('cond', Cond.NAMES[value & 15])
    if kind == KIND_FLOAT:
        return ('float', struct.unpack('<d', struct.pack('<q', _i64(value)))[0])
    if kind == KIND_STR:
        return ('str', value)
    raise ValueError(f"Unknown operand kind: {kind}")


def encode_program(instructions: List[Instruction], entry: int = 0,
                   labels: Optional[Dict[str, int]] = None) -> bytes:
    """编码程序为字节码。labels 用于将 ('label', name) 操作数解析为立即数。"""
    out = bytearray()
    out += BC_MAGIC
    out += struct.pack('<B', BC_VERSION)
    out += struct.pack('<I', entry & 0xFFFFFFFF)
    out += struct.pack('<I', len(instructions) & 0xFFFFFFFF)
    labels = labels or {}
    for opcode, args in instructions:
        op_enum = Constants.OPCODE_NAME_TO_ENUM.get(opcode)
        if op_enum is None:
            raise ValueError(f"Unknown opcode: {opcode}")
        out += struct.pack('<BB', op_enum.value, len(args))
        for arg in args:
            if arg[0] == 'label':
                if arg[1] not in labels:
                    raise ValueError(f"Undefined label: {arg[1]}")
                arg = ('imm', labels[arg[1]])
            kind, value, extra = encode_operand(arg)
            out += struct.pack('<Bqq', kind, _i64(value), _i64(extra))
    return bytes(out)


def decode_program(data: bytes) -> Tuple[List[Instruction], int]:
    if data[:4] != BC_MAGIC:
        raise ValueError("Bad bytecode magic")
    version = data[4]
    if version != BC_VERSION:
        raise ValueError(f"Unsupported bytecode version: {version}")
    entry = struct.unpack('<I', data[5:9])[0]
    count = struct.unpack('<I', data[9:13])[0]
    pos = 13
    instructions: List[Instruction] = []
    for _ in range(count):
        op_val, argc = struct.unpack('<BB', data[pos:pos + 2])
        pos += 2
        opcode = Opcode(op_val).name
        args: List[Operand] = []
        for _ in range(argc):
            kind, value, extra = struct.unpack('<Bqq', data[pos:pos + 17])
            pos += 17
            args.append(decode_operand(kind, value, extra))
        instructions.append((opcode, args))
    return instructions, entry


# ==================== 原生库加载 ====================

_LIB_CACHE: Optional['NativeEngine'] = False  # type: ignore


def _lib_candidates() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    system = platform.system()
    if system == 'Windows':
        names = ['ucpu_native.dll']
    elif system == 'Darwin':
        names = ['libucpu_native.dylib', 'ucpu_native.dylib']
    else:
        names = ['libucpu_native.so', 'ucpu_native.so']
    candidates = [os.path.join(here, 'native', n) for n in names]
    candidates += [os.path.join(here, n) for n in names]
    env = os.environ.get('UCPU_NATIVE_LIB')
    if env:
        candidates.insert(0, env)
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包布局: 二进制位于 exe 目录或 _internal 下
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        meipass = getattr(sys, '_MEIPASS', exe_dir)
        candidates += [os.path.join(meipass, n) for n in names]
        candidates += [os.path.join(exe_dir, n) for n in names]
    return candidates


class NativeEngine:
    def __init__(self, lib: ctypes.CDLL):
        self.lib = lib
        self._configure()

    def _configure(self) -> None:
        lib = self.lib
        lib.ucpu_run.argtypes = [
            ctypes.c_void_p, ctypes.c_int,       # bytecode, len
            ctypes.c_void_p, ctypes.c_int,       # mem, len
            ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong,  # entry, sp, heap
            ctypes.c_void_p, ctypes.c_int,       # input, len
            ctypes.c_longlong,                   # max steps
        ]
        lib.ucpu_run.restype = ctypes.c_void_p
        lib.ucpu_free.argtypes = [ctypes.c_void_p]
        lib.ucpu_free.restype = None
        lib.ucpu_crom_pack.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.ucpu_crom_pack.restype = ctypes.c_void_p
        lib.ucpu_crom_unpack.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        lib.ucpu_crom_unpack.restype = ctypes.c_void_p
        lib.ucpu_version.argtypes = []
        lib.ucpu_version.restype = ctypes.c_char_p

    def version(self) -> str:
        try:
            return self.lib.ucpu_version().decode()
        except Exception:
            return "unknown"

    # ---------------- 原生 VM ----------------

    def run(self, bytecode: bytes, mem: bytes, entry: int, sp: int,
            heap_base: int, input_data: bytes, max_steps: int) -> Optional[Dict[str, Any]]:
        bc_buf = ctypes.create_string_buffer(bytecode)
        mem_buf = ctypes.create_string_buffer(bytes(mem), len(mem))
        in_buf = ctypes.create_string_buffer(input_data) if input_data else None

        ptr = self.lib.ucpu_run(
            ctypes.cast(bc_buf, ctypes.c_void_p), len(bytecode),
            ctypes.cast(mem_buf, ctypes.c_void_p), len(mem),
            entry, sp, heap_base,
            ctypes.cast(in_buf, ctypes.c_void_p) if in_buf else None,
            len(input_data),
            max_steps,
        )
        if not ptr:
            return None
        try:
            return self._parse_result(ptr)
        finally:
            self.lib.ucpu_free(ptr)

    def _parse_result(self, ptr: int) -> Dict[str, Any]:
        base = ptr
        view = ctypes.string_at(base, 64)
        status = view[0]
        pc, sp, heap_ptr, steps = struct.unpack_from('<QQQQ', view, 4)
        off = 4 + 32
        regs_raw = ctypes.string_at(base + off, 33 * 8)
        off += 33 * 8
        vec_raw = ctypes.string_at(base + off, 32 * 4 * 8)
        off += 32 * 4 * 8
        mem_len = struct.unpack_from('<Q', ctypes.string_at(base + off, 8), 0)[0]
        off += 8
        mem_data = bytes(ctypes.string_at(base + off, mem_len)) if mem_len else b''
        off += mem_len
        out_len = struct.unpack_from('<Q', ctypes.string_at(base + off, 8), 0)[0]
        off += 8
        out_data = bytes(ctypes.string_at(base + off, out_len)) if out_len else b''
        off += out_len
        err_len = struct.unpack_from('<H', ctypes.string_at(base + off, 2), 0)[0]
        off += 2
        err_msg = ctypes.string_at(base + off, err_len).decode('utf-8', 'replace') if err_len else ''

        regs = list(struct.unpack('<33Q', regs_raw))
        vec_flat = struct.unpack(f'<{32 * 4}d', vec_raw)
        vec_regs = [list(vec_flat[i * 4:(i + 1) * 4]) for i in range(32)]

        error = None
        if status == 2:
            error = 'unsupported'
        elif status == 3:
            error = err_msg or 'runtime error'

        return {
            'status': status,
            'halted': status == 0,
            'pc': pc,
            'sp': sp,
            'heap_ptr': heap_ptr,
            'steps': steps,
            'regs': regs,
            'vec_regs': vec_regs,
            'mem': mem_data,
            'output': out_data.decode('utf-8', 'replace'),
            'error': error,
        }

    # ---------------- CROM ----------------

    def crom_pack(self, mem: bytes, compress: bool) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(bytes(mem), len(mem))
        out_len = ctypes.c_int(0)
        ptr = self.lib.ucpu_crom_pack(
            ctypes.cast(buf, ctypes.c_void_p), len(mem),
            1 if compress else 0, ctypes.byref(out_len))
        if not ptr:
            return None
        try:
            return bytes(ctypes.string_at(ptr, out_len.value))
        finally:
            self.lib.ucpu_free(ptr)

    def crom_unpack(self, data: bytes) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(data, len(data))
        mem_len = ctypes.c_int(0)
        flags = ctypes.c_int(0)
        ptr = self.lib.ucpu_crom_unpack(
            ctypes.cast(buf, ctypes.c_void_p), len(data),
            ctypes.byref(mem_len), ctypes.byref(flags))
        if not ptr:
            return None
        try:
            return bytes(ctypes.string_at(ptr, mem_len.value))
        finally:
            self.lib.ucpu_free(ptr)


def get_engine(logger=None) -> Optional[NativeEngine]:
    """查找并加载原生库; 失败返回 None (纯 Python 回退)。"""
    global _LIB_CACHE
    if _LIB_CACHE is not False:
        return _LIB_CACHE

    engine = None
    for path in _lib_candidates():
        if not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            engine = NativeEngine(lib)
            if logger:
                logger.debug(f"Loaded native library: {path} ({engine.version()})")
            break
        except OSError as e:
            if logger:
                logger.debug(f"Failed to load native library {path}: {e}")
            engine = None

    _LIB_CACHE = engine
    return engine
