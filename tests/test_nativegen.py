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


SIMPLE = ('function main() -> int {\n'
          '    int a = 7\n'
          '    int b = 3\n'
          '    println(a * b + a % b)\n'
          '    return 0\n'
          '}\n')


CASE = ('function main() -> int {\n'
        '    println(upper("ab cd"))\n'
        '    println(lower("XY Z"))\n'
        '    return 0\n'
        '}\n')


CONV = ('function main() -> int {\n'
        '    println(atoi("123") + 1)\n'
        '    println(atoi("-45"))\n'
        '    println(true)\n'
        '    return 0\n'
        '}\n')


IDX = ('function main() -> int {\n'
       '    println(indexof("hello world", "world"))\n'
       '    println(indexof("hello", "zz"))\n'
       '    return 0\n'
       '}\n')


RAND = ('function main() -> int {\n'
        '    srand(7)\n'
        '    println(rand())\n'
        '    srand(7)\n'
        '    println(rand())\n'
        '    println(time() > 0)\n'
        '    return 0\n'
        '}\n')


TRIM = ('function main() -> int {\n'
        '    println("[" + trim("  x  ") + "]")\n'
        '    println("[" + ltrim("  y  ") + "]")\n'
        '    println("[" + rtrim("  z  ") + "]")\n'
        '    return 0\n'
        '}\n')


FILEIO = ('function main() -> int {\n'
          '    file_write("/tmp/cin_io_test.txt", "hello")\n'
          '    println(file_read("/tmp/cin_io_test.txt"))\n'
          '    return 0\n'
          '}\n')


FS = ('function main() -> int {\n'
      '    file_write("/tmp/cin_fs_test.txt", "abc")\n'
      '    println(file_exists("/tmp/cin_fs_test.txt"))\n'
      '    println(file_size("/tmp/cin_fs_test.txt"))\n'
      '    file_delete("/tmp/cin_fs_test.txt")\n'
      '    println(file_exists("/tmp/cin_fs_test.txt"))\n'
      '    return 0\n'
      '}\n')


NET = ('function main() -> int {\n'
      '    int fd = tcp_dial("127.0.0.1", 46123)\n'
      '    tcp_send(fd, "ping", 4)\n'
      '    int buf[64]\n'
      '    int n = tcp_recv(fd, buf, 64)\n'
      '    println(n)\n'
      '    println(buf[0])\n'
      '    tcp_close(fd)\n'
      '    return 0\n'
      '}\n')

SRV = ('import socket\n'
       's = socket.socket()\n'
       's.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n'
       's.bind(("127.0.0.1", 46123))\n'
       's.listen(1)\n'
       'c, _ = s.accept()\n'
       'd = c.recv(64)\n'
       'c.sendall(d.upper())\n'
       'c.close()\n'
       's.close()\n')


UDP = ('function main() -> int {\n'
      '    int fd = udp_open(0)\n'
      '    int sent = udp_sendto(fd, "127.0.0.1", 46203, "ping", 4)\n'
      '    int buf[64]\n'
      '    int n = udp_recvfrom(fd, buf, 64, 0)\n'
      '    println(sent)\n'
      '    println(n)\n'
      '    println(buf[0])\n'
      '    udp_close(fd)\n'
      '    return 0\n'
      '}\n')

UDP_SRV = ('import socket\n'
           's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
           's.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n'
           's.bind(("127.0.0.1", 46203))\n'
           'data, addr = s.recvfrom(64)\n'
           's.sendto(data.upper(), addr)\n'
           's.close()\n')


@pytest.mark.skipif(os.name != "nt", reason="需要 WSL 运行 ELF")
def test_cincc_udp_against_echo_server(workdir):
    import shutil
    import subprocess

    wsl = shutil.which("wsl")
    if not wsl:
        pytest.skip("WSL 不可用")
    src = os.path.join(workdir, "udp.cin")
    with open(src, "w", encoding="utf-8") as f:
        f.write(UDP)
    with open(os.path.join(workdir, "srv.py"), "w", encoding="utf-8") as f:
        f.write(UDP_SRV)
    built = os.path.join(workdir, "udpapp")
    assert cli.main([src, "--native-out", built, "--log-level", "ERROR"]) == 0
    base = "/mnt/" + built[0].lower() + built[2:].replace("\\", "/")
    d = base.rsplit("/", 1)[0]
    script = (f"cd '{d}' && (python3 srv.py &) ; sleep 1; timeout 5 '{base}'; "
              f"echo rc=$?")
    out = subprocess.run([wsl, "-e", "sh", "-c", script],
                         capture_output=True, text=True)
    assert "1196312912" in out.stdout


