"""CROM 内存镜像与 CPUSA 二进制程序格式 (v5.9.0 段式内存)。

v5.9.0 起程序内存默认 1 GiB, 不再整块存储:
- CROM v4 / BIN v3: 只保存已分配 (非零) 的内存段;
- 旧格式 (CROM v3 / BIN v2, 整块镜像) 仍可读取, 不再写出。

CROM 优先调用 Go 原生库 (codecin.native) 进行压缩/解压, 无原生库时回退 zlib。
"""

import os
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

#: v2 容器头长度: MAGIC 5 + ver 1 + mem_size u32 + entry u32 + sp u64 +
#: bc_len u32 + 保留 8B。
BIN_V2_HEADER = 34
#: 与 Go 引擎 vm.push() / SYS ALLOCFRAME 的栈-堆隔离区一致。SP 必须高于
#: heap_base + 该值, 否则首条 PUSH/ALLOCFRAME 就会被判为栈堆碰撞。
BIN_STACK_GUARD = 4096
#: v5.9.0 之前的默认内存 (64 KiB): 旧 BIN v2 的迁移提示用。
LEGACY_BIN_MEM_SIZE = 64 * 1024


def _bin_error_name(path: Optional[str]) -> str:
    return f" '{os.path.basename(path)}'" if path else ""


def _bin_actions(path: Optional[str], mem_size: int) -> List[str]:
    """旧版 BIN 布局不兼容时的可执行建议 (两行)。"""
    target = os.path.basename(path) if path else "<程序>.bin"
    return [
        "请任选其一:",
        "  1) 用当前版本重新编译源码: "
        "codecin <源码>.cin --compile -o <新文件>.bin",
        f"  2) 用与字节码一致的内存布局运行: "
        f"codecin {target} --mem-size {mem_size}",
    ]


def _bin_header_layout_error(version: int, mem_size: int, sp: int,
                             heap_base: int, reason: str) -> CPUSimulatorError:
    """头部自相矛盾 (SP/heap/mem_size 越界): 在加载阶段直接拒绝。"""
    return CPUSimulatorError(
        f"BIN v{version} 头部记录的栈/堆布局非法: {reason}。\n"
        f"  mem_size={mem_size} bytes (0x{mem_size:x}), sp=0x{sp:x}, "
        f"heap_base=0x{heap_base:x}\n"
        "  该文件可能已损坏, 或由不兼容的工具链生成; "
        "请用当前版本重新编译源码后重试。")


def _legacy_bin_layout_error(path: Optional[str], mem_size: int, sp: int,
                             heap_base: int, runtime_mem: int,
                             runtime_heap: int) -> CPUSimulatorError:
    """v2 文件记录的布局与当前 --mem-size 布局不兼容。

    典型场景: v5.9.0 之前默认 64 KiB 内存编译的 BIN (SP≈0xfff0) 被 v5.9.0+
    的默认 1 GiB 布局加载, heap_base 变成 0x20000000, SP 低于堆警戒线。
    必须在这里拦住: 否则执行首条 PUSH/ALLOCFRAME 才会报
    "Stack overflow (collides with heap)", 很容易被误解为真实栈溢出。
    """
    lines = [
        f"无法加载旧版 BIN v2 字节码{_bin_error_name(path)}: "
        "其初始 SP 约定与当前内存布局不兼容。",
        f"  文件头布局: mem_size={mem_size} bytes (0x{mem_size:x}), "
        f"sp=0x{sp:x}, 期望 heap_base=0x{heap_base:x}",
        f"  当前运行布局: mem_size={runtime_mem} bytes (0x{runtime_mem:x}), "
        f"heap_base=0x{runtime_heap:x}",
        "  BIN v2 只记录 SP、不记录 heap_base；旧运行时固定按 "
        "heap_base = mem_size // 2 计算堆基址。",
    ]
    if sp < runtime_heap + BIN_STACK_GUARD + Constants.STACK_SLOT:
        lines.extend([
            "  按当前布局执行时 SP 低于 heap_base + 4096 的栈保护线, "
            "首条 PUSH/ALLOCFRAME 会被误报为",
            '  "Stack overflow (collides with heap)" (并非真实栈写满)。',
        ])
    else:
        lines.append(
            f"  当前布局会把堆基址从 0x{heap_base:x} 抬高到 "
            f"0x{runtime_heap:x}, 改变字节码编译时的栈/堆边界。")
    if mem_size == LEGACY_BIN_MEM_SIZE:
        lines.append(
            "  该字节码可能由 v5.9.0 之前的旧版本 (默认 64 KB 内存) 编译; "
            "格式可读不等于内存布局兼容。")
    else:
        lines.append(
            "  该字节码的初始 SP 约定与当前 --mem-size 布局不匹配; "
            "格式可读不等于内存布局兼容。")
    lines.extend(_bin_actions(path, mem_size))
    return CPUSimulatorError("\n".join(lines))


