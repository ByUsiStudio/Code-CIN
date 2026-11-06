"""直接 AST → C 原生后端（不经过 UCPU 指令集）的验收用例。"""

import os
import struct
import subprocess

import pytest

from codecin import cli, mkn, nativegen, toolchain

LOOP = ('function add(int a, int b) -> int { return a + b }\n'
        'function main() -> int {\n'
        '    int s = 0\n'
        '    int i = 1\n'
        '    while (i <= 5) { s = add(s, i) i = i + 1 }\n'
        '    println(s)\n'
        '    return 0\n'
        '}\n')


def _compiler():
    from codecin.cbackend import _host_target_name
    try:
        spec = toolchain.get_target(_host_target_name())
    except toolchain.ToolchainError:
        return None
    return toolchain.find_compiler(spec) or toolchain.find_zig()


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cli_native_runs_without_isa(workdir, capfd):
    src = os.path.join(workdir, 'loop.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(LOOP)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '15\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cli_native_out_builds_only(workdir):
    src = os.path.join(workdir, 'loop.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(LOOP)
    out = os.path.join(workdir, 'built-native')
    assert cli.main([src, '--native-out', out, '--log-level', 'ERROR']) == 0
    built = out + ('.exe' if os.name == 'nt' else '')
    assert os.path.isfile(built)
    assert subprocess.run([built], capture_output=True,
                          text=True).stdout == '15\n'


def test_cli_native_reports_unsupported(workdir, capsys):
    src = os.path.join(workdir, 'bad.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int { float f = 1.5 return 0 }')
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 1
    assert 'Native Build Error' in capsys.readouterr().out


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_mkn_build_native(workdir, monkeypatch):
    root = os.path.join(workdir, 'proj')
    os.makedirs(os.path.join(root, 'src'))
    with open(os.path.join(root, 'mkn.toml'), 'w', encoding='utf-8') as f:
        f.write('[project]\nname = "app"\nversion = "0.1.0"\n'
                'entry = "src/main.cin"\n')
    with open(os.path.join(root, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function main() -> int {\n    println(7 * 6)\n    return 0\n}\n')
    monkeypatch.chdir(root)
    assert mkn.main(['build', '--native']) == 0
    suffix = '.exe' if os.name == 'nt' else ''
    built = os.path.join(root, 'target', 'app-0.1.0' + suffix)
    assert os.path.isfile(built)
    assert subprocess.run([built], capture_output=True,
                          text=True).stdout == '42\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_if_bool_string_coverage(workdir):
    src = ('function main() -> int {\n'
           '    int x = 3\n'
           '    if (x > 2) { println("big") } else { println("small") }\n'
           '    bool ok = x == 3\n'
           '    println(ok)\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'ifprog'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == 'big\ntrue\n'


def test_string_in_expression_is_rejected():
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function main() -> int {\n'
                         '    int x = "s"\n'
                         '    return 0\n'
                         '}\n')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_arrays_and_for_loop(workdir):
    src = ('function main() -> int {\n'
           '    int a[3]\n'
           '    for (int i = 0; i < 3; i = i + 1) {\n'
           '        a[i] = i * 2\n'
           '        if (i == 1) { continue }\n'
           '        if (i == 2) { break }\n'
           '    }\n'
           '    println(a[0])\n'
           '    println(a[1])\n'
           '    println(a[2])\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'arrprog'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == '0\n2\n4\n'


def test_emit_c_contains_no_isa_artifacts():
    source = nativegen.emit_c(LOOP)
    assert '#include <stdio.h>' in source
    assert 'fn_main' in source and 'fn_add' in source
    # 不含任何 UCPU/BIN 痕迹
    assert 'CPUSA' not in source and 'UCBC' not in source
    assert 'r0' not in source and 'R[' not in source


def test_unsupported_constructs_are_rejected():
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function main() -> int { string s = "x" return 0 }')
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function main() -> int { float f = 1.5 return 0 }')
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function helper() -> int { return 1 }')
    assert not nativegen.supports_source('function main() -> int { float f = 1.5 return 0 }')
    assert nativegen.supports_source(LOOP)


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_native_pipeline_runs_without_bytecode(workdir):
    out = nativegen.compile_native(LOOP, os.path.join(workdir, 'native'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '15\n'


@pytest.mark.skipif(toolchain.find_zig() is None, reason='未安装 Zig')
def test_native_cross_targets_all_devices(workdir):
    """同一份源码直接产出各设备本机的 ELF（x86-64/arm64/arm32/riscv64）。"""
    expected = {
        'x86_64-linux': 0x3E,
        'aarch64-linux': 0xB7,
        'arm-linux': 0x28,
        'riscv64-linux': 0xF3,
    }
    for target, machine in expected.items():
        out = nativegen.compile_native(LOOP, os.path.join(workdir, target),
                                       target=target)
        data = open(out, 'rb').read()
        assert data[:4] == b'\x7fELF', target
        assert struct.unpack_from('<H', data, 18)[0] == machine, target