@pytest.mark.skipif(os.name != "nt", reason="需要 WSL 运行 ELF")
def test_cincc_tcp_against_echo_server(workdir):
    import shutil
    import subprocess

    wsl = shutil.which("wsl")
    if not wsl:
        pytest.skip("WSL 不可用")
    src = os.path.join(workdir, "net.cin")
    with open(src, "w", encoding="utf-8") as f:
        f.write(NET)
    with open(os.path.join(workdir, "srv.py"), "w", encoding="utf-8") as f:
        f.write(SRV)
    built = os.path.join(workdir, "netapp")
    assert cli.main([src, "--native-out", built, "--log-level", "ERROR"]) == 0
    base = "/mnt/" + built[0].lower() + built[2:].replace("\\", "/")
    d = base.rsplit("/", 1)[0]
    script = (f"cd '{d}' && (python3 srv.py &) ; sleep 1; "
              f"'{base}'; echo rc=$?")
    out = subprocess.run([wsl, "-e", "sh", "-c", script],
                         capture_output=True, text=True)
    assert "4" in out.stdout
    assert "1196312912" in out.stdout


SYSCALLS = ('function main() -> int {\n'
            '    int t0 = time_ms()\n'
            '    sleep_ms(120)\n'
            '    println(time_ms() - t0 >= 100)\n'
            '    chdir("/tmp")\n'
            '    println(cwd() == "/tmp")\n'
            '    return 0\n'
            '}\n')


