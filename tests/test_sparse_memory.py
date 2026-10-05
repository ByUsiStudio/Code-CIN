"""稀疏分页 FastMemory (v5.9.0) 与段式格式 (CROM v4 / BIN v3) 测试。

v5.9.0 起内存默认 1 GiB (OS 按需提交, 4 KiB 页), 只占用实际写入的物理内存;
持久化格式只保存已分配页 (段式), 旧全量镜像格式已被替换。
"""

import struct

import pytest

from codecin import crom
from codecin.errors import CPUSimulatorError, MemoryAccessError
from codecin.memory import FastMemory

GIB = 1 << 30

# ==================== 稀疏分页 ====================

def test_default_size_is_1gib():
    assert len(FastMemory()) == GIB
    assert FastMemory().size == GIB

def test_sparse_write_only_touches_pages():
    m = FastMemory()
    assert m.resident_bytes == 0
    m.write_block(GIB - 256, b'\x01\x02')           # 接近 1 GiB 末尾
    assert m.read_block(GIB - 256, 2) == b'\x01\x02'
    # 常驻内存只有 1 页 (4 KiB), 而不是整个 1 GiB
    assert m.resident_bytes == 4096

def test_write_at_last_byte():
    m = FastMemory()
    m.write_byte(GIB - 1, 0xAB)
    assert m.read_byte(GIB - 1) == 0xAB
    assert m.resident_bytes == 4096

def test_out_of_bounds_raises():
    m = FastMemory(size=4096)
    with pytest.raises(MemoryAccessError):
        m.write_byte(4096, 1)
    with pytest.raises(MemoryAccessError):
        m.read_block(4090, 8)

def test_cross_page_read_write():
    m = FastMemory()
    addr = 4096 - 3                                  # 跨 4 KiB 页边界
    payload = bytes(range(6))
    m.write_block(addr, payload)
    assert m.read_block(addr, 6) == payload

def test_unaligned_qword_roundtrip():
    m = FastMemory()
    addr = 8192 - 5                                  # 8 字节访问跨页
    m.write_qword(addr, 0x1122334455667788)
    assert m.read_qword(addr) == 0x1122334455667788

def test_word_dword_float_roundtrip():
    m = FastMemory()
    m.write_word(64, 0xBEEF)
    m.write_dword(128, 0xDEADBEEF)
    m.write_double(192, 2.5)
    assert m.read_word(64) == 0xBEEF
    assert m.read_dword(128) == 0xDEADBEEF
    assert m.read_double(192) == 2.5

def test_string_roundtrip():
    m = FastMemory()
    m.write_string(256, 'hello 中文')
    assert m.read_string(256) == 'hello 中文'
    raw = m.read_cstr_bytes(256)
    assert raw == 'hello 中文'.encode('utf-8')

def test_snapshot_segments_sorted_and_minimal():
    m = FastMemory()
    m.write_block(1 << 20, b'world' * 10)
    m.write_block(100, b'hello')
    segs = m.snapshot_segments()
    addrs = [a for a, _ in segs]
    assert addrs == sorted(addrs)
    # 只包含被触碰的页, 不包含 1 GiB 的空洞
    assert all(len(d) <= 8192 for _, d in segs)

def test_load_segments_roundtrip():
    m = FastMemory()
    m.write_block(100, b'hello')
    m.write_block(1 << 20, b'world' * 10)
    m2 = FastMemory()
    m2.load_segments(m.snapshot_segments())
    assert m2.read_block(100, 5) == b'hello'
    assert m2.read_block(1 << 20, 50) == b'world' * 10

def test_get_snapshot_sparse_reads_to_last_page():
    m = FastMemory()
    m.write_block(1 << 20, b'tail')
    snap = m.get_snapshot()                          # 不应试图分配 1 GiB
    # 读到最后一个已分配页的页尾 (4 KiB 对齐), 其余为零填充
    assert len(snap) == (1 << 20) + 4096
    assert snap[1 << 20:(1 << 20) + 4] == b'tail'

def test_reset_clears_pages():
    m = FastMemory()
    m.write_block(0, b'abc')
    m.reset()
    assert m.resident_bytes == 0
    assert m.read_block(0, 3) == b'\x00\x00\x00'

# ==================== CROM v4 (段式镜像) ====================

def test_crom_v4_roundtrip_compressed(tmp_path):
    m = FastMemory()
    m.write_block(0x100, b'CROMV4' * 100)
    m.write_block(1 << 16, b'\xAA' * 16)
    path = str(tmp_path / 'img.crom')
    crom.save_crom(m, path, compress=True)
    m2 = FastMemory()
    crom.load_crom(m2, path)
    assert m2.read_block(0x100, 600) == b'CROMV4' * 100
    assert m2.read_block(1 << 16, 16) == b'\xAA' * 16

def test_crom_v4_roundtrip_uncompressed(tmp_path):
    m = FastMemory()
    m.write_block(0x40, b'RAW' * 10)
    path = str(tmp_path / 'raw.crom')
    crom.save_crom(m, path, compress=False)
    m2 = FastMemory()
    crom.load_crom(m2, path)
    assert m2.read_block(0x40, 30) == b'RAW' * 10

def test_crom_v4_file_only_stores_allocated_pages(tmp_path):
    m = FastMemory()
    m.write_block(1 << 20, b'x' * 8)
    path = str(tmp_path / 'tiny.crom')
    crom.save_crom(m, path, compress=False)
    # 16B 头 + 16B 段表 + 8B 数据 + 1 页数据? 至远小于全量镜像
    assert __import__('os').path.getsize(path) < 8192 + 64

def test_crom_rejects_corrupt_checksum(tmp_path):
    m = FastMemory()
    m.write_block(0, b'data')
    path = str(tmp_path / 'bad.crom')
    crom.save_crom(m, path, compress=False)
    with open(path, 'rb') as f:
        blob = bytearray(f.read())
    blob[-1] ^= 0xFF                                 # 破坏数据段
    with open(path, 'wb') as f:
        f.write(blob)
    with pytest.raises(CPUSimulatorError):
        crom.load_crom(FastMemory(), path)

# ==================== 段表编解码 ====================

def test_pack_unpack_segments():
    segs = [(0, b'abc'), (1 << 30, b'z' * 5)]
    packed = crom._pack_segments(segs)
    # 每段 16B 头 (addr u64 + len u64) + 数据
    assert len(packed) == 16 + 3 + 16 + 5
    assert crom._unpack_segments(packed, 2) == segs

def test_unpack_truncated_raises():
    packed = crom._pack_segments([(0, b'abcdef')])
    with pytest.raises(CPUSimulatorError):
        crom._unpack_segments(packed[:-1], 1)

def test_bin_v3_header_size_constant():
    # BIN v3 头: MAGIC5 + ver1 + mem_size u64 + entry u32 + sp u64
    #          + heap_base u64 + bc_len u32 + seg_count u32 + 保留 8B = 50
    assert crom.BIN_V3_HEADER == 50
