"""② CROM v3 + MMU 页表持久化 (roundtrip / 兼容 / 忽略模式)。"""

import os

import pytest

from codecin import crom
from codecin.errors import MemoryAccessError, PageFaultError
from codecin.memory import FastMemory, Mmu

PAGE = Mmu.PAGE_SIZE
SIZE = 4 * PAGE


def _mapped_mem():
    mem = FastMemory(SIZE)
    mem.attach_mmu(Mmu(SIZE))
    mmu = mem.mmu
    mmu.map_page(0x0, 0x2000, 'rwx')     # 页0 -> 物理页2
    mmu.protect(0x1000, 'r')              # 页1 只读
    mmu.unmap(0x3000)                     # 页3 缺页
    mem.write_block(0x10, b'abc')         # 写入物理 0x2010
    mem.read_byte(0x1000)                 # 让 identity 懒映射记录页1 (已被 protect)
    return mem


def test_crom_roundtrip_preserves_mmu_state(workdir):
    mem = _mapped_mem()
    path = os.path.join(workdir, 'mmu.crom')
    crom.save_crom(mem, path)

    fresh = FastMemory(SIZE)
    crom.load_crom(fresh, path, enable_mmu=True)
    mmu = fresh.mmu
    assert mmu is not None
    # 映射保留
    assert mmu.translate(0x100, 'r') == 0x2100
    # 内容留在目标物理页
    assert fresh.read_block(0x10, 3) == b'abc'
    # 只读保护保留
    assert fresh.read_byte(0x1000) == 0
    with pytest.raises(MemoryAccessError):
        fresh.write_byte(0x1000, 1)
    # 解映射页仍缺页
    assert not mmu.is_mapped(0x3000)
    with pytest.raises(PageFaultError):
        fresh.read_byte(0x3100)


def test_crom_without_mmu_unchanged(workdir):
    plain = FastMemory(1024)
    plain.write_block(0x40, b'plain-data')
    path = os.path.join(workdir, 'plain.crom')
    crom.save_crom(plain, path)

    fresh = FastMemory(1024)
    crom.load_crom(fresh, path)
    assert fresh.mmu is None
    assert fresh.read_block(0x40, 10) == b'plain-data'


def test_crom_mmu_load_without_enable_keeps_physical(workdir):
    mem = _mapped_mem()
    path = os.path.join(workdir, 'mmu2.crom')
    crom.save_crom(mem, path)

    fresh = FastMemory(SIZE)
    crom.load_crom(fresh, path, enable_mmu=False)
    assert fresh.mmu is None
    # 物理内容仍按原物理地址保存
    assert bytes(fresh.get_snapshot())[0x2010:0x2013] == b'abc'


def test_crom_mmu_roundtrip_compressed_and_plain(workdir):
    mem = _mapped_mem()
    for compress in (True, False):
        path = os.path.join(workdir, f'mmu_{compress}.crom')
        crom.save_crom(mem, path, compress=compress)
        fresh = FastMemory(SIZE)
        crom.load_crom(fresh, path, enable_mmu=True)
        assert fresh.mmu.translate(0x100, 'r') == 0x2100
        assert fresh.read_block(0x10, 3) == b'abc'