OSINFO = ('function main() -> int {\n'
          '    println(os_name())\n'
          '    println(temp_dir())\n'
          '    return 0\n'
          '}\n')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_os_info(workdir, capfd):
    src = os.path.join(workdir, 'os.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(OSINFO)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'linux\n/tmp\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_time_and_dir_syscalls(workdir, capfd):
    src = os.path.join(workdir, 'sys.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(SYSCALLS)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'true\ntrue\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_file_metadata(workdir, capfd):
    src = os.path.join(workdir, 'fs.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(FS)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '1\n3\n0\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_file_io(workdir, capfd):
    src = os.path.join(workdir, 'fio.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(FILEIO)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'hello\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_trim_family(workdir, capfd):
    src = os.path.join(workdir, 'trim.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(TRIM)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '[x]\n[y  ]\n[  z]\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_time_and_rand(workdir, capfd):
    src = os.path.join(workdir, 'rnd.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(RAND)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    lines = capfd.readouterr().out.replace('\r\n', '\n').split('\n')
    assert lines[0] == lines[1]
    assert int(lines[0]) >= 0
    assert lines[2] == 'true'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_indexof(workdir, capfd):
    src = os.path.join(workdir, 'idx.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(IDX)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '6\n-1\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_atoi_and_bool(workdir, capfd):
    src = os.path.join(workdir, 'conv.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(CONV)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '124\n-45\ntrue\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_case_conversion(workdir, capfd):
    src = os.path.join(workdir, 'case.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(CASE)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'AB CD\nxy z\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cli_native_runs_via_cincc(workdir, capfd):
    src = os.path.join(workdir, 'simple.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(SIMPLE)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '22\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cli_native_out_builds_only(workdir):
    src = os.path.join(workdir, 'simple.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(SIMPLE)
    out = os.path.join(workdir, 'built-native')
    assert cli.main([src, '--native-out', out, '--log-level', 'ERROR']) == 0
    assert os.path.isfile(out)


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_mkn_build_native_via_cincc(workdir, monkeypatch):
    import shutil
    import subprocess

    root = os.path.join(workdir, 'proj')
    os.makedirs(os.path.join(root, 'src'))
    with open(os.path.join(root, 'mkn.toml'), 'w', encoding='utf-8') as f:
        f.write('[project]\nname = "app"\nversion = "0.1.0"\n'
                'entry = "src/main.cin"\n')
    with open(os.path.join(root, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function main() -> int {\n    println(6 * 7)\n'
                '    return 0\n}\n')
    monkeypatch.chdir(root)
    assert mkn.main(['build', '--native']) == 0
    built = os.path.join(root, 'target', 'app-0.1.0')
    assert os.path.isfile(built)
    if os.name == 'nt':
        wsl = shutil.which('wsl')
        if not wsl:
            pytest.skip('Windows 且无 WSL, 无法运行 ELF')
        drive = built[0].lower()
        path = '/mnt/' + drive + built[2:].replace('\\', '/')
        out = subprocess.run([wsl, '-e', path], capture_output=True,
                             text=True)
        assert out.stdout.strip() == '42'


FUNCS = ('function add(int x, int y) -> int { return x + y }\n'
         'function main() -> int {\n'
         '    int s = 0\n'
         '    for (int i = 1; i <= 5; i = i + 1) { s = s + i }\n'
         '    if (s > 10) { println(add(s, 0)) } else { println(0) }\n'
         '    return 0\n'
         '}\n')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_functions_and_loops(workdir, capfd):
    src = os.path.join(workdir, 'funcs.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(FUNCS)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '15\n'


MIXED = ('struct P { int x int y }\n'
         'function main() -> int {\n'
         '    string a = "ab"\n'
         '    string b = "cd"\n'
         '    println(a + b)\n'
         '    P p\n'
         '    p.x = 3\n'
         '    p.y = 4\n'
         '    println(p.x + p.y)\n'
         '    return 0\n'
         '}\n')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_string_concat_and_struct(workdir, capfd):
    src = os.path.join(workdir, 'mixed.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(MIXED)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'abcd\n7\n'


STRS = ('function main() -> int {\n'
        '    println(substr("hello world", 6, 5))\n'
        '    println(strcmp("a", "b"))\n'
        '    return 0\n'
        '}\n')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_cincc_substr_and_strcmp(workdir, capfd):
    src = os.path.join(workdir, 'strs.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(STRS)
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == 'world\n-1\n'


def test_import_closure_multi_file(workdir, capfd):
    with open(os.path.join(workdir, 'util.cin'), 'w', encoding='utf-8') as f:
        f.write('function double_it(int x) -> int { return x * 2 }\n')
    src = os.path.join(workdir, 'main.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('import "./util.cin"\n'
                'function main() -> int {\n'
                '    println(double_it(21))\n'
                '    return 0\n'
                '}\n')
    assert cli.main([src, '--native', '--log-level', 'ERROR']) == 0
    assert capfd.readouterr().out.replace('\r\n', '\n') == '42\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_global_variables(workdir):
    src = ('int counter = 10\n'
           'string label = "n="\n'
           'function inc() -> int { counter = counter + 1 return counter }\n'
           'function main() -> int {\n'
           '    println(label + "x")\n'
           '    println(inc())\n'
           '    println(inc())\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'globprog'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == 'n=x\n11\n12\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_builtins_and_bitwise(workdir):
    src = ('function main() -> int {\n'
           '    println(strlen("hello"))\n'
           '    println(abs(-7))\n'
           '    println(indexof("hello world", "world"))\n'
           '    println(1 << 4)\n'
           '    println(6 & 3)\n'
           '    println(6 | 3)\n'
           '    println(6 ^ 3)\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'builtinprog'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == '5\n7\n6\n16\n2\n7\n5\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_array_parameter(workdir):
    src = ('function sum(int[] a, int n) -> int {\n'
           '    int s = 0\n'
           '    for (int i = 0; i < n; i = i + 1) { s = s + a[i] }\n'
           '    return s\n'
           '}\n'
           'function main() -> int {\n'
           '    int a[4]\n'
           '    a[0] = 1\n'
           '    a[1] = 2\n'
           '    a[2] = 3\n'
           '    a[3] = 4\n'
           '    println(sum(a, 4))\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'arrparam'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '10'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_string_builtins(workdir):
    src = ('function main() -> int {\n'
           '    println(substr("hello world", 0, 5))\n'
           '    println(upper("abc"))\n'
           '    println(lower("ABC"))\n'
           '    println(int_to_str(42))\n'
           '    println(strcmp("a", "b") < 0)\n'
           '    println(trim("  x  "))\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'strbuiltin'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == \
        'hello\nABC\nabc\n42\ntrue\nx\n'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_math_and_typed_arrays(workdir):
    src = ('function main() -> int {\n'
           '    println(pow(2.0, 3.0))\n'
           '    println(floor(3.7))\n'
           '    float xs[2]\n'
           '    xs[0] = 1.5\n'
           '    xs[1] = 2.5\n'
           '    string names[2]\n'
           '    names[0] = "a"\n'
           '    println(xs[1])\n'
           '    println(names[0])\n'
           '    return 0\n'
           '}\n')
    out = nativegen.compile_native(src, os.path.join(workdir, 'matharr'))
    result = subprocess.run([out], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace('\r\n', '\n') == '8\n3\n2.5\na\n'


def test_emit_c_contains_no_isa_artifacts():
    source = nativegen.emit_c(LOOP)
    assert '#include <stdio.h>' in source
    assert 'fn_main' in source and 'fn_add' in source
    # 不含任何 UCPU/BIN 痕迹
    assert 'CPUSA' not in source and 'UCBC' not in source
    assert 'r0' not in source and 'R[' not in source


def test_unsupported_constructs_are_rejected():
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function main() -> int { int[][] m return 0 }')
    with pytest.raises(nativegen.NativeGenError):
        nativegen.emit_c('function helper() -> int { return 1 }')
    assert not nativegen.supports_source(
        'function main() -> int { int[][] m return 0 }')
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
