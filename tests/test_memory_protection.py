"""memory.py 保护检查统一 (建议 8): 浮点/块读写不可绕过保护。"""

import pytest

from codecin.errors import MemoryAccessError
from codecin.memory import FastMemory


def _mem():
    return FastMemory(1024)


def test_float_access_honors_protection():
    m = _mem()
    m.set_protection(8, 'r')
    with pytest.raises(MemoryAccessError):
        m.write_float(8, 1.5)
    m.write_float(16, 1.5)      # 未保护地址可写
    assert m.read_float(16) == pytest.approx(1.5)


def test_double_access_honors_protection():
    m = _mem()
    m.set_protection(32, 'r')
    with pytest.raises(MemoryAccessError):
        m.write_double(32, 2.0)
    assert m.read_double(32) == 0.0          # 只读可读


def test_read_block_honors_protection():
    m = _mem()
    m.write_block(0, bytes(range(64)))
    m.set_protection(32, 'rw')   # 32.. 允许读写, 不影响 16..31
    # 区域含受保护字节且读被禁时拒绝
    m2 = _mem()
    m2.write_block(0, bytes(range(64)))
    m2.set_protection(32, 'w')   # 仅写: 块读覆盖该字节应被拒
    with pytest.raises(MemoryAccessError):
        m2.read_block(16, 32)


def test_write_block_honors_protection():
    m = _mem()
    m.set_protection(32, 'r')    # 仅读: 块写覆盖该字节应被拒
    with pytest.raises(MemoryAccessError):
        m.write_block(16, bytes(32))
    m.write_block(16, bytes(8))  # 不触碰受保护字节 -> 允许


def test_load_bytes_honors_protection():
    m = _mem()
    m.set_protection(100, 'r')
    with pytest.raises(MemoryAccessError):
        m.load_bytes(100, b'hello')


def test_write_string_honors_protection():
    m = _mem()
    m.write_string(0, 'ok')
    m.set_protection(0, 'r')
    with pytest.raises(MemoryAccessError):
        m.write_string(0, 'overwrite')
