"""CROM 内存镜像与 CPUSA 二进制程序格式 (v5.9.0 段式内存)。

v5.9.0 起程序内存默认 1 GiB, 不再整块存储:
- CROM v4 / BIN v3: 只保存已分配 (非零) 的内存段;
- 旧格式 (CROM v3 / BIN v2, 整块镜像) 仍可读取, 不再写出。

CROM 优先调用 Go 原生库 (codecin.native) 进行压缩/解压, 无原生库时回退 zlib。
"""

import struct
import zlib
from typing import TYPE_CHECKING, List, Optional, Tuple

from .errors import CPUSimulatorError
from .isa import Constants

if TYPE_CHECKING:
    from .cpu import CPU
    from .logger import Logger
    from .memory import FastMemory

CROM_HEADER_SIZE = 16
# flags bit0: zlib 压缩 (v3/v4 通用)
CROM_FLAG_COMPRESS = 0x01
CROM_FLAG_MMU = 0x02  # 仅 v3 遗留格式: 尾部携带 MMU 页表元数据 (读取时忽略)
# v3 遗留格式解压余量 (MMU 尾部元数据)
CROM_MAX_TRAILER = 4 << 20

Seg = Tuple[int, bytes]


def _pack_segments(segs: List[Seg]) -> bytes:
    """段表编码: 每段 addr u64 + len u64 + data (顺序拼接)。"""
    out = bytearray()
    for addr, data in segs:
        out += struct.pack('<QQ', addr, len(data))
        out += data
    return bytes(out)


def _unpack_segments(data: bytes, seg_count: int) -> List[Seg]:
    segs: List[Seg] = []
    pos = 0
    for _ in range(seg_count):
        if pos + 16 > len(data):
            raise CPUSimulatorError("段表不完整 (文件损坏?)")
        addr, seg_len = struct.unpack_from('<QQ', data, pos)
        pos += 16
        if pos + seg_len > len(data):
            raise CPUSimulatorError("段数据不完整 (文件损坏?)")
        segs.append((addr, bytes(data[pos:pos + seg_len])))
        pos += seg_len
    return segs


# ==================== CROM 内存镜像 ====================

def save_crom(memory: 'FastMemory', path: str, compress: bool = True,
              logger: Optional['Logger'] = None) -> None:
    """CROM v4: 段式内存镜像 (只存已分配页)。"""
    segs = memory.snapshot_segments()
    body = _pack_segments(segs)
    flags = CROM_FLAG_COMPRESS if compress else 0x00
    if compress:
        body = zlib.compress(body, level=6)
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    # 头部 16B: MAGIC 4B | ver u8 | flags u8 | seg_count u32 | checksum u32 | 保留 2B
    header = (Constants.CROM_MAGIC
              + struct.pack('<B', Constants.CROM_VERSION)
              + struct.pack('<B', flags)
              + struct.pack('<I', len(segs))
              + struct.pack('<I', checksum)
              + b'\x00\x00')
    packed = header + body
    with open(path, 'wb') as f:
        f.write(packed)
    if logger:
        logger.info(f".crom saved to {path} ({len(packed)} bytes, "
                    f"segments={len(segs)}, compressed={compress})")


def load_crom(memory: 'FastMemory', path: str,
              logger: Optional['Logger'] = None) -> None:
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 8:
        raise CPUSimulatorError(f".crom file too short: {len(data)} bytes")

    if data[:4] != Constants.CROM_MAGIC:
        # 旧版裸格式: 前 4 字节为 mem_size。
        # 要求声明长度与文件实际内容一致, 否则任意文件 (例如 "NOTACROMFILE")
        # 都会被静默当成内存镜像载入。
        mem_size = struct.unpack('<I', data[:4])[0]
        if mem_size > len(data) - 4:
            raise CPUSimulatorError(
                f"Not a .crom file: 声明的 mem_size={mem_size} "
                f"超过文件实际内容 ({len(data) - 4} bytes)")
        memory.load_bytes(0, data[4:4 + mem_size])
        if logger:
            logger.info(f"Loaded legacy .crom: {min(mem_size, len(data) - 4)} bytes")
        return

    version = data[4]
    if version == Constants.CROM_VERSION:
        _load_crom_v4(memory, data, logger)
        return
    if version == Constants.CROM_VERSION_V3:
        _load_crom_v3(memory, data, logger)
        return
    raise CPUSimulatorError(f"Unsupported .crom version: {version}")


