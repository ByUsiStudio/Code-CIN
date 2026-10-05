"""FFI (SYS 140-144) 与网络 (SYS 145-157) 系统调用端到端测试。

直接跑 CIN 程序 (经 Go 原生引擎), 需要原生 DLL; 网络用例全部走 127.0.0.1
回环 (Python 侧起临时 echo/HTTP 服务), 不访问外网。
"""

import http.server
import os
import socket
import struct
import sys
import threading
import uuid

import pytest

from codecin import native
from codecin.cin import CINCompiler
from tests.conftest import TMP_ROOT
from tests.helpers import new_cpu

IS_WINDOWS = sys.platform == 'win32'

def _run_cin_capture(src: str, allow_fail: bool = False, **cfg):
    """编译并运行 CIN 源码, 捕获 stdout, 返回 cpu。

    源码先写入临时文件再经 CINCompiler.compile 编译 —— import 展开
    ("ffi.cin"/"net.cin") 只在 load_program_source_mapped (文件路径)
    中进行, compile_source 不展开 import。
    allow_fail=True 时不断言 execution_failed (供沙箱拦截用例)。
    """
    os.makedirs(TMP_ROOT, exist_ok=True)
    path = os.path.join(TMP_ROOT, f'ffi_net_{uuid.uuid4().hex}.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    res = CINCompiler().compile(path)
    cpu = new_cpu(**cfg)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    cpu.run()
    if not allow_fail:
        assert not cpu.execution_failed, ''.join(cpu.output_buffer)
    return cpu

# ==================== FFI (SYS 140-144) ====================

FFI_LIB_SRC = '''
import "ffi.cin"
function main() -> int {{
    int h = ffi_load("{lib}")
    if (h == 0) {{ return -1 }}
    int fn = ffi_find(h, "{sym}")
    if (fn == 0) {{ return -2 }}
    int v = ffi_call0(fn)
    ffi_free(h)
    return v
}}'''

FFI_FLOAT_SRC = '''
import "ffi.cin"
function main() -> float {{
    int h = ffi_load("{lib}")
    if (h == 0) {{ return 0.0 }}
    int fn = ffi_find(h, "{sym}")
    if (fn == 0) {{ return 0.0 }}
    float r = ffi_callf2(fn, 3.0, 4.0)
    ffi_free(h)
    return r
}}'''

@pytest.mark.skipif(not IS_WINDOWS, reason='Windows 专属库')
def test_ffi_call_gettickcount64_windows():
    cpu = _run_cin_capture(
        FFI_LIB_SRC.format(lib='kernel32.dll', sym='GetTickCount64'))
    assert cpu.regs.read(0) > 0                    # 开机以来的毫秒数

@pytest.mark.skipif(IS_WINDOWS, reason='POSIX 专属库')
def test_ffi_call_getpid_posix():
    cpu = _run_cin_capture(
        FFI_LIB_SRC.format(lib='libc.so.6', sym='getpid'))
    assert cpu.regs.read(0) > 0

@pytest.mark.skipif(not IS_WINDOWS, reason='Windows 专属库')
def test_ffi_callf_pow_windows():
    cpu = _run_cin_capture(
        FFI_FLOAT_SRC.format(lib='ucrtbase.dll', sym='pow'))
    bits = cpu.regs.read(0) & 0xFFFFFFFFFFFFFFFF
    val = struct.unpack('<d', struct.pack('<Q', bits))[0]
    assert abs(val - 81.0) < 1e-9                  # pow(3, 4)

@pytest.mark.skipif(IS_WINDOWS, reason='POSIX 专属库')
def test_ffi_callf_pow_posix():
    cpu = _run_cin_capture(
        FFI_FLOAT_SRC.format(lib='libm.so.6', sym='pow'))
    bits = cpu.regs.read(0) & 0xFFFFFFFFFFFFFFFF
    val = struct.unpack('<d', struct.pack('<Q', bits))[0]
    assert abs(val - 81.0) < 1e-9

def test_ffi_load_missing_library_returns_zero():
    cpu = _run_cin_capture(
        'import "ffi.cin"\n'
        'function main() -> int {\n'
        '    return ffi_load("no_such_lib_xyz_123.dll")\n'
        '}')
    assert cpu.regs.read(0) == 0

def test_ffi_find_missing_symbol_returns_zero():
    lib = 'kernel32.dll' if IS_WINDOWS else 'libc.so.6'
    cpu = _run_cin_capture(
        'import "ffi.cin"\n'
        'function main() -> int {\n'
        f'    int h = ffi_load("{lib}")\n'
        '    if (h == 0) { return -1 }\n'
        '    return ffi_find(h, "cin_no_such_symbol_xyz")\n'
        '}')
    assert cpu.regs.read(0) == 0

def test_ffi_call_bad_handle_returns_zero():
    cpu = _run_cin_capture(
        'import "ffi.cin"\n'
        'function main() -> int {\n'
        '    return ffi_call(12345, 0, 0)\n'
        '}')
    # 无效句柄: 引擎返回错误 (execution_failed) 或 0, 都不应崩溃
    assert cpu.regs.read(0) == 0 or cpu.execution_failed

# ==================== DNS (SYS 157) ====================

def test_dns_lookup_localhost():
    cpu = _run_cin_capture(
        'import "net.cin"\n'
        'function main() -> int {\n'
        '    string ip = dns_lookup("localhost")\n'
        '    if (ip == "") { return -1 }\n'
        '    return 1\n'
        '}')
    assert cpu.regs.read(0) == 1

def test_dns_lookup_invalid_host_returns_empty():
    cpu = _run_cin_capture(
        'import "net.cin"\n'
        'function main() -> string {\n'
        '    return dns_lookup("cin_no_such_host_xyz.invalid")\n'
        '}')
    # 失败返回空串: x0 指向空堆串, 断言执行未失败即可 (空串长度 0)
    ip = cpu.memory.read_cstr_bytes(cpu.regs.read(0))
    assert ip == b''

# ==================== TCP (SYS 147-152) ====================

def _echo_server_once():
    """一次性 TCP echo 服务: 收到什么就原样发回, 返回 (port, 收到的数据)。"""
    srv = socket.socket()
    srv.settimeout(10)
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    holder = {'port': port, 'data': b''}

    def serve():
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(10)
                data = conn.recv(1024)
                holder['data'] = data
                conn.sendall(data)
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return holder, t

def test_tcp_client_send_recv_echo():
    holder, thread = _echo_server_once()
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    int fd = tcp_dial("127.0.0.1", {holder['port']})
    if (fd <= 0) {{ return -1 }}
    int sent = tcp_send_str(fd, "hello")
    if (sent != 5) {{ return -2 }}
    int buf[128]
    string reply = tcp_recv_line(fd, buf, 1024)
    tcp_close(fd)
    if (reply == "hello") {{ return 1 }}
    return -3
}}''')
    thread.join(timeout=10)
    assert holder['data'] == b'hello'
    assert cpu.regs.read(0) == 1

def _cin_tcp_server_src(port: int) -> str:
    return f'''
