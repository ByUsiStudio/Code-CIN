"""AOT C 后端: 生成 C / 交叉编译 / 与 VM 行为一致。"""

import os
import subprocess

import pytest

from codecin import cbackend, cli, toolchain
from codecin.config import Config
from codecin.cpu import CPU

LOOP = ('function add(int a, int b) -> int { return a + b }\n'
        'function main() -> int {\n'
        '    int x = 0\n'
        '    int i = 1\n'
        '    while (i <= 5) { x = add(x, i) i = i + 1 }\n'
        '    println(x)\n'
        '    return 0\n'
        '}\n')


def _compiler():
    try:
        spec = toolchain.get_target(cbackend._host_target_name())
    except toolchain.ToolchainError:
        return None
    return toolchain.find_compiler(spec)


def test_emit_c_has_labels_and_runtime():
    source = cbackend.emit_c(
        [('MOV', [('reg', 0), ('imm', 1)]),
         ('HALT', [])],
        {}, [], 0, 1 << 20, (1 << 20) - 8, 1 << 19)
    assert 'L0:;' in source and 'L1:;' in source
    assert 'int main(void)' in source
    assert 'rt_init(UINT64_C(1048576)' in source


def test_unsupported_syscall_is_reported_at_runtime():
    source = cbackend.emit_c(
        [('SYS', [('imm', 59)]), ('HALT', [])],
        {}, [], 0, 1 << 20, (1 << 20) - 8, 1 << 19)
    assert 'SYS 59 未在 C 后端实现' in source


def test_missing_compiler_gives_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setenv('CODECIN_CC_X86_64_WINDOWS', str(tmp_path / 'nope'))
    monkeypatch.setattr(cbackend.toolchain, 'find_compiler', lambda spec: None)
    with pytest.raises(toolchain.ToolchainError) as info:
        cbackend.compile_instructions([], {}, [], 0, 4096, 4088, 2048,
                                      str(tmp_path / 'out.exe'),
                                      target='x86_64-windows')
    assert 'CODECIN_CC' in str(info.value)


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_compiled_program_matches_vm(workdir, capsys):
    src = os.path.join(workdir, 'loop.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(LOOP)
    assert cli.main([src, '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out

    cpu = CPU(Config(log_level='ERROR'), src)
    out = os.path.join(workdir, 'loop' + ('.exe' if os.name == 'nt' else ''))
    built = cbackend.compile_instructions(
        cpu.instructions, cpu.labels, cpu.memory.snapshot_segments(),
        cpu.entry_pc, len(cpu.memory), cpu.sp, cpu.heap_base, out,
        optimize=2)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected == '15\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_runtime_error_path_sets_exit_code(workdir):
    """越界访问必须显式失败 (退出码 1), 而不是静默算错。"""
    src = os.path.join(workdir, 'bad.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    int p = 0\n'
                '    pointer q = p\n'
                '    return 0\n'
                '}\n')
    # 直接构造越界 STORE, 避免依赖 CIN 指针语法
    cpu = CPU(Config(log_level='ERROR'), _write_asm(workdir))
    out = os.path.join(workdir, 'bad' + ('.exe' if os.name == 'nt' else ''))
    built = cbackend.compile_instructions(
        cpu.instructions, cpu.labels, cpu.memory.snapshot_segments(),
        cpu.entry_pc, 4096, 4088, 2048, out, optimize=1)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 1
    assert 'out of bounds' in result.stderr.lower()


def _write_asm(workdir):
    path = os.path.join(workdir, 'oob.asm')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('MOV r0, 4090\n'
                'SD r0, r0\n'
                'HALT\n')
    return path
