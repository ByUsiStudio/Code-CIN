"""旧版 BIN v2 与新版内存布局的兼容性回归测试。

背景 (2026-10-18 issue): v5.9.0 之前默认 64 KiB 内存编译的 BIN v2 只固化
SP、不记录 heap_base。用 v6.0.0 默认 1 GiB 布局运行时, heap_base 变成
0x20000000, 而 SP 仍是 0xfff0 左右, 首条 PUSH/ALLOCFRAME 就会报
"Stack overflow (collides with heap)" —— 看起来像真实栈溢出, 实际是
加载时布局不兼容。现在必须在加载阶段报出可操作的错误。
"""

import os
import struct

import pytest

from codecin import Config, crom
from codecin.cpu import CPU
from codecin.disasm import disassemble_file
from codecin.errors import CPUSimulatorError
from codecin.isa import Constants
from codecin.native import encode_program, get_engine

needs_native = pytest.mark.skipif(get_engine() is None,
                                  reason='native library not built')


def _write(workdir, name, blob):
    path = os.path.join(workdir, name)
    with open(path, 'wb') as f:
        f.write(blob)
    return path


def _v2_blob(mem_size=64 * 1024, sp=None, bytecode=None):
    """构造一个完整可解析的 BIN v2 (整块 64 KiB 镜像 + 单条 HALT)。"""
    if sp is None:
        sp = (mem_size - Constants.STACK_SLOT) & ~0x7
    if bytecode is None:
        bytecode = encode_program([('HALT', [])], 0)
    header = (Constants.MAGIC_NUMBER
              + bytes([Constants.BIN_VERSION_V2])
              + struct.pack('<I', mem_size)
              + struct.pack('<I', 0)              # entry
              + struct.pack('<Q', sp)
              + struct.pack('<I', len(bytecode))
              + b'\x00' * 8)
    return header + bytes(mem_size) + bytecode


def _v3_blob(mem_size=64 * 1024, sp=None, heap_base=None, segs=b'',
             seg_count=0, bytecode=None):
    if sp is None:
        sp = (mem_size - Constants.STACK_SLOT) & ~0x7
    if heap_base is None:
        heap_base = mem_size // 2
    if bytecode is None:
        bytecode = encode_program([('HALT', [])], 0)
    header = (Constants.MAGIC_NUMBER
              + bytes([Constants.BIN_VERSION])
              + struct.pack('<Q', mem_size)
              + struct.pack('<I', 0)              # entry
              + struct.pack('<Q', sp)
              + struct.pack('<Q', heap_base)
              + struct.pack('<I', len(bytecode))
              + struct.pack('<I', seg_count)
              + b'\x00' * 8)
    return header + segs + bytecode


# ==================== BIN v2: 加载阶段检测布局不兼容 ====================

def test_v2_layout_mismatch_fails_at_load_with_actionable_message(workdir):
    path = _write(workdir, 'legacy.bin', _v2_blob())
    with pytest.raises(CPUSimulatorError) as ei:
        CPU(Config(log_level='ERROR'), path)
    msg = str(ei.value)
    # 指向真正原因, 而不是执行期容易误解的栈溢出
    assert 'BIN v2' in msg
    assert '65536' in msg
    assert '0x20000000' in msg
    assert '64 KB' in msg
    assert '格式可读不等于内存布局兼容' in msg
    assert '--mem-size 65536' in msg
    assert '重新编译' in msg
    assert 'Stack overflow (collides with heap)' in msg


def test_v2_layout_mismatch_is_not_a_runtime_stack_error(workdir, capsys):
    """CLI 必须在 Load Error 阶段退出, 不能进入执行后才报栈溢出。"""
    from codecin import cli
    path = _write(workdir, 'legacy_cli.bin', _v2_blob())
    code = cli.main([path, '--log-level', 'ERROR'])
    assert code == 1
    out = capsys.readouterr().out
    assert 'Load Error' in out
    assert '--mem-size 65536' in out
    assert 'Execution Error' not in out
    assert 'Total Instructions' not in out


