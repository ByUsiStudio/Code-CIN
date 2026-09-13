"""A4: --debug-server 远程驱动式调试协议 (socket 集成测试)。"""

import ast
import contextlib
import socket
import threading

from tests.helpers import imm, new_cpu, reg

PROGRAM = [
    ('ADDI', [reg(0), reg(0), imm(1)]),   # pc0: x0=1
    ('ADDI', [reg(0), reg(0), imm(2)]),   # pc1: x0=3
    ('HALT', []),                          # pc2
]


class _Client:
    def __init__(self, port):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.buf = b''

    def send(self, line: str):
        self.sock.sendall((line + '\n').encode('utf-8'))

    def recv_line(self) -> str:
        while b'\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        if b'\n' not in self.buf:
            raise RuntimeError('connection closed')
        line, self.buf = self.buf.split(b'\n', 1)
        return line.decode('utf-8', 'replace')

    def close(self):
        with contextlib.suppress(Exception):
            self.sock.close()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_remote():
    port = _free_port()
    cpu = new_cpu(debug_server_port=port)
    cpu.instructions = [tuple(i) for i in PROGRAM]
    cpu.entry_pc = 0
    cpu.pc = 0
    errors = []

    def runner():
        try:
            cpu.run()
        except Exception as e:          # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    # 等待监听就绪
    client = None
    for _ in range(50):
        try:
            client = _Client(port)
            break
        except OSError:
            import time
            time.sleep(0.05)
    assert client is not None, 'server did not start listening'
    return cpu, t, client, errors


def test_remote_drive_session():
    cpu, thread, client, errors = _start_remote()
    try:
        assert 'ready' in client.recv_line()

        client.send('break 1')
        assert 'OK' in client.recv_line()

        client.send('continue')
        assert client.recv_line() == 'OK continuing'
        assert client.recv_line().startswith('PAUSED pc=0x1')

        client.send('regs')
        regs = ast.literal_eval(client.recv_line())
        assert regs[0] == 1          # 已执行 pc0

        client.send('step')
        assert 'OK pc=0x2' in client.recv_line()

        client.send('regs')
        assert ast.literal_eval(client.recv_line())[0] == 3

        client.send('continue')
        assert client.recv_line() == 'OK continuing'
        assert client.recv_line() == 'HALTED'

        client.send('step')
        assert 'program halted' in client.recv_line()

        client.send('pc')
        assert 'PC' in client.recv_line()

        client.send('quit')
        assert client.recv_line() == 'BYE'
    finally:
        client.close()
        thread.join(timeout=5)

    assert not errors, errors
    assert cpu.running is False


def test_remote_step_through_to_halt():
    cpu, thread, client, errors = _start_remote()
    try:
        client.recv_line()                       # welcome
        client.send('step')
        assert 'OK pc=0x1' in client.recv_line()
        client.send('step')
        assert 'OK pc=0x2' in client.recv_line()
        client.send('step')
        assert client.recv_line() == 'HALTED'
        client.send('quit')
        client.recv_line()
    finally:
        client.close()
        thread.join(timeout=5)
    assert not errors, errors


def test_remote_breakpoint_set_and_info():
    cpu, thread, client, errors = _start_remote()
    try:
        client.recv_line()
        client.send('break 0')
        assert 'OK' in client.recv_line()
        client.send('info break')
        assert 'Breakpoints' in client.recv_line()
        assert '0x0' in client.recv_line()
        client.send('quit')
        client.recv_line()
    finally:
        client.close()
        thread.join(timeout=5)
    assert not errors, errors