def _load_crom_v4(memory: 'FastMemory', data: bytes,
                  logger: Optional['Logger']) -> None:
    """v4: flags u8 + 保留 3B | seg_count u32 | checksum u32 | 段表。"""
    if len(data) < CROM_HEADER_SIZE:
        raise CPUSimulatorError(".crom header incomplete")
    flags = data[5]
    compressed = bool(flags & CROM_FLAG_COMPRESS)
    seg_count = struct.unpack_from('<I', data, 6)[0]
    checksum = struct.unpack_from('<I', data, 10)[0]
    body = data[CROM_HEADER_SIZE:]
    if zlib.crc32(body) & 0xFFFFFFFF != checksum:
        raise CPUSimulatorError(".crom checksum mismatch (file corrupted?)")
    if compressed:
        try:
            body = zlib.decompress(body)
        except zlib.error as e:
            raise CPUSimulatorError(
                f"Failed to decompress .crom: {e}") from e
    segs = _unpack_segments(body, seg_count)
    memory.load_segments(segs)
    if logger:
        logger.info(f"Loaded .crom v4: {len(segs)} segments, "
                    f"compressed={compressed}")


def _load_crom_v3(memory: 'FastMemory', data: bytes,
                  logger: Optional['Logger']) -> None:
    """v3 遗留格式: 整块内存镜像 (+ 可选 MMU 尾部, 忽略)。"""
    if len(data) < CROM_HEADER_SIZE:
        raise CPUSimulatorError(".crom header incomplete")
    mem_size = struct.unpack_from('<I', data, 5)[0]
    flags = data[9]
    compressed = bool(flags & CROM_FLAG_COMPRESS)
    checksum = struct.unpack_from('<I', data, 10)[0]
    body = data[CROM_HEADER_SIZE:]
    if zlib.crc32(body) & 0xFFFFFFFF != checksum:
        raise CPUSimulatorError(".crom checksum mismatch (file corrupted?)")
    if compressed:
        limit = mem_size + CROM_MAX_TRAILER
        try:
            d = zlib.decompressobj()
            raw = d.decompress(body, limit)
            if d.unconsumed_tail:
                raise CPUSimulatorError(
                    f".crom 解压超过头部声明的大小 ({limit} bytes): 疑似损坏或恶意文件")
            raw += d.flush()
        except zlib.error as e:
            raise CPUSimulatorError(
                f"Failed to decompress .crom: {e}") from e
        if len(raw) > limit:
            raise CPUSimulatorError(
                f".crom 解压超过头部声明的大小 ({limit} bytes)")
    else:
        raw = body
        if len(raw) > mem_size + CROM_MAX_TRAILER:
            raise CPUSimulatorError(
                f".crom 载荷超过头部声明的大小 ({mem_size} bytes)")
    memory.load_bytes(0, raw[:min(mem_size, len(raw))])
    if flags & CROM_FLAG_MMU and logger:
        logger.info(".crom v3 含 MMU 页表 (v5.9.0 起已移除 MMU), 忽略")
    if logger:
        logger.info(f"Loaded legacy .crom v3: {min(mem_size, len(raw))} bytes, "
                    f"compressed={compressed}")


# ==================== CPUSA 二进制程序 ====================
# v3 (当前): MAGIC 5B + ver u8 + mem_size u64 + entry u32 + sp u64 +
#            heap_base u64 + bc_len u32 + seg_count u32 + 保留 8B +
#            段表 (addr u64 + len u64 + data) + bytecode
# v2 (遗留): MAGIC 5B + ver u8 + mem_size u32 + entry u32 + sp u64 +
#            bc_len u32 + 保留 8B + 整块内存镜像 + bytecode

BIN_V3_HEADER = 5 + 1 + 8 + 4 + 8 + 8 + 4 + 4 + 8  # 50


