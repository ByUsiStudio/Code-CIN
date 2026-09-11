import struct
from typing import Dict, Iterator, Optional, Tuple

from .console import Console, Table
from .errors import MemoryAccessError, PageFaultError

# ==================== MMU / 分页 (B1) ====================


class Mmu:
    """扁平 4K 页表: vpn -> (ppn, perms)。

    - 默认按需 identity 映射 (vpn == ppn, perms='rwx');
    - unmap 后访问触发 PageFaultError; 权限位按 'r'/'w'/'x' 检查。
    """

    PAGE_BITS = 12
    PAGE_SIZE = 1 << PAGE_BITS
    PAGE_MASK = PAGE_SIZE - 1

    def __init__(self, mem_size: int):
        self.mem_size = mem_size
        self.pages: Dict[int, Tuple[int, str]] = {}
        self._blacklist = set()   # 显式 unmap 的 vpn (禁止 identity 回填)
        self._identity = True     # 未显式配置/未 unmap 的页按 identity 映射

    def _page_count(self) -> int:
        return (self.mem_size + self.PAGE_SIZE - 1) // self.PAGE_SIZE

    def reset(self, mem_size: Optional[int] = None) -> None:
        if mem_size is not None:
            self.mem_size = mem_size
        self.pages = {}
        self._blacklist = set()

    def map(self, vpn: int, ppn: Optional[int] = None,
            perms: str = 'rwx') -> None:
        """映射 vpn; 缺省 ppn 采用 identity (ppn = vpn)。"""
        if ppn is None:
            ppn = vpn
        self.pages[vpn] = (ppn, perms)
        self._blacklist.discard(vpn)

    def map_page(self, vaddr: int, paddr: int, perms: str = 'rwx') -> None:
        self.map(vaddr >> self.PAGE_BITS, paddr >> self.PAGE_BITS, perms)

    def unmap(self, vaddr: int) -> bool:
        vpn = vaddr >> self.PAGE_BITS
        self._blacklist.add(vpn)
        return self.pages.pop(vpn, None) is not None

    def protect(self, vaddr: int, perms: str) -> None:
        vpn = vaddr >> self.PAGE_BITS
        if vpn in self.pages:
            ppn, _ = self.pages[vpn]
        elif vpn in self._blacklist:
            raise MemoryAccessError(
                f"Cannot protect unmapped page {vpn} (0x{vaddr:x})")
        else:
            ppn = vpn
        self.pages[vpn] = (ppn, perms)
        self._blacklist.discard(vpn)

    def is_mapped(self, vaddr: int) -> bool:
        vpn = vaddr >> self.PAGE_BITS
        return vpn in self.pages or (self._identity and vpn not in self._blacklist)

    def translate(self, vaddr: int, access: str) -> int:
        vpn = vaddr >> self.PAGE_BITS
        off = vaddr & self.PAGE_MASK
        ent = self.pages.get(vpn)
        if ent is None:
            if self._identity and vpn not in self._blacklist:
                ent = (vpn, 'rwx')     # 懒 identity
                self.pages[vpn] = ent
            else:
                raise PageFaultError(
                    f"Page fault at virtual 0x{vaddr:x} (page {vpn} unmapped)")
        ppn, perms = ent
        if access not in perms:
            raise MemoryAccessError(
                f"Page protection violation at 0x{vaddr:x} for '{access}'")
        paddr = (ppn << self.PAGE_BITS) | off
        if not 0 <= paddr < self.mem_size:
            raise PageFaultError(
                f"Physical address 0x{paddr:x} out of memory (size "
                f"0x{self.mem_size:x})")
        return paddr


# ==================== 内存 ====================


