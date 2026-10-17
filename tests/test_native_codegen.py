"""native 代码生成与 IR 优化器测试。"""

import contextlib
import io
import os
import shutil
import subprocess

import pytest

from codecin import CPU, Config
from codecin.cin import CINCompiler
from codecin.native_codegen import generate_go_source
from codecin.optimizer import optimize

needs_go = pytest.mark.skipif(shutil.which('go') is None,
                              reason='未安装 Go 工具链')

PROGRAM = '''
function fact(int n) -> int {
    if (n <= 1) { return 1 }
    return n * fact(n - 1)
}

function main() -> int {
    int s = 0
    for (int i = 1; i <= 5; i = i + 1) {
        s = s + fact(i)
    }
    println("s=" + int_to_str(s))
    return 0
}'''


def _write(workdir, name, text):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _run_cpu(path, optimize_level):
    cpu = CPU(Config(log_level='ERROR', optimize=optimize_level), path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cpu.run()
    assert not cpu.execution_failed
    return buf.getvalue()


def test_native_codegen_has_no_vm_dispatch():
    """生成源码必须直接调用 NativeRuntime, 不能再走 UCBC/VM 解释循环。"""
    res = CINCompiler().compile_source(PROGRAM)
    src = generate_go_source(res.instructions, res.labels, [],
                             mem_size=1 << 20, max_steps=100000)
    assert 'codecin-native/engine' in src
    assert 'engine.NewNativeRuntime' in src
    assert 'RunV2' not in src
    assert 'program.ucbc' not in src
    # 核心算术/控制流应已展开成直接调用
    assert 'rt.Mov(' in src or 'rt.AddTo(' in src
    assert 'goto Ldispatch' in src


def test_native_codegen_asr_operand_forms():
    """三操作数 ASR rd, rn, shift 不能被当成两操作数 Base ASR。"""
    three = [('ASR', [('reg', 0), ('reg', 1), ('reg', 2)]), ('HALT', [])]
    src = generate_go_source(three, {}, [], mem_size=4096)
    assert 'rt.AsrFrom(0, 1, rt.Reg(2))' in src

    two = [('ASR', [('reg', 0), ('reg', 1)]), ('HALT', [])]
    src = generate_go_source(two, {}, [], mem_size=4096)
    assert 'rt.Asr(0, rt.Reg(1))' in src


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_optimizer_levels_preserve_behavior(workdir, level):
    src = _write(workdir, 'opt.cin', PROGRAM)
    out = _run_cpu(src, level)
    assert out == _run_cpu(src, 0)
    assert 's=153' in out


def test_optimizer_removes_nops_and_remaps_labels():
    ins = [
        ('MOV', [('reg', 0), ('imm', 1)]),
        ('NOP', []),
        ('JMP', [('label', 'end')]),
        ('HALT', []),
    ]
    labels = {'end': 3}
    new_ins, new_labels, entry = optimize(ins, labels, level=1)
    assert entry == 0
    # NOP 被删；JMP 的目标就是下一条，也被删；HALT 重映射到下标 1。
    assert new_labels['end'] == 1
    assert all(op not in ('NOP', 'JMP') for op, _ in new_ins)
    assert len(new_ins) == 2


def test_optimizer_constant_folding():
    ins = [
        ('MOV', [('reg', 0), ('imm', 10)]),
        ('ADD', [('reg', 0), ('imm', 5)]),
        ('MOV', [('reg', 2), ('imm', 20)]),
        ('ADDI', [('reg', 3), ('reg', 2), ('imm', 2)]),
        ('HALT', []),
    ]
    new_ins, _, _ = optimize(ins, {}, level=2)
    assert ('MOV', [('reg', 0), ('imm', 15)]) in new_ins
    assert ('MOV', [('reg', 3), ('imm', 22)]) in new_ins
    assert new_ins[-1] == ('HALT', [])
    assert len(new_ins) == 4


@pytest.mark.parametrize(('op', 'args', 'needle'), [
    ('ADDS', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.AddS(0, 1, rt.Reg(2))'),
    ('SUBS', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.SubS(0, 1, rt.Reg(2))'),
    ('ADDC', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.AddC(0, 1, rt.Reg(2))'),
    ('SUBC', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.SubC(0, 1, rt.Reg(2))'),
    ('ROR', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.Ror(0, 1, rt.Reg(2))'),
    ('EOR', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.Eor(0, 1, rt.Reg(2))'),
    ('BIC', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.Bic(0, 1, rt.Reg(2))'),
    ('ORN', [('reg', 0), ('reg', 1), ('reg', 2)], 'rt.Orn(0, 1, rt.Reg(2))'),
    ('LDP', [('reg', 0), ('reg', 1), ('mem', 0, 0)], 'rt.Ldp(0, 1, rt.Addr(0, 0))'),
    ('STP', [('reg', 0), ('reg', 1), ('mem', 0, 0)], 'rt.Stp(0, 1, rt.Addr(0, 0))'),
    ('CSEL', [('reg', 0), ('reg', 1), ('reg', 2), ('cond', 'EQ')],
     'rt.CondSelect("CSEL", 0, 1, rt.Reg(2), 0)'),
    ('SXTB', [('reg', 0), ('reg', 1)], 'rt.SignExtend(0, rt.Reg(1), 8)'),
    ('CLZ', [('reg', 0), ('reg', 1)], 'rt.Clz(0, rt.Reg(1))'),
    ('SLTI', [('reg', 0), ('reg', 1), ('imm', 5)],
     'rt.SetLessImm(0, 1, uint64(0x5), true)'),
    ('SLLI', [('reg', 0), ('reg', 1), ('imm', 3)],
     'rt.LslFrom(0, 1, uint64(0x3))'),
    ('LUI', [('reg', 0), ('imm', 0x12345)], 'rt.Lui(0, uint64(0x12345))'),
    ('FADD', [('vec', 0), ('vec', 1), ('vec', 2)], 'rt.FAdd(0, 1, 2)'),
    ('FDIV', [('vec', 0), ('vec', 1), ('vec', 2)], 'rt.FDiv(0, 1, 2)'),
    ('FCMP', [('vec', 0), ('vec', 1)], 'rt.FCmp(0, 1)'),
    ('FCVT', [('reg', 0), ('vec', 1)], 'rt.FCvtToInt(0, 1, 0)'),
    ('FCVT', [('vec', 0), ('reg', 1)], 'rt.FCvtToFloat(0, 1)'),
    ('FABS', [('vec', 0), ('vec', 1)], 'rt.FAbs(0, 1)'),
    ('LDRS', [('vec', 0), ('mem', 0, 0)], 'rt.LoadF32(0, rt.Addr(0, 0))'),
    ('VADD', [('vec', 0), ('vec', 1), ('vec', 2)], 'rt.VOp("VADD", 0, 1, 2)'),
    ('VLD1', [('vec', 0), ('mem', 0, 0)], 'rt.VLoad1(0, rt.Addr(0, 0))'),
])
def test_native_codegen_direct_extended_ops(op, args, needle):
    source = generate_go_source([(op, args), ('HALT', [])], {}, [],
                                mem_size=4096)
    assert needle in source


def test_resolve_backend_auto_threshold():
    from codecin import aot
    assert aot.resolve_backend('auto', 100) == 'native'
    assert aot.resolve_backend('auto', aot.NATIVE_INSTRUCTION_LIMIT) == 'native'
    assert aot.resolve_backend('auto', aot.NATIVE_INSTRUCTION_LIMIT + 1) == 'vm'
    assert aot.resolve_backend('native', 10 ** 6) == 'native'
    assert aot.resolve_backend('vm', 10) == 'vm'


def test_native_codegen_block_mode_source():
    """大程序基本块模式: 无 goto/Ldispatch, 每个基本块独立函数。"""
    res = CINCompiler().compile_source(PROGRAM)
    src = generate_go_source(res.instructions, res.labels, [],
                             mem_size=1 << 20, max_steps=100000,
                             block_mode=True)
    assert 'func block' in src
    assert 'goto L' not in src
    assert 'Ldispatch' not in src
    assert 'switch pc {' in src


@needs_go
def test_native_aot_block_mode_end_to_end(workdir):
    from codecin import aot
    res = CINCompiler().compile_source(PROGRAM)
    segs = []
    if res.data_writes:
        end = max(addr + len(data) for addr, data in res.data_writes)
        buf = bytearray(end)
        for addr, data in res.data_writes:
            buf[addr:addr + len(data)] = data
        segs.append((0, bytes(buf)))
    src = generate_go_source(res.instructions, res.labels, segs,
                             mem_size=1 << 20, max_steps=100000,
                             block_mode=True, program_name='block.cin')
    out = os.path.join(workdir, 'block' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_native(src, out)
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    assert r.returncode == 0, r.stderr
    assert 's=153' in r.stdout


@needs_go
def test_native_aot_optimized_end_to_end(workdir):
    """-O3 的 native AOT 产物与 -O0 输出一致, 且可直接运行。"""
    from codecin import aot
    src = _write(workdir, 'opt_aot.cin', PROGRAM)
    out = os.path.join(workdir, 'opt_aot' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_program(src, out=out, backend='native', optimize=3)
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    assert r.returncode == 0, r.stderr
    assert 's=153' in r.stdout
