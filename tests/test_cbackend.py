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
        [('SYS', [('imm', 39)]), ('HALT', [])],
        {}, [], 0, 1 << 20, (1 << 20) - 8, 1 << 19)
    assert 'SYS 39 未在 C 后端实现' in source


def test_missing_compiler_gives_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setenv('CODECIN_CC_X86_64_WINDOWS', str(tmp_path / 'nope'))
    monkeypatch.setattr(cbackend.toolchain, 'find_compiler', lambda spec: None)
    monkeypatch.setattr(cbackend.toolchain, 'find_zig', lambda: None)
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
                try:
                    conn, _addr = server.accept()
                except OSError:          # 超时/关闭: 直接退出, 不拖住测试
                    return
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


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_http_get_and_post_match_vm(workdir, capsys, monkeypatch):
    """http_get/http_post/http_code 与 VM 一致（本地 HTTP 服务）。"""
    import http.server
    import socketserver
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'hello-http'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            data = self.rfile.read(length)
            body = b'got:' + data
            self.send_response(201)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        src = os.path.join(workdir, 'http.cin')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('function main() -> int {\n'
                    f'    println(http_get("http://127.0.0.1:{port}/x"))\n'
                    '    println(http_code())\n'
                    f'    println(http_post("http://127.0.0.1:{port}/p", '
                    '"abc"))\n'
                    '    println(http_code())\n'
                    '    return 0\n'
                    '}\n')
        monkeypatch.chdir(workdir)
        assert cli.main(['http.cin', '--log-level', 'ERROR']) == 0
        expected = capsys.readouterr().out
        assert expected.splitlines() == ['hello-http', '200', 'got:abc',
                                         '201']

        built = aot.build_program(src, out=os.path.join(workdir, 'httpprog'),
                                  backend='c', use_cache=False)
        result = subprocess.run([built], capture_output=True, text=True,
                                timeout=60)
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_tcp_server_accept_matches_vm(workdir):
    """tcp_listen/tcp_accept 可以对外提供 TCP 服务，行为与 VM 一致。"""
    import socket
    import sys
    import time

    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    _first_port = port

    program = ('function main() -> int {\n'
               f'    int lfd = tcp_listen({port})\n'
               '    if (lfd < 0) { println("listen_fail") return 1 }\n'
               '    int fd = tcp_accept(lfd)\n'
               '    if (fd < 0) { println("accept_fail") return 1 }\n'
               '    int buf[64]\n'
               '    int n = tcp_recv(fd, buf, 64)\n'
               '    println(n)\n'
               '    tcp_send(fd, "pong", 4)\n'
               '    tcp_close(fd)\n'
               '    tcp_close(lfd)\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'server.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)

    def exercise(process_factory):
        proc = process_factory()
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    conn = socket.create_connection(('127.0.0.1', port),
                                                    timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                detail = ''
                if proc.poll() is not None:
                    detail = f' (进程已退出: {proc.stderr.read()[:200]!r})'
                raise AssertionError(f'server did not start on {port}{detail}')
            conn.sendall(b'ping')
            reply = conn.recv(64)
            conn.close()
            out, err = proc.communicate(timeout=30)
            return proc.returncode, out, reply, err
        finally:
            if proc.poll() is None:
                proc.kill()

    vm_code, vm_out, vm_reply, vm_err = exercise(
        lambda: subprocess.Popen(
            [sys.executable, '-c',
             'import sys; from codecin.cli import main; '
             'sys.exit(main([sys.argv[1], "--log-level", "ERROR"]))', src],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    assert vm_code == 0, (vm_out, vm_err)

    # 为 AOT 阶段重新取空闲端口; 端口偶发被占用时换端口重试
    aot_program = program
    last_error = None
    for attempt in range(5):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        with open(src, 'w', encoding='utf-8') as f:
            f.write(aot_program.replace(f'tcp_listen({_first_port})',
                                        f'tcp_listen({port})'))
        built = aot.build_program(src,
                                  out=os.path.join(workdir, 'serverprog'),
                                  backend='c', use_cache=False)
        try:
            aot_code, aot_out, aot_reply, aot_err = exercise(
                lambda: subprocess.Popen([built], stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE))
            break
        except AssertionError as exc:      # 换端口再来一次
            last_error = exc
    else:
        pytest.skip(f'TCP 服务端未能启动（端口竞争/环境限制）: {last_error}')

    assert vm_code == aot_code == 0
    assert vm_reply == aot_reply == b'pong'
    assert vm_out.strip() == aot_out.strip() == b'4', (vm_out, aot_out,
                                                       aot_err)


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_udp_roundtrip_matches_vm(workdir):
    """udp_open/sendto/recvfrom/close 与 VM 一致（本地 UDP echo 对端）。"""
    import socket
    import sys
    import threading

    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(('127.0.0.1', 0))
    peer.settimeout(30)
    peer_port = peer.getsockname()[1]

    def echo():
        try:
            for _ in range(2):
                data, addr = peer.recvfrom(64)
                peer.sendto(data, addr)
        finally:
            peer.close()

    threading.Thread(target=echo, daemon=True).start()

    program = ('function main() -> int {\n'
               '    int fd = udp_open(0)\n'
               '    if (fd < 0) { println("open_fail") return 1 }\n'
               '    int buf[64]\n'
               f'    int sent = udp_sendto(fd, "127.0.0.1", {peer_port}, '
               '"ping", 4)\n'
               '    int n = udp_recvfrom(fd, buf, 64, 0)\n'
               '    println(sent)\n'
               '    println(n)\n'
               '    udp_close(fd)\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'udp.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)

    vm = subprocess.run(
        [sys.executable, '-c',
         'import sys; from codecin.cli import main; '
         'sys.exit(main([sys.argv[1], "--log-level", "ERROR"]))', src],
        capture_output=True, timeout=60)
    built = aot.build_program(src, out=os.path.join(workdir, 'udpprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, timeout=60)
    assert vm.returncode == result.returncode == 0
    vm_out = vm.stdout.replace(b'\r\n', b'\n').strip()
    aot_out = result.stdout.replace(b'\r\n', b'\n').strip()
    assert vm_out == aot_out == b'4\n4'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_base64_matches_vm(workdir, capsys, monkeypatch):
    src = os.path.join(workdir, 'b64.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(base64_encode("hello"))\n'
                '    println(base64_decode("aGVsbG8="))\n'
                '    println(base64_encode("abc"))\n'
                '    return 0\n'
                '}\n')
    monkeypatch.chdir(workdir)
    assert cli.main(['b64.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines() == ['aGVsbG8=', 'hello', 'YWJj']

    built = aot.build_program(src, out=os.path.join(workdir, 'b64prog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_sha256_matches_vm(workdir, capsys, monkeypatch):
    src = os.path.join(workdir, 'sha.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(sha256("abc"))\n'
                '    println(sha256(""))\n'
                '    return 0\n'
                '}\n')
    monkeypatch.chdir(workdir)
    assert cli.main(['sha.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines() == [
        'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    ]

    built = aot.build_program(src, out=os.path.join(workdir, 'shaprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def _png_size(path):
    import struct
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    return struct.unpack('>II', data[16:24])


def _decode_png(path):
    """解码本后端产出的 PNG (RGBA8 + filter 0), 返回像素行。"""
    import struct
    import zlib

    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, idat, width, height = 8, b'', 0, 0
    while pos < len(data):
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b'IHDR':
            width, height = struct.unpack('>II', chunk[:8])
        elif kind == b'IDAT':
            idat += chunk
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    assert len(raw) == stride * height
    rows = []
    for y in range(height):
        base = y * stride
        assert raw[base] == 0, 'AOT 端 PNG 应使用 filter 0'
        rows.append(raw[base + 1:base + stride])
    return width, height, rows


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_canvas_png_matches_vm(workdir, monkeypatch):
    program = ('function main() -> int {\n'
               '    canvas(8, 8)\n'
               '    set_color(0xFF0000)\n'
               '    fill_rect(1, 1, 3, 3)\n'
               '    set_color(0x00FF00)\n'
               '    draw_line(0, 7, 7, 7)\n'
               '    println(save_png("out.png"))\n'
               '    return 0\n'
               '}\n')
    vm_dir = os.path.join(workdir, 'vm')
    aot_dir = os.path.join(workdir, 'aot')
    for target in (vm_dir, aot_dir):
        os.makedirs(target)
        with open(os.path.join(target, 'c.cin'), 'w', encoding='utf-8') as f:
            f.write(program)

    monkeypatch.chdir(vm_dir)
    assert cli.main(['c.cin', '--log-level', 'ERROR']) == 0
    vm_png = os.path.join(vm_dir, 'out.png')

    built = aot.build_program(os.path.join(aot_dir, 'c.cin'),
                              out=os.path.join(aot_dir, 'cprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=aot_dir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '0'

    assert _png_size(vm_png) == (8, 8)
    wa, ha, rows_a = _decode_png(os.path.join(aot_dir, 'out.png'))
    assert (wa, ha) == (8, 8)

    def pixel(rows, x, y):
        return tuple(rows[y][x * 4:x * 4 + 4])

    assert pixel(rows_a, 1, 1) == (255, 0, 0, 255)
    assert pixel(rows_a, 2, 2) == (255, 0, 0, 255)
    assert pixel(rows_a, 0, 7) == (0, 255, 0, 255)
    assert pixel(rows_a, 6, 0) == (255, 255, 255, 255)
    assert pixel(rows_a, 4, 4) == (255, 255, 255, 255)


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_canvas_circle_pixels(workdir):
    src = os.path.join(workdir, 'circle.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    canvas(9, 9)\n'
                '    set_color(0x0000FF)\n'
                '    fill_circle(4, 4, 2)\n'
                '    println(save_png("c.png"))\n'
                '    return 0\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'circleprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    width, height, rows = _decode_png(os.path.join(workdir, 'c.png'))
    assert (width, height) == (9, 9)

    def pixel(x, y):
        return tuple(rows[y][x * 4:x * 4 + 4])

    assert pixel(4, 4) == (0, 0, 255, 255)      # 圆心
    assert pixel(4, 2) == (0, 0, 255, 255)      # 半径内
    assert pixel(7, 4) == (255, 255, 255, 255)  # 半径外
    assert pixel(8, 8) == (255, 255, 255, 255)  # 四角保持白底


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_download_writes_file(workdir):
    import http.server
    import socketserver
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'downloaded-body'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        src = os.path.join(workdir, 'dl.cin')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('function main() -> int {\n'
                    f'    int rc = download("http://127.0.0.1:{port}/f", '
                    '"got.txt")\n'
                    '    println(rc)\n'
                    '    println(file_size("got.txt"))\n'
                    '    println(file_read("got.txt"))\n'
                    '    return 0\n'
                    '}\n')
        built = aot.build_program(src, out=os.path.join(workdir, 'dlprog'),
                                  backend='c', use_cache=False)
        result = subprocess.run([built], cwd=workdir, capture_output=True,
                                text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ['0', '15', 'downloaded-body']
        with open(os.path.join(workdir, 'got.txt'), 'rb') as f:
            assert f.read() == b'downloaded-body'
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_show_canvas_reports_unsupported(workdir):
    """无窗口环境下 show_canvas 明确返回 -1, 而不是静默成功/崩溃。"""
    src = os.path.join(workdir, 'show.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    canvas(4, 4)\n'
                '    println(show_canvas())\n'
                '    return 0\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'showprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in ('-1', '18446744073709551615')


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_path_syscalls_match_vm(workdir, capsys, monkeypatch):
    """is_dir/chdir (87/90) 与 VM 一致。"""
    program = ('function main() -> int {\n'
               '    println(is_dir("."))\n'
               '    println(is_dir("nope-xyz"))\n'
               '    mkdir("sub")\n'
               '    println(is_dir("sub"))\n'
               '    println(chdir("sub"))\n'
               '    println(is_dir(".."))\n'
               '    println(chdir(".."))\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'path.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)
    monkeypatch.chdir(workdir)
    assert cli.main(['path.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines() == ['1', '0', '1', '0', '1', '0']

    built = aot.build_program(src, out=os.path.join(workdir, 'pathprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_dir_remove_is_recursive(workdir, capsys, monkeypatch):
    program = ('function main() -> int {\n'
               '    mkdir("a/b/c")\n'
               '    file_write("a/b/c/x.txt", "hi")\n'
               '    println(is_dir("a/b/c"))\n'
               '    println(dir_remove("a"))\n'
               '    println(is_dir("a"))\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'rm.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)
    monkeypatch.chdir(workdir)
    assert cli.main(['rm.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines() == ['1', '0', '0']

    built = aot.build_program(src, out=os.path.join(workdir, 'rmprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert not os.path.exists(os.path.join(workdir, 'a'))


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_copy_move_mtime_match_vm(workdir, capsys, monkeypatch):
    program = ('function main() -> int {\n'
               '    file_write("a.txt", "hello")\n'
               '    println(file_copy("a.txt", "b.txt"))\n'
               '    println(file_size("b.txt"))\n'
               '    println(file_move("b.txt", "c.txt"))\n'
               '    println(file_exists("b.txt"))\n'
               '    println(file_read("c.txt"))\n'
               '    println(file_mtime("c.txt") > 0)\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'fm.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)
    monkeypatch.chdir(workdir)
    assert cli.main(['fm.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines() == ['0', '5', '0', '0', 'hello', 'true']

    built = aot.build_program(src, out=os.path.join(workdir, 'fmprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert not os.path.exists(os.path.join(workdir, 'b.txt'))
    with open(os.path.join(workdir, 'c.txt'), encoding='utf-8') as f:
        assert f.read() == 'hello'


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_path_join_basename_dirname_match_vm(workdir, capsys, monkeypatch):
    program = ('function main() -> int {\n'
               '    println(path_join("a", "b"))\n'
               '    println(path_join("a/b", "c.txt"))\n'
               '    println(path_basename("a/b/c.txt"))\n'
               '    println(path_dirname("a/b/c.txt"))\n'
               '    println(path_dirname("x.txt"))\n'
               '    return 0\n'
               '}\n')
    src = os.path.join(workdir, 'pj.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(program)
    monkeypatch.chdir(workdir)
    assert cli.main(['pj.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert expected.splitlines()[2:5] == ['c.txt', os.path.join('a', 'b'),
                                          '.']

    built = aot.build_program(src, out=os.path.join(workdir, 'pjprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_path_abs_matches_vm(workdir, capsys, monkeypatch):
    src = os.path.join(workdir, 'abs.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(path_abs("."))\n'
                '    println(path_abs("no-such-file.txt"))\n'
                '    return 0\n'
                '}\n')
    monkeypatch.chdir(workdir)
    assert cli.main(['abs.cin', '--log-level', 'ERROR']) == 0
    expected = capsys.readouterr().out
    assert os.path.isabs(expected.splitlines()[0])

    built = aot.build_program(src, out=os.path.join(workdir, 'absprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_time_cpu_arch_syscalls(workdir):
    src = os.path.join(workdir, 'tm.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(cpu_count() >= 1)\n'
                '    println(arch_name())\n'
                '    int t0 = time_ms()\n'
                '    sleep_ms(30)\n'
                '    int t1 = time_ms()\n'
                '    println(t1 >= t0)\n'
                '    return 0\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'tmprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == 'true'
    assert lines[1] in ('amd64', 'arm64', 'x86')
    assert lines[2] == 'true'


def _zig_available():
    return toolchain.find_zig() is not None


@pytest.mark.skipif(not _zig_available(), reason='未安装 Zig (无交叉工具链)')
def test_cross_compile_elf_targets(workdir):
    """同一份 C 后端产物可交叉编译为 x86-64 / AArch64 ELF。"""
    import struct

    src = os.path.join(workdir, 'x.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    int s = 0\n'
                '    int i = 1\n'
                '    while (i <= 5) { s = s + i i = i + 1 }\n'
                '    println(s)\n'
                '    return 0\n'
                '}\n')
    cpu = CPU(Config(log_level='ERROR'), src)
    expected = {'x86_64-linux': 0x3E, 'aarch64-linux': 0xB7}
    for target, machine in expected.items():
        out = os.path.join(workdir, target)
        built = cbackend.compile_instructions(
            cpu.instructions, cpu.labels, cpu.memory.snapshot_segments(),
            cpu.entry_pc, len(cpu.memory), cpu.sp, cpu.heap_base, out,
            target=target)
        data = open(built, 'rb').read()
        assert data[:4] == b'\x7fELF', target
        assert struct.unpack_from('<H', data, 18)[0] == machine, target


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_meminfo_and_android_flags(workdir):
    src = os.path.join(workdir, 'mi.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    string m = mem_info()\n'
                '    println(strlen(m) > 10)\n'
                '    println(is_android())\n'
                '    return 0\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'miprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['true', '0']


def test_supports_reports_missing_opcode_and_syscall():
    ok, reason = cbackend.supports([('MOV', [('reg', 0), ('imm', 1)]),
                                    ('HALT', [])])
    assert ok, reason
    ok, reason = cbackend.supports([('SYS', [('imm', 39)]), ('HALT', [])])
    assert not ok and 'SYS 39' in reason and 'AUDIOPLAY' in reason
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
def test_canvas_text_pixels(workdir):
    """draw_text 使用内置 5x7 字库, 与 VM 的字形一致。"""
    src = os.path.join(workdir, 'text.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    canvas(16, 16)\n'
                '    set_color(0x0000FF)\n'
                '    draw_text(1, 1, "H")\n'
                '    println(save_png("t.png"))\n'
                '    return 0\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'textprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], cwd=workdir, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    width, height, rows = _decode_png(os.path.join(workdir, 't.png'))
    assert (width, height) == (16, 16)

    def pixel(x, y):
        return tuple(rows[y][x * 4:x * 4 + 4])

    # 'H' 第 0 行位图 10001 -> x=1,5 着色, x=3 留白; 第 3 行 11111 全着色。
    assert pixel(1, 1) == (0, 0, 255, 255)
    assert pixel(5, 1) == (0, 0, 255, 255)
    assert pixel(3, 1) == (255, 255, 255, 255)
    assert pixel(1, 4) == (0, 0, 255, 255)
    assert pixel(5, 4) == (0, 0, 255, 255)
    assert pixel(7, 1) == (255, 255, 255, 255)   # 字距 6px, 第二个字符在 x=7


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_c_backend_exec_and_output(workdir):
    src = os.path.join(workdir, 'exec.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println(exec_output("echo hi"))\n'
                '    return exec("exit 3")\n'
                '}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'execprog'),
                              backend='c', use_cache=False)
    result = subprocess.run([built], capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 3, result.stderr
    assert 'hi' in result.stdout


@pytest.mark.skipif(_compiler() is None, reason='本机没有可用的 C 编译器')
def test_inspect_binary_matches_host_target(workdir):
    host = aot.host_target()
    src = os.path.join(workdir, 'fmt.cin')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n    return 7\n}\n')
    built = aot.build_program(src, out=os.path.join(workdir, 'fmtprog'),
                              target=host, backend='c', use_cache=False)
    info = cbackend.inspect_binary(built, host)
    assert info['bits'] == 64
    assert info['format'] in ('elf', 'pe')
    wrong = ('aarch64-linux' if host.endswith('windows')
             else 'x86_64-windows')
    with pytest.raises(ValueError):
        cbackend.inspect_binary(built, wrong)
