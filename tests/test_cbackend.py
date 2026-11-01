"""AOT C 后端: 生成 C / 交叉编译 / 与 VM 行为一致。"""

import os
import subprocess

import pytest

from codecin import aot, cbackend, cli, sdgcc, toolchain
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


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_builtin_syscalls_match_vm(workdir, capsys):
    """abs/sqrt/substr/upper/strlen/atoi/floor/round 与 VM 逐字节一致。"""
    src = os.path.join(workdir, 'builtins.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(abs(-42))\n'
                '    println(sqrt(16.0))\n'
                '    println(substr("hello world", 6, 5))\n'
                '    println(upper("abc"))\n'
                '    println(strlen("hello"))\n'
                '    println(atoi(" 123 "))\n'
                '    println(floor(3.7))\n'
                '    println(round(3.5))\n'
                '    return 0\n'
                '}\n')
    assert cli.main([src, '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    built = aot.build_program(src, out=os.path.join(workdir, 'builtins'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert expected.splitlines()[:3] == ['42', '4', 'world']


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_file_syscalls_match_vm(workdir, capsys, monkeypatch):
    """file_* 系列 (51-57) 与 VM 行为一致: 返回值/内容/删除。"""
    program = ('function main() -> int {\n'
               '    file_write("data.txt", "hello")\n'
               '    println(file_exists("data.txt"))\n'
               '    println(file_size("data.txt"))\n'
               '    println(file_read("data.txt"))\n'
               '    file_append("data.txt", " world")\n'
               '    println(file_read("data.txt"))\n'
               '    println(file_delete("data.txt"))\n'
               '    println(file_exists("data.txt"))\n'
               '    return 0\n'
               '}\n')
    vm_dir = os.path.join(workdir, 'vm')
    aot_dir = os.path.join(workdir, 'aot')
    os.makedirs(vm_dir)
    os.makedirs(aot_dir)
    for target in (vm_dir, aot_dir):
        with open(os.path.join(target, 'fs.cin'), 'w', encoding='utf-8') as f:
            f.write(program)

    monkeypatch.chdir(vm_dir)
    assert cli.main(['fs.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    monkeypatch.chdir(workdir)

    built = aot.build_program(os.path.join(aot_dir, 'fs.cin'),
                              out=os.path.join(aot_dir, 'fs'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=aot_dir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert expected.splitlines() == ['1', '5', 'hello', 'hello world',
                                     '0', '0']
    assert not os.path.exists(os.path.join(aot_dir, 'data.txt'))


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_system_info_syscalls_match_vm(workdir, capsys, monkeypatch):
    """getenv/hostname/username/cwd (61,64,65,66) 与 VM 一致。"""
    program = ('function main() -> int {\n'
               '    println(cwd())\n'
               '    println(getenv("CIN_AOT_TEST"))\n'
               '    println(hostname())\n'
               '    println(username())\n'
               '    return 0\n'
               '}\n')
    monkeypatch.setenv('CIN_AOT_TEST', 'hello-env')
    src = os.path.join(workdir, 'sys.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)

    monkeypatch.chdir(workdir)
    assert cli.main(['sys.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    monkeypatch.chdir(os.path.dirname(workdir))

    built = aot.build_program(src, out=os.path.join(workdir, 'sysinfo'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert 'hello-env' in expected.splitlines()


def test_dirlist_and_env_runtime_symbols_are_emitted():
    source = cbackend.emit_c(
        [('SYS', [('imm', 58)]), ('SYS', [('imm', 61)]), ('HALT', [])],
        {}, [], 0, 1 << 20, (1 << 20) - 8, 1 << 19)
    for symbol in ('rt_dirlist', 'rt_getenv', 'rt_osname', 'rt_hostname',
                   'rt_cwd', 'rt_homedir'):
        assert symbol in source


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_dns_lookup_matches_vm(workdir, capsys, monkeypatch):
    src = os.path.join(workdir, 'dns.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(dns_lookup("localhost"))\n'
                '    return 0\n'
                '}\n')
    monkeypatch.chdir(workdir)
    assert cli.main(['dns.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.strip().startswith('127.')

    built = aot.build_program(src, out=os.path.join(workdir, 'dnsprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_tcp_echo_matches_vm(workdir, capsys, monkeypatch):
    """TCP dial/send/recv/close 与 VM 行为一致（本地 echo 服务器）。"""
    import socket
    import threading

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(4)
    server.settimeout(30)
    port = server.getsockname()[1]
    received = []

    def serve():
        try:
            for _ in range(2):
                conn, _addr = server.accept()
                data = conn.recv(64)
                received.append(data)
                conn.sendall(data)
                conn.close()
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    src = os.path.join(workdir, 'tcp.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                f'    int fd = tcp_dial("127.0.0.1", {port})\n'
                '    if (fd < 0) { println("dial_fail") return 1 }\n'
                '    int buf[64]\n'
                '    int sent = tcp_send(fd, "ping", 4)\n'
                '    int n = tcp_recv(fd, buf, 64)\n'
                '    println(sent)\n'
                '    println(n)\n'
                '    tcp_close(fd)\n'
                '    return 0\n'
                '}\n')
    monkeypatch.chdir(workdir)
    assert cli.main(['tcp.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out

    built = aot.build_program(src, out=os.path.join(workdir, 'tcpprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    thread.join(10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected == '4\n4\n'
    assert received == [b'ping', b'ping']


def test_supports_reports_missing_opcode_and_syscall():
    ok, reason = cbackend.supports([('MOV', [('reg', 0), ('imm', 1)]),
                                    ('HALT', [])])
    assert ok, reason
    ok, reason = cbackend.supports([('SYS', [('imm', 59)]), ('HALT', [])])
    assert not ok and 'SYS 59' in reason
    ok, reason = cbackend.supports([('NOP', []), ('HALT', [])])
    assert not ok and 'NOP' in reason


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_aot_build_program_uses_c_backend(workdir, capsys, monkeypatch):
    monkeypatch.setenv('CODECIN_AOT_CACHE', os.path.join(workdir, 'cache'))
    src = os.path.join(workdir, 'loop.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(LOOP)
    assert cli.main([src, '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    built = aot.build_program(src, out=os.path.join(workdir, 'aotprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected == '15\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_aot_accepts_sdgcc_input(workdir):
    src = os.path.join(workdir, 'loop.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(LOOP)
    cpu = CPU(Config(log_level='ERROR'), src)
    pack = os.path.join(workdir, 'loop.sdgcc')
    sdgcc.save_program(cpu, pack, multi_file=False)
    built = aot.build_program(pack, out=os.path.join(workdir, 'fromsg'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '15\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_aot_c_backend_rejects_unsupported_program(workdir):
    asm = os.path.join(workdir, 'unsupported.asm')
    with open(asm, 'w', encoding='utf-8') as f:
        f.write('SYS 59\nHALT\n')
    with pytest.raises(aot.AotError) as info:
        aot.build_program(asm, out=os.path.join(workdir, 'bad'),
                          backend='c', use_cache=False)
    assert 'SYS 59' in str(info.value)