def _validate_bin_stack_layout(version: int, mem_size: int, sp: int,
                               heap_base: int) -> None:
    """校验头部的 mem_size / SP / heap_base 是否自洽。

    这里只做与运行环境无关的检查; v2 与当前 --mem-size 的差异由
    :func:`_legacy_bin_layout_error` 处理。
    """
    if mem_size < 256:
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "mem_size 过小 (最小 256 bytes)")
    if mem_size > (1 << 40):
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "mem_size 超出引擎上限 (1 TiB)")
    if heap_base > mem_size - Constants.STACK_SLOT:
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "heap_base 超出内存范围")
    if sp < Constants.STACK_SLOT or sp > mem_size - Constants.STACK_SLOT:
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "SP 超出内存范围或未给栈槽位留出空间")
    if sp < heap_base:
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "SP 低于 heap_base")
    # 只有内存本身足够大、能容纳 4 KiB 栈堆隔离区时才强制守卫线;
    # 极小的 --mem-size 布局 (例如只跑 HALT 的 .bin) 不应仅因此被拒绝。
    min_sp = heap_base + BIN_STACK_GUARD + Constants.STACK_SLOT
    if min_sp <= mem_size - Constants.STACK_SLOT and sp < min_sp:
        raise _bin_header_layout_error(
            version, mem_size, sp, heap_base,
            "SP 低于堆警戒线 (heap_base + 4096)")


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
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 6 or data[:5] != Constants.MAGIC_NUMBER:
        raise CPUSimulatorError("Invalid binary file (bad magic)")
    version = data[5]
    if version == Constants.BIN_VERSION:
        _load_bin_v3(cpu, data, path)
        return
    if version == Constants.BIN_VERSION_V2:
        _load_bin_v2(cpu, data, path)
        return
    raise CPUSimulatorError(f"Unsupported binary version: {version}")


def _load_bin_v3(cpu: 'CPU', data: bytes, path: Optional[str] = None) -> None:
    from .native import decode_program
    if len(data) < BIN_V3_HEADER:
        raise CPUSimulatorError("BIN v3 头部不完整 (文件损坏?)")
    mem_size = struct.unpack_from('<Q', data, 6)[0]
    entry = struct.unpack_from('<I', data, 14)[0]
    sp = struct.unpack_from('<Q', data, 18)[0]
    heap_base = struct.unpack_from('<Q', data, 26)[0]
    bc_len = struct.unpack_from('<I', data, 34)[0]
    seg_count = struct.unpack_from('<I', data, 38)[0]

    _validate_bin_stack_layout(3, mem_size, sp, heap_base)

    segs = _unpack_segments(data[BIN_V3_HEADER:], seg_count)
    packed_segs = _pack_segments(segs)
    bytecode_off = BIN_V3_HEADER + len(packed_segs)
    if bytecode_off + bc_len > len(data):
        raise CPUSimulatorError("BIN v3 字节码不完整 (文件损坏?)")
    # 段表声明的内容不能越过头部声明的内存大小。
    for addr, seg_data in segs:
        if addr + len(seg_data) > mem_size:
            raise CPUSimulatorError(
                f"BIN v3 段地址越界: segment 0x{addr:x}+{len(seg_data)} "
                f"超出头部 mem_size={mem_size} bytes (文件损坏?)")
    bytecode = data[bytecode_off:bytecode_off + bc_len]

    if mem_size > len(cpu.memory):
        cpu.memory.resize(mem_size)
        cpu.config.mem_size = mem_size
    cpu.memory.load_segments(segs)
    cpu.instructions, cpu.entry_pc = decode_program(bytecode)
    cpu.pc = entry
    cpu.sp = sp
    cpu.heap_base = heap_base
    cpu.heap_ptr = heap_base


def _load_bin_v2(cpu: 'CPU', data: bytes, path: Optional[str] = None) -> None:
    """v2 遗留格式: 整块内存镜像。

    头部 34B: MAGIC 5 + ver 1 + mem_size u32 + entry u32 + sp u64 +
    bc_len u32 + 保留 8B, 之后是镜像与字节码。

    v2 不记录 heap_base: 旧运行时固定按 ``heap_base = mem_size // 2`` 布局。
    因此只有当当前 ``--mem-size`` 与头部声明完全一致时才能复现旧布局;
    不一致时在加载阶段给出明确错误 (而不是让首条 PUSH 报假栈溢出)。
    """
    from .native import decode_program
    if len(data) < BIN_V2_HEADER:
        raise CPUSimulatorError("BIN v2 头部不完整 (文件损坏?)")
    mem_size = struct.unpack_from('<I', data, 6)[0]
    entry = struct.unpack_from('<I', data, 10)[0]
    sp = struct.unpack_from('<Q', data, 14)[0]
    bc_len = struct.unpack_from('<I', data, 22)[0]
    expected_heap = mem_size // 2

    _validate_bin_stack_layout(2, mem_size, sp, expected_heap)

    runtime_mem = len(cpu.memory)
    if runtime_mem > mem_size:
        # 不再静默地把旧 BIN 放进更大的 1 GiB 布局: heap_base 会从
        # mem_size//2 抬到 runtime_mem//2, 与文件里的 SP 冲突。
        raise _legacy_bin_layout_error(
            path, mem_size, sp, expected_heap, runtime_mem, cpu.heap_ptr)
    if runtime_mem < mem_size:
        cpu.memory.resize(mem_size)
        cpu.config.mem_size = mem_size

    image_end = BIN_V2_HEADER + mem_size
    if image_end + bc_len > len(data):
        raise CPUSimulatorError(
            f"BIN v2 数据不完整 (需要 {image_end + bc_len} bytes, "
            f"实际 {len(data)} bytes)")
    mem_image = data[BIN_V2_HEADER:image_end]
    bytecode = data[image_end:image_end + bc_len]

    cpu.memory.load_bytes(0, mem_image)
    cpu.instructions, cpu.entry_pc = decode_program(bytecode)
    cpu.pc = entry
    cpu.sp = sp
    # 与旧运行时一致: 堆基址由内存大小决定, v2 文件没有该字段。
    cpu.heap_base = expected_heap
    cpu.heap_ptr = expected_heap