def test_v2_with_matching_mem_size_uses_old_layout(workdir):
    path = _write(workdir, 'legacy_ok.bin', _v2_blob())
    cpu = CPU(Config(log_level='ERROR', mem_size=64 * 1024), path)
    assert cpu.sp == (64 * 1024 - 8) & ~0x7
    assert cpu.heap_base == 32 * 1024
    assert cpu.heap_ptr == 32 * 1024
    assert [op for op, _ in cpu.instructions] == ['HALT']


@needs_native
def test_v2_with_matching_mem_size_runs(workdir):
    path = _write(workdir, 'legacy_run.bin', _v2_blob())
    cpu = CPU(Config(log_level='ERROR', mem_size=64 * 1024), path)
    cpu.run()
    assert cpu.execution_failed is False
    assert cpu.pc == 1


@needs_native
def test_cli_runs_v2_bin_with_matching_mem_size(workdir):
    from codecin import cli
    path = _write(workdir, 'legacy_cli_ok.bin', _v2_blob())
    assert cli.main([path, '--mem-size', '65536', '--log-level', 'ERROR']) == 0


def test_v2_with_a_different_but_non_colliding_size_is_still_rejected(workdir):
    """BIN v2 的 heap_base / SP 约定随 mem_size 改变, 不能静默改变布局。"""
    path = _write(workdir, 'legacy_72k.bin', _v2_blob())
    with pytest.raises(CPUSimulatorError) as ei:
        CPU(Config(log_level='ERROR', mem_size=72 * 1024), path)
    msg = str(ei.value)
    assert '--mem-size 65536' in msg
    assert '改变字节码编译时的栈/堆边界' in msg


def test_v2_truncated_payload_is_reported_as_corruption(workdir):
    blob = _v2_blob()
    path = _write(workdir, 'truncated.bin', blob[:-4])
    with pytest.raises(CPUSimulatorError) as ei:
        CPU(Config(log_level='ERROR', mem_size=64 * 1024), path)
    assert '数据不完整' in str(ei.value)


# ==================== BIN v3: 头部元数据校验 ====================

def test_v3_invalid_heap_layout_fails_at_load(workdir):
    path = _write(workdir, 'bad_v3.bin',
                  _v3_blob(heap_base=64 * 1024 - 8))
    with pytest.raises(CPUSimulatorError) as ei:
        CPU(Config(log_level='ERROR', mem_size=64 * 1024), path)
    msg = str(ei.value)
    assert 'BIN v3' in msg
    assert 'heap_base' in msg


def test_v3_roundtrip_still_loads(workdir):
    path = _write(workdir, 'good_v3.bin', _v3_blob())
    cpu = CPU(Config(log_level='ERROR'), path)
    assert cpu.sp == (64 * 1024 - 8) & ~0x7
    assert cpu.heap_base == 32 * 1024
    assert [op for op, _ in cpu.instructions] == ['HALT']


# ==================== 反汇编: 只解码, 不执行, 不受布局校验影响 ====================

def test_disasm_still_reads_legacy_v2(workdir):
    path = _write(workdir, 'disasm_v2.bin', _v2_blob())
    lines = disassemble_file(path)
    assert 'CPUSA binary' in lines[0]
    assert any('HALT' in line for line in lines)


# ==================== 原生引擎: 直接调用时也不允许 SP < heap_base ====================

@needs_native
def test_engine_rejects_sp_below_heap_base_before_execution():
    eng = get_engine()
    bc = encode_program([('PUSH', [('reg', 29)])], 0)
    res = eng.run_v2(bc, [], 0,
                     0xfff8,        # 旧 64 KiB 布局的 SP
                     0x20000000,    # 新 1 GiB 布局的 heap_base
                     1 << 30, b'', 1000)
    assert res is not None and res['error']
    assert res['steps'] == 0
    assert 'initial stack layout' in res['error']
    assert '--mem-size' in res['error']