import "net.cin"
function main() -> int {{
    int lfd = tcp_listen({port})
    if (lfd <= 0) {{ return -1 }}
    int cfd = tcp_accept(lfd)
    if (cfd <= 0) {{ return -2 }}
    int buf[128]
    int n = tcp_recv(cfd, buf, 128)
    if (n <= 0) {{ return -3 }}
    int sent = tcp_send(cfd, buf, n)
    tcp_close(cfd)
    tcp_close(lfd)
    return sent
}}'''

def test_tcp_server_listen_accept_echo():
    """CIN 侧 tcp_listen/tcp_accept: Python 客户端连入, CIN 回显。"""
    srv = socket.socket()
    srv.settimeout(10)
    srv.bind(('127.0.0.1', 0))
    port = srv.getsockname()[1]
    srv.close()                                    # 只为拿空闲端口 (CIN 再监听)

    result = {'reply': b''}

    def client():
        try:
            for _ in range(50):                    # 等 CIN 监听就绪
                try:
                    conn = socket.create_connection(('127.0.0.1', port),
                                                    timeout=5)
                    break
                except OSError:
                    threading.Event().wait(0.05)
            else:
                return
            with conn:
                conn.settimeout(10)
                conn.sendall(b'ping\n')
                result['reply'] = conn.recv(1024)
        except OSError:
            pass

    t = threading.Thread(target=client, daemon=True)
    t.start()
    cpu = _run_cin_capture(_cin_tcp_server_src(port))
    t.join(timeout=10)
    assert result['reply'] == b'ping\n'
    assert cpu.regs.read(0) == 5                   # 回显 5 字节

def test_tcp_dial_refused_returns_minus_one():
    # 端口 1 上的连接几乎必然被拒绝 (保留端口, 无监听)
    cpu = _run_cin_capture(
        'import "net.cin"\n'
        'function main() -> int {\n'
        '    return tcp_dial("127.0.0.1", 1)\n'
        '}')
    assert cpu.regs.read(0) == 0xFFFFFFFFFFFFFFFF or \
        cpu.regs.read(0) == 0xFFFFFFFF

# ==================== UDP (SYS 153-156) ====================

def test_udp_roundtrip():
    """CIN 发数据报到 Python, 再收 Python 的回包。"""
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.settimeout(10)
    peer.bind(('127.0.0.1', 0))
    peer_port = peer.getsockname()[1]
    result = {'from': None}

    def exchange():
        try:
            data, addr = peer.recvfrom(1024)       # 等 CIN 的 "hi"
            result['from'] = data
            peer.sendto(b'pong', addr)             # 回给 CIN 的端口
        except OSError:
            pass

    t = threading.Thread(target=exchange, daemon=True)
    t.start()
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    int fd = udp_open(0)
    if (fd <= 0) {{ return -1 }}
    int sent = udp_send_str(fd, "127.0.0.1", {peer_port}, "hi")
    int buf[64]
    int n = udp_recvfrom(fd, buf, 64, 0)
    udp_close(fd)
    return sent * 1000 + n
}}''')
    t.join(timeout=10)
    peer.close()
    assert result['from'] == b'hi'
    assert cpu.regs.read(0) == 2 * 1000 + 4        # sent=2, recv=4 ("pong")

