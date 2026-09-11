"""B1: MMU/分页 (identity 页表 / map / unmap / 权限 / 缺页) + CPU 集成。"""

import pytest

from codecin.errors import MemoryAccessError, PageFaultError
from codecin.memory import FastMemory, Mmu

from tests.helpers import run_cin_source

PAGE = Mmu.PAGE_SIZE


def _mmu_mem():
    m = FastMemory(4 * PAGE)
    m.attach_mmu(Mmu(4 * PAGE))
    return m


# ---------------- Mmu 单元 ----------------

def test_mmu_identity_translate():
    mmu = Mmu(64 * 1024)
    assert mmu.translate(0x1234, 'r') == 0x1234
    assert mmu.translate(0x1FFF + 1, 'w') == 0x2000


def test_mmu_map_and_unmap():
    mmu = Mmu(64 * 1024)
    # 页 0 -> 物理页 5 (0x5000)
    mmu.map_page(0x0, 0x5000, 'rwx')
    assert mmu.translate(0x100, 'r') == 0x5100
    mmu.unmap(0x0)
    assert not mmu.is_mapped(0x0)
    with pytest.raises(PageFaultError):
        mmu.translate(0x100, 'r')


def test_mmu_protect():
    mmu = Mmu(64 * 1024)
    mmu.protect(0x1000, 'r')           # 页1 只读
    assert mmu.translate(0x1000, 'r') == 0x1000
    with pytest.raises(MemoryAccessError):
        mmu.translate(0x1000, 'w')


def test_mmu_out_of_memory_physical():
    m = _mmu_mem()
    with pytest.raises(PageFaultError):
        m.mmu.map_page(0x0, 16 * PAGE, 'rwx')   # 超出物理内存
        m.read_byte(0)


# ---------------- FastMemory + MMU 集成 ----------------

def test_memory_identity_mmu_rw():
    m = _mmu_mem()
    m.write_qword(0x100, 0xCAFE)
    assert m.read_qword(0x100) == 0xCAFE


def test_memory_remap_page_redirects_physical():
    m = _mmu_mem()
    # 虚拟页0 -> 物理页2 (0x2000)
    m.mmu.map_page(0x0, 0x2000, 'rwx')
    m.write_block(0x10, b'hello')
    assert bytes(m.get_snapshot())[0x2010:0x2015] == b'hello'
    assert m.read_block(0x10, 5) == b'hello'


def test_memory_unmapped_page_faults():
    m = _mmu_mem()
    m.mmu.unmap(0x0)
    with pytest.raises(PageFaultError):
        m.read_byte(0x50)


def test_memory_block_across_pages():
    m = _mmu_mem()
    # 块跨越页边界 (identity)
    data = bytes(range(200))
    m.write_block(PAGE - 64, data)
    assert m.read_block(PAGE - 64, 200) == data


# ---------------- CPU 集成 ----------------

CIN_SRC = """
function main() -> int {
    int arr[8]
    arr[0] = 10
    arr[7] = 32
    return arr[0] + arr[7]
}
"""


def test_cpu_mmu_identity_same_result():
    # 不启用 vs 启用 (identity 页表) 结果一致
    assert run_cin_source(CIN_SRC).regs.read(0) == 42
    assert run_cin_source(CIN_SRC, mmu=True).regs.read(0) == 42


def test_cpu_mmu_page_fault_aborts(capsys):
    from codecin.cin import CINCompiler
    from tests.helpers import new_cpu
    src = """
int g = 5
function main() -> int {
    return g
}
"""
    res = CINCompiler().compile_source(src)
    cpu = new_cpu(mmu=True, use_native=False)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = cpu.pc = 0
    # 解映射全局数据所在页 (数据段从 0 开始)
    cpu.memory.mmu.unmap(0)
    cpu.run()
    out = capsys.readouterr().out
    assert 'Page fault' in out