class FastMemory:
    def __init__(self, size: int = 64 * 1024):
        self._size = size
        self._memory = bytearray(size)
        self._view = memoryview(self._memory)
        self._protection: Dict[int, str] = {}
        # B1: 可选 MMU (None = 关)
        self._mmu: Optional[Mmu] = None
        # 调试日志钩子 (DEBUG 级别记录每次读写)
        self._log = None

    # ---------------- MMU ----------------

    def attach_mmu(self, mmu: Optional[Mmu]) -> None:
        self._mmu = mmu

    @property
    def mmu(self) -> Optional[Mmu]:
        return self._mmu

    def _phys(self, addr: int, access: str) -> int:
        if self._mmu is None:
            return addr
        return self._mmu.translate(addr, access)

    def _chunks(self, addr: int, size: int, access: str
                ) -> Iterator[Tuple[int, int]]:
        """把 [addr, addr+size) 翻译为物理分块 (含 MMU 分页)。"""
        if self._mmu is None:
            yield addr, size
            return
        end = addr + size
        while addr < end:
            paddr = self._mmu.translate(addr, access)
            step = min(self._mmu.PAGE_SIZE - (addr & self._mmu.PAGE_MASK),
                       end - addr)
            yield paddr, step
            addr += step

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

    def reset(self, size: Optional[int] = None) -> None:
        if size is not None:
            self._size = size
        self._memory = bytearray(self._size)
        self._view = memoryview(self._memory)
        self._protection.clear()
        if self._mmu is not None:
            self._mmu.reset(self._size)

    def resize(self, new_size: int) -> None:
        if new_size <= self._size:
            return
        new_mem = bytearray(new_size)
        new_mem[:self._size] = self._memory
        self._memory = new_mem
        self._view = memoryview(self._memory)
        self._size = new_size
        if self._mmu is not None:
            self._mmu.reset(self._size)

    def set_protection(self, addr: int, perms: str, size: int = 1) -> None:
        for i in range(size):
            self._protection[addr + i] = perms

    def check_access(self, addr: int, access: str) -> bool:
        perm = self._protection.get(addr, 'rwx')
        return access in perm

    def _check_bounds(self, addr: int, size: int = 1) -> None:
        if not 0 <= addr <= self._size - size:
            raise MemoryAccessError(
                f"Address 0x{addr:x} out of bounds (memory size 0x{self._size:x}, "
                f"access width {size})")

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
        p = self._phys(addr, 'r')
        self._check_bounds(p)
        self._check_protection(p, 'r')
        v = self._view[p]
        if self._mem_trace:
            self._trace_mem('RD', addr, v, 1)
        return v

    def write_byte(self, addr: int, value: int) -> None:
        p = self._phys(addr, 'w')
        self._check_bounds(p)
        self._check_protection(p, 'w')
        self._view[p] = value & 0xFF
        if self._mem_trace:
            self._trace_mem('WR', addr, value & 0xFF, 1)

    # ---------------- 定宽多字节 (要求不跨页) ----------------

    def _rw_phys(self, addr: int, width: int, access: str) -> int:
        p = self._phys(addr, access)
        self._check_bounds(p, width)
        self._check_protection(p, access)
        return p

    def read_word(self, addr: int) -> int:
        p = self._rw_phys(addr, 2, 'r')
        v = struct.unpack_from('<H', self._view, p)[0]
        if self._mem_trace:
            self._trace_mem('RD', addr, v, 2)
        return v

    def write_word(self, addr: int, value: int) -> None:
        p = self._rw_phys(addr, 2, 'w')
        struct.pack_into('<H', self._view, p, value & 0xFFFF)
        if self._mem_trace:
            self._trace_mem('WR', addr, value & 0xFFFF, 2)

    def read_dword(self, addr: int) -> int:
        p = self._rw_phys(addr, 4, 'r')
        v = struct.unpack_from('<I', self._view, p)[0]
        if self._mem_trace:
            self._trace_mem('RD', addr, v, 4)
        return v

    def write_dword(self, addr: int, value: int) -> None:
        p = self._rw_phys(addr, 4, 'w')
        struct.pack_into('<I', self._view, p, value & 0xFFFFFFFF)
        if self._mem_trace:
            self._trace_mem('WR', addr, value & 0xFFFFFFFF, 4)

    def read_qword(self, addr: int) -> int:
        p = self._rw_phys(addr, 8, 'r')
        v = struct.unpack_from('<Q', self._view, p)[0]
        if self._mem_trace:
            self._trace_mem('RD', addr, v, 8)
        return v

    def write_qword(self, addr: int, value: int) -> None:
        p = self._rw_phys(addr, 8, 'w')
        struct.pack_into('<Q', self._view, p, value & 0xFFFFFFFFFFFFFFFF)
        if self._mem_trace:
            self._trace_mem('WR', addr, value & 0xFFFFFFFFFFFFFFFF, 8)

    def read_float(self, addr: int) -> float:
        p = self._rw_phys(addr, 4, 'r')
        return struct.unpack_from('<f', self._view, p)[0]

    def write_float(self, addr: int, value: float) -> None:
        p = self._rw_phys(addr, 4, 'w')
        struct.pack_into('<f', self._view, p, value)

    def read_double(self, addr: int) -> float:
        p = self._rw_phys(addr, 8, 'r')
        return struct.unpack_from('<d', self._view, p)[0]

    def write_double(self, addr: int, value: float) -> None:
        p = self._rw_phys(addr, 8, 'w')
        struct.pack_into('<d', self._view, p, value)

    # ---------------- 块 / 字符串 ----------------

    def read_block(self, addr: int, size: int) -> bytes:
        self._check_bounds(addr, size)
        self._check_protection_range(addr, size, 'r')
        if self._mmu is None:
            return bytes(self._view[addr:addr + size])
        parts = []
        for p, step in self._chunks(addr, size, 'r'):
            parts.append(bytes(self._view[p:p + step]))
        return b''.join(parts)

    def write_block(self, addr: int, data) -> None:
        size = len(data)
        self._check_bounds(addr, size)
        self._check_protection_range(addr, size, 'w')
        if self._mmu is None:
            self._view[addr:addr + size] = data
            return
        src = bytes(data)
        off = 0
        for p, step in self._chunks(addr, size, 'w'):
            self._view[p:p + step] = src[off:off + step]
            off += step

    def read_string(self, addr: int, max_len: int = 4096) -> str:
        chars = []
        for _ in range(max_len):
            b = self.read_byte(addr)
            if b == 0:
                break
            chars.append(b)
            addr += 1
        return bytes(chars).decode('utf-8', errors='replace')

    def write_string(self, addr: int, text: str) -> int:
        data = text.encode('utf-8') + b'\x00'
        self.write_block(addr, data)
        return len(data)

    def load_bytes(self, addr: int, data) -> None:
        self.write_block(addr, bytes(data))

    def get_snapshot(self, start: int = 0, count: int = -1) -> bytes:
        if count < 0:
            count = self._size - start
        return bytes(self._view[start:start + count])

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
            chunk = self._view[i:min(i + 16, end)]
            hex_str = ' '.join(f'{b:02X}' for b in chunk)
            ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
            perm = self._protection.get(i, 'rwx')
            table.add_row(f"{i:04X}", hex_str, ascii_str, perm)
        console.print(str(table))
