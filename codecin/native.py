"""Go 原生库桥接 (c-shared)。

通过 ctypes 加载 Go 编译的共享库 (Windows: codecin_native.dll,
Linux/Termux: libcodecin_native.so, macOS: libcodecin_native.dylib),
提供:
  1. 原生字节码 VM (codecin_run_v2) -- 整程序高速执行 (段式内存 ABI)
  2. CROM 压缩/解压 (codecin_crom_pack / codecin_crom_unpack)

v2 ABI: 程序内存默认 1 GiB, 不再整块传输 -- 调用方只传初始内存段,
原生执行后只回传脏段 (64 KiB 页粒度合并)。

字节码格式 (UCBC):
  头部: magic[4]='UCBC' version u8 entry u32 instr_count u32
  指令: opcode u8 argc u8
  操作数: kind u8 value i64 extra i64 (小端)
"""

import ctypes
import os
import platform
import struct
import sys
from typing import Any, Dict, List, Optional, Tuple

from .isa import (
    BC_MAGIC,
    BC_VERSION,
    KIND_COND,
    KIND_FLOAT,
    KIND_IMM,
    KIND_MEM,
    KIND_REG,
    KIND_STR,
    KIND_VEC,
    KIND_VECLANE,
    Cond,
    Constants,
    Opcode,
)

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


def _os_slug() -> str:
    """Release 资产命名里的平台段。"""
    system = platform.system()
    if system == 'Windows':
        return 'windows'
    if system == 'Darwin':
        return 'macos'
    return 'linux'


def _arch_slug() -> str:
    """Release 资产命名里的架构段。"""
    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64'):
        return 'x64'
    if machine in ('aarch64', 'arm64'):
        return 'arm64'
    if machine in ('i386', 'i686', 'x86'):
        return 'x86'
    return machine