def save_bin(cpu: 'CPU', path: str, logger: Optional['Logger'] = None) -> None:
    from .native import encode_program
    all_labels = dict(cpu.labels)
    all_labels.update(cpu.data_labels)
    bytecode = encode_program(cpu.instructions, cpu.entry_pc, all_labels)
    segs = cpu.memory.snapshot_segments()

    with open(path, 'wb') as f:
        f.write(Constants.MAGIC_NUMBER)                       # 5B
        f.write(struct.pack('<B', Constants.BIN_VERSION))     # 1B
        f.write(struct.pack('<Q', len(cpu.memory)))           # 8B
        f.write(struct.pack('<I', cpu.entry_pc))              # 4B
        f.write(struct.pack('<Q', cpu.sp))                    # 8B
        f.write(struct.pack('<Q', cpu.heap_base))             # 8B
        f.write(struct.pack('<I', len(bytecode)))             # 4B
        f.write(struct.pack('<I', len(segs)))                 # 4B
        f.write(b'\x00' * 8)                                  # 保留
        f.write(_pack_segments(segs))
        f.write(bytecode)
    if logger:
        logger.info(f"Binary saved to {path} ({len(bytecode)} bytecode bytes, "
                    f"{len(segs)} segments)")


def load_bin(cpu: 'CPU', path: str) -> None:
    from .native import decode_program
    with open(path, 'rb') as f:
        data = f.read()

    if data[:5] != Constants.MAGIC_NUMBER:
        raise CPUSimulatorError("Invalid binary file (bad magic)")
    version = data[5]
    if version == Constants.BIN_VERSION:
        _load_bin_v3(cpu, data)
        return
    if version == Constants.BIN_VERSION_V2:
        _load_bin_v2(cpu, data)
        return
    raise CPUSimulatorError(f"Unsupported binary version: {version}")


def _load_bin_v3(cpu: 'CPU', data: bytes) -> None:
    from .native import decode_program
    mem_size = struct.unpack_from('<Q', data, 6)[0]
    entry = struct.unpack_from('<I', data, 14)[0]
    sp = struct.unpack_from('<Q', data, 18)[0]
    heap_base = struct.unpack_from('<Q', data, 26)[0]
    bc_len = struct.unpack_from('<I', data, 34)[0]
    seg_count = struct.unpack_from('<I', data, 38)[0]
    segs = _unpack_segments(data[BIN_V3_HEADER:], seg_count)
    bytecode = data[BIN_V3_HEADER + len(_pack_segments(segs)):
                    BIN_V3_HEADER + len(_pack_segments(segs)) + bc_len]

    if mem_size > len(cpu.memory):
        cpu.memory.resize(mem_size)
        cpu.config.mem_size = mem_size
    cpu.memory.load_segments(segs)
    cpu.instructions, cpu.entry_pc = decode_program(bytecode)
    cpu.pc = entry
    cpu.sp = sp
    cpu.heap_base = heap_base
    cpu.heap_ptr = heap_base


def _load_bin_v2(cpu: 'CPU', data: bytes) -> None:
    """v2 遗留格式: 整块内存镜像。

    头部 34B: MAGIC 5 + ver 1 + mem_size u32 + entry u32 + sp u64 +
    bc_len u32 + 保留 8B, 之后是镜像与字节码。
    """
    from .native import decode_program
    pos = 6
    mem_size = struct.unpack_from('<I', data, pos)[0]
    entry = struct.unpack_from('<I', data, pos + 4)[0]
    sp = struct.unpack_from('<Q', data, pos + 8)[0]
    bc_len = struct.unpack_from('<I', data, pos + 16)[0]
    image_off = pos + 28
    mem_image = data[image_off:image_off + mem_size]
    bytecode = data[image_off + mem_size:image_off + mem_size + bc_len]

    if len(mem_image) > len(cpu.memory):
        cpu.memory.resize(len(mem_image))
        cpu.config.mem_size = len(mem_image)
    cpu.memory.load_bytes(0, mem_image)
    cpu.instructions, cpu.entry_pc = decode_program(bytecode)
    cpu.pc = entry
    cpu.sp = sp
