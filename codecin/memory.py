import struct
from typing import Dict, Iterator, List, Optional, Tuple

from .console import Console, Table
from .errors import MemoryAccessError

# ==================== 稀疏内存 ====================
# 程序内存按需分配: 逻辑大小可达 1 GiB, 只有被写过的 4 KiB 页才占真实内存。
# 这与 Go 原生引擎的 make([]byte, memSize) (OS 懒提交) 行为一致:
# 读未写过的地址返回 0, 写入时才真正落页。


class FastMemory:
    PAGE_BITS = 12
    PAGE_SIZE = 1 << PAGE_BITS
    PAGE_MASK = PAGE_SIZE - 1

    def __init__(self, size: int = 1 << 30):
        self._size = size
        self._pages: Dict[int, bytearray] = {}
        self._protection: Dict[int, str] = {}
        # 调试日志钩子 (DEBUG 级别记录每次读写)
        self._log = None

    # ---------------- 页管理 ----------------

    def _page(self, idx: int) -> Optional[bytearray]:
        return self._pages.get(idx)

    def _ensure_page(self, idx: int) -> bytearray:
        page = self._pages.get(idx)
        if page is None:
            page = bytearray(self.PAGE_SIZE)
            self._pages[idx] = page
        return page

    def attach_logger(self, logger) -> None:
        """挂接日志器, DEBUG 级别下记录所有内存读写。"""
        self._log = logger

    @property
    def _mem_trace(self) -> bool:
        return self._log is not None and self._log.is_debug

    def _trace_mem(self, op: str, addr: int, value, width: int) -> None:
        vs = f"0x{value:x}" if isinstance(value, int) else repr(value)
        self._log.trace(f"  MEM {op:<5} @0x{addr:04x} w={width} value={vs}")

    def __len__(self) -> int:
        return self._size

    @property
    def size(self) -> int:
        return self._size

    @property
    def resident_bytes(self) -> int:
        """实际占用的物理内存字节数 (已分配页)。"""
        return len(self._pages) * self.PAGE_SIZE

    def reset(self, size: Optional[int] = None) -> None:
        if size is not None:
            self._size = size
        self._pages.clear()
        self._protection.clear()

    def resize(self, new_size: int) -> None:
        if new_size <= self._size:
            return
        self._size = new_size

    def set_protection(self, addr: int, perms: str, size: int = 1) -> None:
        for i in range(size):
            self._protection[addr + i] = perms

    def check_access(self, addr: int, access: str) -> bool:
        perm = self._protection.get(addr, 'rwx')
        return access in perm

    def _check_bounds(self, addr: int, size: int = 1) -> None:
        if not 0 <= addr <= self._size - size:
            hint = ""
            if addr >= (1 << 63):
                # 按位模 2^64 后为负数: 典型成因是栈溢出 (SP 被推到负地址)
                # 或野指针。可读性提示, 与 Go 引擎 vm.go 的文案保持一致。
                hint = " (negative address: stack overflow or bad pointer?)"
            raise MemoryAccessError(
                f"Address 0x{addr:x} out of bounds (memory size 0x{self._size:x}, "
                f"access width {size}){hint}")

    def _check_protection(self, addr: int, access: str) -> None:
        if not self.check_access(addr, access):
            raise MemoryAccessError(
                f"Memory protection violation at 0x{addr:x} for '{access}'")

    def _check_protection_range(self, addr: int, size: int, access: str) -> None:
        """对 [addr, addr+size) 内每个被保护字节做访问检查 (块读写统一入口)。"""
        if not self._protection:
            return
        end = addr + size
        for paddr, perms in self._protection.items():
            if addr <= paddr < end and access not in perms:
                raise MemoryAccessError(
                    f"Memory protection violation at 0x{paddr:x} "
                    f"for '{access}' (range 0x{addr:x}+{size})")

    # ---------------- 单字节 ----------------

    def read_byte(self, addr: int) -> int:
        self._check_bounds(addr)
        self._check_protection(addr, 'r')
        page = self._page(addr >> self.PAGE_BITS)
        v = page[addr & self.PAGE_MASK] if page is not None else 0
        if self._mem_trace:
            self._trace_mem('RD', addr, v, 1)
        return v

    def write_byte(self, addr: int, value: int) -> None:
        self._check_bounds(addr)
        self._check_protection(addr, 'w')
        self._ensure_page(addr >> self.PAGE_BITS)[addr & self.PAGE_MASK] = value & 0xFF
        if self._mem_trace:
            self._trace_mem('WR', addr, value & 0xFF, 1)

    # ---------------- 定宽多字节 (4 KiB 页内不跨页的问题与旧实现一致) ----------------

    def read_word(self, addr: int) -> int:
        self._check_bounds(addr, 2)
        self._check_protection(addr, 'r')
        return self._read_into(addr, 2, '<H')

    def write_word(self, addr: int, value: int) -> None:
        self._check_bounds(addr, 2)
        self._check_protection(addr, 'w')
        self._write_from(addr, 2, struct.pack('<H', value & 0xFFFF))

    def read_dword(self, addr: int) -> int:
        self._check_bounds(addr, 4)
        self._check_protection(addr, 'r')
        return self._read_into(addr, 4, '<I')

    def write_dword(self, addr: int, value: int) -> None:
        self._check_bounds(addr, 4)
        self._check_protection(addr, 'w')
        self._write_from(addr, 4, struct.pack('<I', value & 0xFFFFFFFF))

    def read_qword(self, addr: int) -> int:
        self._check_bounds(addr, 8)
        self._check_protection(addr, 'r')
        return self._read_into(addr, 8, '<Q')

    def write_qword(self, addr: int, value: int) -> None:
        self._check_bounds(addr, 8)
        self._check_protection(addr, 'w')
        self._write_from(addr, 8, struct.pack('<Q', value & 0xFFFFFFFFFFFFFFFF))

    def read_float(self, addr: int) -> float:
        self._check_bounds(addr, 4)
        self._check_protection(addr, 'r')
        return struct.unpack('<f', self._raw_read(addr, 4))[0]

    def write_float(self, addr: int, value: float) -> None:
        self._check_bounds(addr, 4)
        self._check_protection(addr, 'w')
        self._write_from(addr, 4, struct.pack('<f', value))

    def read_double(self, addr: int) -> float:
        self._check_bounds(addr, 8)
        self._check_protection(addr, 'r')
        return struct.unpack('<d', self._raw_read(addr, 8))[0]

    def write_double(self, addr: int, value: float) -> None:
        self._check_bounds(addr, 8)
        self._check_protection(addr, 'w')
        self._write_from(addr, 8, struct.pack('<d', value))

    def _raw_read(self, addr: int, size: int) -> bytes:
        """读 [addr, addr+size); 未分配页返回 0。"""
        out = bytearray(size)
        pos = 0
        while pos < size:
            a = addr + pos
            page = self._page(a >> self.PAGE_BITS)
            if page is not None:
                off = a & self.PAGE_MASK
                n = min(self.PAGE_SIZE - off, size - pos)
                out[pos:pos + n] = page[off:off + n]
                pos += n
            else:
                pos += self.PAGE_SIZE - (a & self.PAGE_MASK)
        return bytes(out)

    def _raw_write(self, addr: int, data: bytes) -> None:
        """写 [addr, addr+len(data)); 按需分配页。"""
        pos = 0
        while pos < len(data):
            a = addr + pos
            page = self._ensure_page(a >> self.PAGE_BITS)
            off = a & self.PAGE_MASK
            n = min(self.PAGE_SIZE - off, len(data) - pos)
            page[off:off + n] = data[pos:pos + n]
            pos += n

    def _read_into(self, addr: int, width: int, fmt: str) -> int:
        return struct.unpack(fmt, self._raw_read(addr, width))[0]

    def _write_from(self, addr: int, width: int, raw: bytes) -> None:
        self._raw_write(addr, raw)

    # ---------------- 块 / 字符串 ----------------

    def read_block(self, addr: int, size: int) -> bytes:
        self._check_bounds(addr, size)
        self._check_protection_range(addr, size, 'r')
        return self._raw_read(addr, size)

    def write_block(self, addr: int, data) -> None:
        size = len(data)
        self._check_bounds(addr, size)
        self._check_protection_range(addr, size, 'w')
        self._raw_write(addr, bytes(data))

    def read_string(self, addr: int, max_len: int = 4096) -> str:
        chars = []
        for _ in range(max_len):
            b = self.read_byte(addr)
            if b == 0:
                break
            chars.append(b)
            addr += 1
        return bytes(chars).decode('utf-8', errors='replace')

    def read_cstr_bytes(self, addr: int, max_len: int = 4096) -> bytes:
        """读 NUL 结尾的**原始字节** (不做 UTF-8 解码)。

        CIN 的字符串是字节序列: `strlen` / `substr` / `s[i]` 都是字节语义。
        用 ``read_string`` 再 ``encode`` 会经过一次 UTF-8 编解码, 非法字节序列
        会被替换成 U+FFFD (每个 3 字节), 于是与 Go 原生 VM 的字节结果不一致。
        需要字节级语义的地方一律用本方法。
        """
        chars = []
        for _ in range(max_len):
            b = self.read_byte(addr)
            if b == 0:
                break
            chars.append(b)
            addr += 1
        return bytes(chars)

    def write_string(self, addr: int, text: str) -> int:
        data = text.encode('utf-8') + b'\x00'
        self.write_block(addr, data)
        return len(data)

    def load_bytes(self, addr: int, data) -> None:
        self.write_block(addr, bytes(data))

    def snapshot_segments(self) -> List[Tuple[int, bytes]]:
        """返回全部已分配内容为 (页起始地址, 数据) 列表, 按地址升序。

        段式格式 (CROM v4 / bin v3 / native v2 ABI) 的统一出口。
        """
        segs = []
        for idx in sorted(self._pages):
            segs.append((idx << self.PAGE_BITS, bytes(self._pages[idx])))
        return segs

    def load_segments(self, segs: List[Tuple[int, bytes]]) -> None:
        """用 (addr, data) 列表恢复内容 (不清空现有页)。"""
        for addr, data in segs:
            self.write_block(addr, data)

    def get_snapshot(self, start: int = 0, count: int = -1) -> bytes:
        """读取一段连续内容 (未分配页为 0)。

        count < 0 时读到最后一个已写字节为止 (稀疏友好: 不会输出
        整个逻辑空间的零填充, 与旧的整块快照在测试语义上兼容)。
        """
        if count >= 0:
            return self._raw_read(start, count)
        end = start
        for idx in self._pages:
            page_end = (idx << self.PAGE_BITS) + self.PAGE_SIZE
            if page_end > end:
                end = page_end
        if end <= start:
            return b''
        return self._raw_read(start, end - start)

    def display_memory(self, title: str = "Memory Dump", start: int = 0,
                       count: int = 32, console: Optional[Console] = None) -> None:
        if console is None:
            return
        table = Table(title=title)
        table.add_column("Address")
        table.add_column("Hex")
        table.add_column("ASCII")
        table.add_column("Prot")
        end = min(start + count, self._size)
        for i in range(start, end, 16):
            chunk = self._raw_read(i, min(16, end - i))
            hex_str = ' '.join(f'{b:02X}' for b in chunk)
            ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
            perm = self._protection.get(i, 'rwx')
            table.add_row(f"{i:04X}", hex_str, ascii_str, perm)
        console.print(str(table))