def _lib_candidates() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    system = platform.system()
    if system == 'Windows':
        prefix, ext = 'codecin_native', 'dll'
        canonical = ['codecin_native.dll']
    elif system == 'Darwin':
        prefix, ext = 'libcodecin_native', 'dylib'
        canonical = ['libcodecin_native.dylib', 'codecin_native.dylib']
    else:
        prefix, ext = 'libcodecin_native', 'so'
        canonical = ['libcodecin_native.so', 'codecin_native.so']

    # Release 资产带平台/架构后缀 (如 libcodecin_native-linux-arm64.so)。
    # 排在通用名之前: 同一个目录里同时存在两种架构的库时, 必须优先选本机的那个,
    # 否则会拿到"能 dlopen 但跑不了/符号不对"的库。
    specific = [f'{prefix}-{_os_slug()}-{_arch_slug()}.{ext}']
    names = specific + canonical

    candidates = [os.path.join(here, 'native', n) for n in names]
    candidates += [os.path.join(here, n) for n in names]
    env = os.environ.get('CODECIN_NATIVE_LIB')
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
        lib.codecin_run_v2.argtypes = [
            ctypes.c_void_p, ctypes.c_int,       # 请求缓冲 (bc+段+输入), len
            ctypes.c_longlong, ctypes.c_longlong,  # entry, sp
            ctypes.c_longlong, ctypes.c_longlong,  # heapBase, memSize
            ctypes.c_longlong,                   # maxSteps
            ctypes.c_longlong, ctypes.c_longlong,  # seed, flags (bit0=sandbox)
        ]
        lib.codecin_run_v2.restype = ctypes.c_void_p
        lib.codecin_free.argtypes = [ctypes.c_void_p]
        lib.codecin_free.restype = None
        lib.codecin_crom_pack.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.codecin_crom_pack.restype = ctypes.c_void_p
        lib.codecin_crom_unpack.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        lib.codecin_crom_unpack.restype = ctypes.c_void_p
        lib.codecin_version.argtypes = []
        lib.codecin_version.restype = ctypes.c_char_p

        # None (arg_count()/arg(i) 恒为 0/空串), 不影响其余原生能力。
        set_args = getattr(lib, 'codecin_set_args', None)
        if set_args is not None:
            set_args.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]
            set_args.restype = None
        self._set_args = set_args

    def version(self) -> str:
        try:
            return self.lib.codecin_version().decode()
        except Exception:
            return "unknown"

    # ---------------- 原生 VM (v2 段式 ABI) ----------------

    def _build_request_v2(self, bytecode: bytes, segments, input_data: bytes) -> bytes:
        """打包 v2 请求缓冲:
        bc_len u32 | bc | seg_count u32 | 每段 (addr u64 + len u32 + data) |
        in_len u32 | input
        (与 native/main.go codecin_run_v2 的解析严格一致)
        """
        out = bytearray()
        out += struct.pack('<I', len(bytecode))
        out += bytecode
        segs = [(int(a), bytes(d)) for a, d in (segments or []) if d]
        out += struct.pack('<I', len(segs))
        for addr, data in segs:
            out += struct.pack('<QI', addr, len(data))
            out += data
        out += struct.pack('<I', len(input_data))
        out += input_data
        return bytes(out)

    def run_v2(self, bytecode: bytes, segments, entry: int, sp: int,
               heap_base: int, mem_size: int, input_data: bytes = b'',
               max_steps: int = 0, args: Optional[List[str]] = None,
               seed: Optional[int] = None, sandbox: bool = False
               ) -> Optional[Dict[str, Any]]:
        req = self._build_request_v2(bytecode, segments, input_data)
        req_buf = ctypes.create_string_buffer(req, len(req))

        # 命令行参数注入 (arg_count/arg 的数据源)。每次运行都注入 (空列表
        # 即清空), 避免同一进程内多次运行时上一次的参数残留。
        if self._set_args is not None:
            enc = [a.encode('utf-8') for a in (args or [])]
            arr = (ctypes.c_char_p * len(enc))(*enc)
            self._set_args(arr, len(enc))

        flags = 1 if sandbox else 0
        ptr = self.lib.codecin_run_v2(
            ctypes.cast(req_buf, ctypes.c_void_p), len(req),
            entry, sp, heap_base, mem_size,
            max_steps,
            int(seed) if seed else 0,
            flags,
        )
        if not ptr:
            return None
        try:
            return self._parse_result_v2(ptr)
        finally:
            self.lib.codecin_free(ptr)

    def _parse_result_v2(self, ptr: int) -> Dict[str, Any]:
        base = ptr
        head = ctypes.string_at(base, 36)
        status = head[0]
        flg = head[1]
        pc, sp, heap_ptr, steps = struct.unpack_from('<QQQQ', head, 4)
        off = 36
        regs_raw = ctypes.string_at(base + off, 33 * 8)
        off += 33 * 8
        vec_raw = ctypes.string_at(base + off, 32 * 4 * 8)
        off += 32 * 4 * 8
        seg_count = struct.unpack_from(
            '<Q', ctypes.string_at(base + off, 8), 0)[0]
        off += 8
        segments: List[Tuple[int, bytes]] = []
        for _ in range(seg_count):
            hdr = ctypes.string_at(base + off, 16)
            seg_addr, seg_len = struct.unpack('<QQ', hdr)
            off += 16
            data = bytes(ctypes.string_at(base + off, seg_len)) if seg_len else b''
            off += seg_len
            segments.append((seg_addr, data))
        out_len = struct.unpack_from('<Q', ctypes.string_at(base + off, 8), 0)[0]
        off += 8
        out_data = bytes(ctypes.string_at(base + off, out_len)) if out_len else b''
        off += out_len
        err_len = struct.unpack_from('<H', ctypes.string_at(base + off, 2), 0)[0]
        off += 2
        err_msg = ctypes.string_at(base + off, err_len).decode(
            'utf-8', 'replace') if err_len else ''

        regs = list(struct.unpack('<33Q', regs_raw))
        vec_flat = struct.unpack(f'<{32 * 4}d', vec_raw)
        vec_regs = [list(vec_flat[i * 4:(i + 1) * 4]) for i in range(32)]

        flags = {'N': bool(flg & 1), 'Z': bool(flg & 2),
                 'C': bool(flg & 4), 'V': bool(flg & 8)}

        error = None
        if status == 2:
            error = err_msg or 'unsupported instruction'
        elif status == 3:
            error = err_msg or 'runtime error'

        return {
            'status': status,
            'flags': flags,
            'pc': pc,
            'sp': sp,
            'heap_ptr': heap_ptr,
            'steps': steps,
            'regs': regs,
            'vec_regs': vec_regs,
            'segments': segments,
            'output': out_data.decode('utf-8', 'replace'),
            'error': error,
        }

    # ---------------- CROM ----------------

    def crom_pack(self, mem: bytes, compress: bool) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(bytes(mem), len(mem))
        out_len = ctypes.c_int(0)
        ptr = self.lib.codecin_crom_pack(
            ctypes.cast(buf, ctypes.c_void_p), len(mem),
            1 if compress else 0, ctypes.byref(out_len))
        if not ptr:
            return None
        try:
            return bytes(ctypes.string_at(ptr, out_len.value))
        finally:
            self.lib.codecin_free(ptr)

    def crom_unpack(self, data: bytes) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(data, len(data))
        mem_len = ctypes.c_int(0)
        flags = ctypes.c_int(0)
        ptr = self.lib.codecin_crom_unpack(
            ctypes.cast(buf, ctypes.c_void_p), len(data),
            ctypes.byref(mem_len), ctypes.byref(flags))
        if not ptr:
            return None
        try:
            return bytes(ctypes.string_at(ptr, mem_len.value))
        finally:
            self.lib.codecin_free(ptr)


def get_engine(logger=None) -> Optional[NativeEngine]:
    """查找并加载原生库; 失败返回 None (纯 Python 回退)。"""
    global _LIB_CACHE
    if _LIB_CACHE is not False:
        return _LIB_CACHE

    engine = None
    failures = []
    for path in _lib_candidates():
        if not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            engine = NativeEngine(lib)
            if logger:
                logger.debug(f"Loaded native library: {path} ({engine.version()})")
            break
        except (OSError, AttributeError) as e:
            # OSError: 架构不符 / 依赖缺失 / 不是动态库
            # AttributeError: 能加载但缺导出符号或 ABI 版本不符 (例如旁边的旧库)
            # 两种情况都应继续尝试下一个候选, 而不是让整个运行炸掉。
            failures.append((path, e))
            if logger:
                logger.debug(f"Failed to load native library {path}: {e}")
            engine = None

    if engine is None and failures and logger:
        path, err = failures[-1]
        logger.warning(f"原生库不可用 (v5.9.0 起无解释器回退, 请重建原生库): "
                       f"{path} ({err})")

    _LIB_CACHE = engine
    return engine