def test_udp_send_str_hostname():
    """udp_send_str 支持主机名 (localhost 解析为 127.0.0.1)。"""
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.settimeout(10)
    peer.bind(('127.0.0.1', 0))
    peer_port = peer.getsockname()[1]
    got = {'data': b''}

    def exchange():
        try:
            got['data'], _ = peer.recvfrom(1024)
        except OSError:
            pass

    t = threading.Thread(target=exchange, daemon=True)
    t.start()
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    int fd = udp_open(0)
    if (fd <= 0) {{ return -1 }}
    int sent = udp_send_str(fd, "localhost", {peer_port}, "by-name")
    udp_close(fd)
    return sent
}}''')
    t.join(timeout=10)
    peer.close()
    assert got['data'] == b'by-name'
    assert cpu.regs.read(0) == 7

# ==================== HTTP (SYS 145-146) ====================

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):                  # 静默访问日志
        pass

    def do_GET(self):
        body = b'CIN_HTTP_OK body'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        payload = self.rfile.read(n)
        body = b'POSTED:' + payload
        self.send_response(201)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

@pytest.fixture(scope='module')
def http_server():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()
    srv.server_close()

def test_http_get_and_status_code(http_server):
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    string body = http_get("http://127.0.0.1:{http_server}/test")
    int code = http_code()
    int found = 0
    if (indexof(body, "CIN_HTTP_OK") >= 0) {{ found = 1 }}
    return code * 10 + found
}}''')
    assert cpu.regs.read(0) == 200 * 10 + 1

def test_http_post_custom_headers(http_server):
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    string body = http_post("http://127.0.0.1:{http_server}/submit",
                            "cin-payload")
    int code = http_code()
    int ok = 0
    if (indexof(body, "POSTED:cin-payload") >= 0) {{ ok = 1 }}
    return code * 10 + ok
}}''')
    assert cpu.regs.read(0) == 201 * 10 + 1

def test_http_req_custom_method_and_headers(http_server):
    cpu = _run_cin_capture(f'''
import "net.cin"
function main() -> int {{
    string body = http_req("GET",
                           "http://127.0.0.1:{http_server}/hdr",
                           "X-Cin-Test: 1\\nAccept: text/plain",
                           "")
    int code = http_code()
    return code
}}''')
    assert cpu.regs.read(0) == 200

# ==================== 沙箱 ====================

def test_sandbox_blocks_host_syscalls():
    # 沙箱拦宿主 SYS: 引擎报错 -> ExecutionError -> execution_failed
    cpu = _run_cin_capture(
        'function main() -> int {\n'
        '    return http_get("http://127.0.0.1:1/x")\n'
        '}',
        allow_fail=True,
        sandbox_mode=True)
    assert cpu.execution_failed

def test_sandbox_allows_core_computation():
    cpu = _run_cin_capture(
        'function main() -> int {\n'
        '    int s = 0\n'
        '    for (int i = 1; i <= 100; i = i + 1) {\n'
        '        s = s + i\n'
        '    }\n'
        '    return s\n'
        '}',
        sandbox_mode=True)
    assert cpu.regs.read(0) == 5050
