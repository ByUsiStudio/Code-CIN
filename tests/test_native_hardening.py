"""原生库加固测试 (docs/SUGGESTIONS.md §3.1.6 / §3.1.7)。

被测行为:
  * decodeBytecode 不再信任输入头部 (版本 / count / argc), 畸形字节码
    必须被安全拒绝, 而不是 OOM 或越界 panic;
  * CROM 解压有上限, zip bomb 被拒绝;
  * 步数用尽报错误而不是伪装成正常停机。
所有用例都要求原生库存在, 否则跳过。
"""

import struct
import zlib

import pytest

from codecin import native

needs_native = pytest.mark.skipif(native.get_engine() is None,
                                  reason='native library not built')


def _bc(entry=0, instructions=b'', version=1, count=None):
    """手工构造 UCBC 字节流 (magic + version + entry + count + body)。"""
    if count is None:
        count = 0
    return b'UCBC' + bytes([version]) + struct.pack('<I', entry) + \
        struct.pack('<I', count) + instructions


@needs_native
def test_bad_magic_rejected():
    eng = native.get_engine()
    res = eng.run(b'XXXX' + bytes(60), b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_wrong_version_rejected():
    """版本字节此前从不校验。"""
    eng = native.get_engine()
    bc = _bc(version=99)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_huge_instruction_count_does_not_oom():
    """13 字节输入声明 0xFFFFFFFF 条指令: 旧实现按此预分配 ~137GB。"""
    eng = native.get_engine()
    bc = b'UCBC' + bytes([1]) + struct.pack('<I', 0) + \
        struct.pack('<I', 0xFFFFFFFF)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_argc_mismatch_rejected():
    """MOV 需要 2 个操作数; 声明 0 个时旧实现会在 execute 里 args[1] 越界 panic。"""
    eng = native.get_engine()
    # opcode 0 = MOV, argc 0
    body = bytes([0, 0])
    bc = _bc(count=1, instructions=body)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_unknown_opcode_rejected():
    eng = native.get_engine()
    body = bytes([250, 0])          # 超出 argCounts 表
    bc = _bc(count=1, instructions=body)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_truncated_operand_rejected():
    eng = native.get_engine()
    # MOV 声明 1 个操作数但字节流在操作数中途结束
    body = bytes([0, 1, 1]) + b'\x00' * 5
    bc = _bc(count=1, instructions=body)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 1000)
    assert res is not None and res['error']


@needs_native
def test_crom_zip_bomb_rejected():
    """头部声明 1KB, 实际解出 1MB: 必须拒绝 (旧实现无上限地解压)。"""
    eng = native.get_engine()
    raw = b'\x00' * (1 << 20)
    payload = zlib.compress(raw)
    header = b'CROM' + bytes([3]) + struct.pack('<I', 1024) + \
        bytes([0x01]) + struct.pack('<I', zlib.crc32(payload)) + b'\x00\x00'
    blob = header + payload
    assert eng.crom_unpack(blob) is None


@needs_native
def test_crom_mem_size_mismatch_rejected():
    """头部 mem_size 与实际载荷长度不符时必须拒绝。"""
    eng = native.get_engine()
    raw = b'\x00' * 2048
    payload = zlib.compress(raw)
    header = b'CROM' + bytes([3]) + struct.pack('<I', 4096) + \
        bytes([0x01]) + struct.pack('<I', zlib.crc32(payload)) + b'\x00\x00'
    assert eng.crom_unpack(header + payload) is None


@needs_native
def test_crom_roundtrip_still_works():
    """正常 CROM 打包/解包必须仍然可用 (防止加固把合法路径也拒了)。"""
    eng = native.get_engine()
    data = bytes(range(256)) * 8
    packed = eng.crom_pack(data, True)
    assert packed is not None
    assert eng.crom_unpack(packed) == data


@needs_native
def test_step_limit_is_an_error_not_silent_success():
    """步数用尽必须报错误 (旧实现返回 StatusDone, 与 HALT 无法区分)。"""
    from codecin.isa import KIND_IMM, Opcode

    eng = native.get_engine()
    # 单条 `JMP 0` 自跳转 = 死循环
    body = bytes([Opcode.JMP.value, 1, KIND_IMM]) + \
        struct.pack('<q', 0) + struct.pack('<q', 0)
    bc = _bc(count=1, instructions=body)
    res = eng.run(bc, b'\x00' * 4096, 0, 4096, 2048, b'', 500)
    assert res is not None
    assert res['steps'] <= 501
    assert res['error'], '步数用尽必须是错误, 不能伪装成正常结束'
    assert 'limit' in res['error'].lower()


# ---------------- Python 侧 (zlib 回退路径) 的同类加固 ----------------

def _write(tmp_path, blob):
    p = tmp_path / 'x.crom'
    p.write_bytes(blob)
    return str(p)


def test_python_crom_zip_bomb_rejected(tmp_path):
    """Python 回退路径同样必须限制解压大小 (旧实现 zlib.decompress 无上限)。"""
    from codecin.crom import load_crom
    from codecin.errors import CPUSimulatorError
    from codecin.memory import FastMemory

    raw = b'\x00' * (1 << 20)
    payload = zlib.compress(raw)
    header = b'CROM' + bytes([3]) + struct.pack('<I', 1024) + \
        bytes([0x01]) + struct.pack('<I', zlib.crc32(payload)) + b'\x00\x00'
    path = _write(tmp_path, header + payload)
    with pytest.raises(CPUSimulatorError):
        load_crom(FastMemory(4096), path)


def test_python_crom_garbage_rejected(tmp_path):
    """非 CROM 文件不再被静默当作旧版内存镜像载入。"""
    from codecin.crom import load_crom
    from codecin.errors import CPUSimulatorError
    from codecin.memory import FastMemory

    path = _write(tmp_path, b'NOTACROMFILE' + b'\x00' * 64)
    with pytest.raises(CPUSimulatorError):
        load_crom(FastMemory(4096), path)
