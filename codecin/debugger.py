"""调试器: 条件断点、执行历史(前进/后退)、TCP 远程调试服务与交互式调试会话。

职责 (自 ucpu/cpu.py 拆分):
  - DebugSession   交互式调试会话 (断点命中 / --step 统一命令集)
  - display_state  终端状态渲染 (寄存器/内存/缓存)
  - DebugServer    条件断点 + 执行历史 + TCP 远程调试服务

设计约束: 本模块不 import ucpu.cpu (仅 TYPE_CHECKING), 通过属性访问 CPU。
"""

import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional

from .console import Colors, Panel

if TYPE_CHECKING:
    from .cpu import CPU


# ==================== 状态渲染 ====================


def fmt_operand(op: tuple) -> str:
    """格式化操作数为汇编文本 (自 cpu.CPU._fmt_operand 迁移)。"""
    kind = op[0]
    if kind == 'reg':
        return f"X{op[1]}"
    if kind == 'vec':
        return f"V{op[1]}"
    if kind == 'veclane':
        return f"V{op[1]}.{op[2]}"
    if kind == 'imm':
        return f"#{op[1]}"
    if kind == 'mem':
        base, off = op[1], op[2]
        if base >= 0:
            return f"[X{base}, #{off}]" if off else f"[X{base}]"
        return f"[#{off}]"
    if kind == 'cond':
        return op[1]
    if kind == 'str':
        return f"\"@{op[1]}\""
    return str(op)


def display_state(cpu: 'CPU', title: str = "CPU State",
                  opcode: Optional[str] = None,
                  args: Optional[List[tuple]] = None) -> None:
    """渲染 CPU 完整状态 (寄存器/标志/内存/缓存)。"""
    console = cpu.console
    console.clear()
    console.rule(f"{Colors.colorize(title, Colors.MAGENTA, True)}")

    if opcode:
        instr_text = f"{opcode} " + ", ".join(fmt_operand(a) for a in (args or []))
        console.print(str(Panel(instr_text.strip(), title="Current Instruction")))

    reg_info = {
        'PSTATE': ' '.join(f"{k}={int(v)}" for k, v in cpu.pstate.items()),
        'SP': cpu.sp,
        'PC': cpu.pc,
    }
    if cpu.breakpoints:
        reg_info['BREAKPOINTS'] = ', '.join(f"{b:#x}" for b in cpu.breakpoints)
    cpu.regs.display_registers("General Registers (X0-X31)", reg_info, console)

    if cpu.config.show_vector_regs:
        cpu.vec_regs.display_vector_registers("Vector Registers (V0-V31)", console)

    cpu.memory.display_memory("Memory (first 64 bytes)", 0, 64, console)

    cache_stats = cpu.cache.get_stats()
    console.print(
        f"Cache: {cache_stats['hits']} hits, {cache_stats['misses']} misses "
        f"({cache_stats['hit_rate'] * 100:.1f}% hit rate)")
    console.rule()


DEBUG_HELP = f"""
{Colors.colorize('Debug Commands:', Colors.CYAN, True)}
  {Colors.colorize('continue / c', Colors.GREEN)}  - Continue execution
  {Colors.colorize('step / s (空行)', Colors.GREEN)} - Single step
  {Colors.colorize('run / r', Colors.GREEN)}       - Run freely (退出单步模式)
  {Colors.colorize('reverse', Colors.GREEN)}       - Reverse one step
  {Colors.colorize('forward', Colors.GREEN)}       - Forward one step
  {Colors.colorize('break <addr> [cond]', Colors.GREEN)} - Set breakpoint
  {Colors.colorize('delete <addr>', Colors.GREEN)} - Remove breakpoint
  {Colors.colorize('watch <addr> [r|w|rw]', Colors.GREEN)} - Set watchpoint
  {Colors.colorize('list / info', Colors.GREEN)}   - List breakpoints
  {Colors.colorize('print / p <target>', Colors.GREEN)} - X0-X31, regs, mem [addr], cache, pc, sp
  {Colors.colorize('quit / q', Colors.GREEN)}      - Exit simulator
  {Colors.colorize('help / h', Colors.GREEN)}      - Show this help
"""


@dataclass
class ConditionalBreakpoint:
    address: int
    condition: str
    count: int = 0
    hit_count: int = 0
    enabled: bool = True


# ==================== 远程调试服务 ====================


class DebugServer:
    """TCP 远程调试服务 + 条件断点求值 + 执行历史 (前进/后退)。

    注意: 交互式会话在本地以 DebugSession 驱动; 本服务为未来 IDE/远程
    集成预留 (start() 为阻塞式服务循环)。
    """

    def __init__(self, cpu: 'CPU', port: int = 1234):
        self.cpu = cpu
        self.port = port
        self.socket = None
        self.connected = False
        self.running = False
        self.execution_history: List[Dict] = []
        self.history_limit = 1000
        self.history_index = 0
        self.conditional_breakpoints: List[ConditionalBreakpoint] = []

    # ---------------- 历史记录 ----------------

    def record_state(self) -> None:
        snapshot = {
            'pc': self.cpu.pc,
            'regs': self.cpu.regs.get_all(),
            'sp': self.cpu.sp,
            'pstate': self.cpu.pstate.copy(),
            'timestamp': time.time(),
        }
        self.execution_history.append(snapshot)
        if len(self.execution_history) > self.history_limit:
            self.execution_history.pop(0)
        self.history_index = len(self.execution_history) - 1

    def _restore(self, snapshot: Dict) -> None:
        self.cpu.pc = snapshot['pc']
        self.cpu.regs.set_all(snapshot['regs'])
        self.cpu.sp = snapshot['sp']
        self.cpu.pstate = snapshot['pstate'].copy()

    def reverse_step(self) -> bool:
        if self.history_index > 0:
            self.history_index -= 1
            self._restore(self.execution_history[self.history_index])
            return True
        return False

    def forward_step(self) -> bool:
        if self.history_index < len(self.execution_history) - 1:
            self.history_index += 1
            self._restore(self.execution_history[self.history_index])
            return True
        return False

    # ---------------- 条件断点 ----------------

    def add_conditional_breakpoint(self, address: int, condition: str) -> None:
        bp = ConditionalBreakpoint(address=address, condition=condition)
        self.conditional_breakpoints.append(bp)
        self.cpu.breakpoints.add(address)
        self.cpu.logger.info(f"Conditional breakpoint set at PC={address:#x}: {condition}")

    def check_conditional_breakpoints(self) -> bool:
        for bp in self.conditional_breakpoints:
            if bp.address == self.cpu.pc and bp.enabled:
                try:
                    namespace = {
                        'regs': self.cpu.regs.get_all(),
                        'pc': self.cpu.pc,
                        'sp': self.cpu.sp,
                        'pstate': self.cpu.pstate.copy(),
                    }
                    if eval(bp.condition, {"__builtins__": {}}, namespace):
                        bp.hit_count += 1
                        self.cpu.console.print(
                            f"{Colors.colorize('Conditional breakpoint hit', Colors.YELLOW)} "
                            f"at PC={self.cpu.pc:#x}: {bp.condition}")
                        return True
                except Exception as e:
                    self.cpu.logger.warning(f"Condition evaluation failed: {e}")
        return False

    # ---------------- TCP 服务 ----------------

    def start(self) -> None:
        """阻塞式远程调试服务 (仅绑定 localhost; 供未来 IDE/调试客户端接入)。"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('localhost', self.port))
            self.socket.listen(1)
            self.cpu.logger.info(f"Debug server listening on port {self.port}")
            self.running = True
            while self.running:
                conn, addr = self.socket.accept()
                self.connected = True
                self.cpu.logger.info(f"Debug client connected from {addr}")
                self._handle_connection(conn)
        except Exception as e:
            self.cpu.logger.error(f"Debug server error: {e}")

    def _handle_connection(self, conn) -> None:
        try:
            while self.running:
                data = conn.recv(1024)
                if not data:
                    break
                response = self._process_command(data)
                if response:
                    conn.send(response.encode())
        except Exception as e:
            self.cpu.logger.debug(f"Debug connection error: {e}")
        finally:
            conn.close()
            self.connected = False

    def _process_command(self, data: bytes) -> str:
        cmd = data.decode().strip()
        parts = cmd.split()
        if not parts:
            return "ERROR: Empty command"
        command = parts[0].lower()

        if command == 'step':
            self.record_state()
            self.cpu.step()
            return "OK: Stepped"
        elif command == 'continue':
            self.cpu.running = True
            return "OK: Continuing"
        elif command == 'reverse':
            return "OK: Reversed" if self.reverse_step() else "ERROR: No history"
        elif command == 'forward':
            return "OK: Forwarded" if self.forward_step() else "ERROR: No history"
        elif command == 'break':
            if len(parts) > 1:
                try:
                    addr = int(parts[1], 0)
                    if len(parts) > 2:
                        cond = ' '.join(parts[2:])
                        self.add_conditional_breakpoint(addr, cond)
                        return f"OK: Conditional breakpoint at {addr:#x}: {cond}"
                    self.cpu.add_breakpoint(addr)
                    return f"OK: Breakpoint at {addr:#x}"
                except ValueError:
                    return "ERROR: Invalid address"
            return "ERROR: Missing address"
        elif command == 'delete':
            if len(parts) > 1:
                try:
                    addr = int(parts[1], 0)
                    self.cpu.remove_breakpoint(addr)
                    self.conditional_breakpoints = [
                        bp for bp in self.conditional_breakpoints if bp.address != addr]
                    return f"OK: Removed breakpoint at {addr:#x}"
                except ValueError:
                    return "ERROR: Invalid address"
            return "ERROR: Missing address"
        elif command == 'watch':
            if len(parts) > 1:
                try:
                    addr = int(parts[1], 0)
                    access = parts[2] if len(parts) > 2 else 'rw'
                    self.cpu.memory.set_protection(addr, access)
                    return f"OK: Watchpoint at {addr:#x} for {access}"
                except ValueError:
                    return "ERROR: Invalid address"
            return "ERROR: Missing address"
        elif command == 'regs':
            return str(self.cpu.regs.get_all())
        elif command == 'pc':
            return f"PC: {self.cpu.pc:#x}"
        elif command == 'mem':
            if len(parts) > 1:
                try:
                    addr = int(parts[1], 0)
                    if len(parts) > 2:
                        value = int(parts[2], 0)
                        self.cpu.memory.write_byte(addr, value)
                        return f"OK: mem[{addr:#x}] = {value:#x}"
                    value = self.cpu.memory.read_byte(addr)
                    return f"mem[{addr:#x}] = {value:#x}"
                except ValueError:
                    return "ERROR: Invalid address or value"
            return "ERROR: Missing address"
        elif command == 'history':
            if len(parts) > 1 and parts[1] == 'clear':
                self.execution_history.clear()
                self.history_index = 0
                return "OK: History cleared"
            return f"OK: History size: {len(self.execution_history)}, index: {self.history_index}"
        elif command == 'info':
            if len(parts) > 1:
                if parts[1] == 'break':
                    lines = ["Breakpoints:"]
                    for bp in self.cpu.breakpoints:
                        lines.append(f"  {bp:#x}")
                    for bp in self.conditional_breakpoints:
                        lines.append(f"  {bp.address:#x} (cond: {bp.condition})")
                    return "\n".join(lines)
                elif parts[1] == 'regs':
                    return str(self.cpu.regs.get_all())
                elif parts[1] == 'pc':
                    return f"PC: {self.cpu.pc:#x}"
            return "ERROR: Missing info target"
        elif command == 'quit':
            self.running = False
            return "OK: Quitting"
        return f"ERROR: Unknown command: {command}"

    # ---------------- A4: 驱动式远程调试 ----------------

    @staticmethod
    def _readline(conn) -> str:
        buf = []
        while True:
            ch = conn.recv(1)
            if not ch:
                return ''
            if ch == b'\n':
                return ''.join(buf)
            if ch != b'\r':
                buf.append(ch.decode('utf-8', 'replace'))
        return ''.join(buf)

    @staticmethod
    def _send(conn, line: str) -> None:
        conn.sendall((line + '\n').encode('utf-8'))

    def _burst(self) -> str:
        """continue: 执行到断点/停机/指令上限。返回 'halt' | 'limit' | 'paused'。"""
        cpu = self.cpu
        cpu.running = True
        while cpu.running:
            if cpu.pc < 0 or cpu.pc >= len(cpu.instructions):
                return 'halt'
            if cpu.stats.instruction_count >= cpu.config.max_instructions:
                return 'limit'
            # continue 后的首次断点豁免 (与本地会话一致)
            resume_bp = cpu._resume_bp_pc is not None
            if resume_bp:
                cpu._resume_bp_pc = None
            if not resume_bp:
                if self.check_conditional_breakpoints():
                    return 'paused'
                if cpu.pc in cpu.breakpoints:
                    cpu.console.print(
                        f"{Colors.colorize(f'Breakpoint hit at PC={cpu.pc:#x}', Colors.YELLOW)}")
                    return 'paused'
            opcode, args = cpu.instructions[cpu.pc]
            try:
                if not cpu.execute(opcode, args):
                    return 'halt'
            except Exception as e:
                cpu.running = False
                cpu.logger.error(f"Execution error: {e}")
                raise
        return 'halt'

    def drive(self) -> None:
        """单客户端驱动式调试会话 (阻塞; 由 cpu.run 在 --debug-server 时调用)。"""
        cpu = self.cpu
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('localhost', self.port))
            self.socket.listen(1)
            cpu.logger.info(f"Debug server listening on port {self.port}")
            conn, addr = self.socket.accept()
        except Exception as e:
            cpu.logger.error(f"Debug server error: {e}")
            return
        self.connected = True
        cpu.logger.info(f"Debug client connected from {addr}")
        try:
            self._send(conn, 'UCPU remote debug ready '
                             '(step/continue/break/delete/watch/regs/mem/pc/'
                             'history/info/quit)')
            halted = False
            while True:
                line = self._readline(conn)
                if line == '':
                    break
                cmd = line.strip()
                if not cmd:
                    continue
                parts = cmd.split()
                verb = parts[0].lower()
                if verb in ('quit', 'q', 'exit'):
                    self._send(conn, 'BYE')
                    break
                if halted and verb in ('step', 's', 'continue', 'c', 'run', 'r'):
                    self._send(conn, 'ERROR: program halted')
                    continue
                if verb in ('step', 's'):
                    self.record_state()
                    if cpu.step():
                        self._send(conn, f"OK pc=0x{cpu.pc:x}")
                    else:
                        self._send(conn, 'HALTED')
                        halted = True
                    continue
                if verb in ('continue', 'c', 'run', 'r'):
                    self._send(conn, 'OK continuing')
                    try:
                        res = self._burst()
                    except Exception as e:
                        self._send(conn, f'ERROR: {e}')
                        halted = True
                        continue
                    if res == 'halt':
                        self._send(conn, 'HALTED')
                        halted = True
                    elif res == 'limit':
                        self._send(conn, 'LIMIT')
                        halted = True
                    else:
                        self._send(conn, f"PAUSED pc=0x{cpu.pc:x}")
                    continue
                # 其余命令: 断点/查询等 (驱动空闲时执行)
                try:
                    resp = self._process_command(cmd.encode('utf-8'))
                except Exception as e:
                    resp = f'ERROR: {e}'
                self._send(conn, resp or 'OK')
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.connected = False
            try:
                self.socket.close()
            except Exception:
                pass
            self.running = False


# ==================== 交互式调试会话 ====================


class DebugSession:
    """交互式调试会话 (断点命中 / --step 单步共用同一命令集)。

    input_fn 可注入 (默认 input), 便于测试与 GUI 接入。
    """

    def __init__(self, cpu: 'CPU', input_fn=None):
        self.cpu = cpu
        self.input_fn = input_fn or input
        self.console = cpu.console
        self.server: Optional[DebugServer] = None

    def _ensure_server(self) -> DebugServer:
        """会话共用 CPU 上持久化的 DebugServer (保留条件断点/历史)。"""
        if self.cpu.debug_server is None:
            self.cpu.debug_server = DebugServer(self.cpu)
        self.server = self.cpu.debug_server
        return self.server

    # ---------------- 打印目标 ----------------

    def _print_value(self, parts: List[str]) -> None:
        if len(parts) < 2:
            self.console.print(f"{Colors.colorize('Missing argument', Colors.RED)}")
            return
        cpu = self.cpu
        target = parts[1]
        if target.lower().startswith('x') and target[1:].isdigit():
            idx = int(target[1:])
            v = cpu._reg(idx)
            self.console.print(f"{target.upper()} = {v} (0x{v:x})")
        elif target == 'regs':
            cpu.regs.display_registers(console=self.console)
        elif target == 'mem':
            if len(parts) > 2:
                try:
                    addr = int(parts[2], 0)
                    value = cpu.memory.read_qword(addr)
                    self.console.print(f"mem[{addr:#x}] = {value} (0x{value:x})")
                except ValueError:
                    self.console.print(f"{Colors.colorize('Invalid address', Colors.RED)}")
            else:
                cpu.memory.display_memory(console=self.console)
        elif target == 'cache':
            self.console.print(f"Cache stats: {cpu.cache.get_stats()}")
        elif target == 'pc':
            self.console.print(f"PC: {cpu.pc:#x}")
        elif target == 'sp':
            self.console.print(f"SP: {cpu.sp:#x}")
        else:
            self.console.print(f"{Colors.colorize('Unknown target', Colors.RED)}")

    def _set_breakpoint(self, parts: List[str]) -> None:
        cpu = self.cpu
        if len(parts) < 2:
            self.console.print(f"{Colors.colorize('Missing address', Colors.RED)}")
            return
        try:
            addr = int(parts[1], 0)
        except ValueError:
            self.console.print(f"{Colors.colorize('Invalid address', Colors.RED)}")
            return
        if len(parts) > 2:
            condition = ' '.join(parts[2:])
            self._ensure_server().add_conditional_breakpoint(addr, condition)
            self.console.print(
                f"{Colors.colorize(f'Conditional breakpoint set at {addr:#x}: {condition}', Colors.GREEN)}")
        else:
            cpu.add_breakpoint(addr)
            self.console.print(
                f"{Colors.colorize(f'Breakpoint set at {addr:#x}', Colors.GREEN)}")

    def _set_watchpoint(self, parts: List[str]) -> None:
        cpu = self.cpu
        if len(parts) < 2:
            self.console.print(f"{Colors.colorize('Missing address', Colors.RED)}")
            return
        try:
            addr = int(parts[1], 0)
            access = parts[2] if len(parts) > 2 else 'rw'
            cpu.memory.set_protection(addr, access)
            self.console.print(
                f"{Colors.colorize(f'Watchpoint set at {addr:#x} for {access}', Colors.GREEN)}")
        except ValueError:
            self.console.print(f"{Colors.colorize('Invalid address', Colors.RED)}")

    def _list_breakpoints(self) -> None:
        cpu = self.cpu
        self.console.print(f"{Colors.colorize('Breakpoints:', Colors.BOLD)}")
        for addr in sorted(cpu.breakpoints):
            self.console.print(f"  {addr:#x}")
        for bp in self._ensure_server().conditional_breakpoints:
            self.console.print(f"  {bp.address:#x} (cond: {bp.condition}, "
                               f"hits: {bp.hit_count})")

    # ---------------- 命令分发 ----------------

    def run(self) -> str:
        """断点命中时进入的会话主循环。

        返回 'continue' | 'quit' | 'halted'。
        """
        cpu = self.cpu
        cpu.is_debugging = True
        server = self._ensure_server()
        self.console.print(
            f"{Colors.colorize('Debug mode (type help for commands)', Colors.BLUE, True)}")

        while cpu.is_debugging:
            try:
                raw = self.input_fn(f"{Colors.colorize('dbg>', Colors.YELLOW)} ").strip()
            except (EOFError, KeyboardInterrupt):
                cpu.is_debugging = False
                return 'continue'
            if not raw:
                continue
            parts = raw.split()
            command = parts[0].lower()

            if command in ('help', 'h'):
                self.console.print(DEBUG_HELP)
            elif command in ('continue', 'c'):
                cpu.is_debugging = False
                # 建议 5: 继续时豁免当前 PC 一次, 避免立即再次命中同一断点
                cpu._resume_bp_pc = cpu.pc
                return 'continue'
            elif command in ('step', 's'):
                server.record_state()
                if not cpu.step():
                    cpu.is_debugging = False
                    return 'halted'
                display_state(cpu, "Step Execution")
            elif command in ('reverse',):
                if server.reverse_step():
                    self.console.print(f"{Colors.colorize('Reversed one step', Colors.GREEN)}")
                    display_state(cpu, "Reverse Step")
                else:
                    self.console.print(f"{Colors.colorize('No history available', Colors.RED)}")
            elif command == 'forward':
                if server.forward_step():
                    self.console.print(f"{Colors.colorize('Forward one step', Colors.GREEN)}")
                    display_state(cpu, "Forward Step")
                else:
                    self.console.print(f"{Colors.colorize('No forward history', Colors.RED)}")
            elif command in ('print', 'p'):
                self._print_value(parts)
            elif command == 'break':
                self._set_breakpoint(parts)
            elif command == 'delete':
                if len(parts) > 1:
                    try:
                        cpu.remove_breakpoint(int(parts[1]))
                    except ValueError:
                        self.console.print(f"{Colors.colorize('Invalid address', Colors.RED)}")
            elif command == 'watch':
                self._set_watchpoint(parts)
            elif command in ('list', 'info'):
                self._list_breakpoints()
            elif command in ('quit', 'q'):
                cpu.is_debugging = False
                cpu.running = False     # 优雅退出运行 (原 sys.exit(0) 的替代)
                return 'quit'
            else:
                self.console.print(f"{Colors.colorize('Unknown command', Colors.RED)}")
        return 'continue'

    def run_step_mode(self) -> str:
        """--step 单步模式: 每次执行前渲染状态并等待命令。

        返回 'next' | 'run' | 'quit' | 'halted'。
          - 'next': 已执行一条指令, 由外层继续 (通常不再使用, 见下)
          - 'run' : 用户选择 continue, 退出单步、自由运行
        """
        cpu = self.cpu
        server = self._ensure_server()
        while cpu.running:
            if cpu.pc < 0 or cpu.pc >= len(cpu.instructions):
                return 'halted'
            opcode, args = cpu.instructions[cpu.pc]
            display_state(cpu, "Step Execution", opcode, args)
            try:
                raw = self.input_fn(f"{Colors.colorize('step>', Colors.YELLOW)} ").strip()
            except (EOFError, KeyboardInterrupt):
                return 'quit'
            if not raw or raw.lower() in ('s', 'step'):
                server.record_state()
                if not cpu.step():
                    return 'halted'
                continue
            parts = raw.split()
            command = parts[0].lower()
            if command in ('c', 'continue', 'r', 'run'):
                return 'run'
            if command in ('q', 'quit'):
                cpu.running = False
                return 'quit'
            if command in ('p', 'print'):
                self._print_value(parts)
            elif command == 'b':
                self._set_breakpoint(parts)
            elif command == 'd':
                if len(parts) > 1:
                    try:
                        cpu.remove_breakpoint(int(parts[1]))
                    except ValueError:
                        self.console.print(f"{Colors.colorize('Invalid address', Colors.RED)}")
            elif command in ('list', 'info', 'l'):
                self._list_breakpoints()
            elif command in ('help', 'h', '?'):
                self.console.print(DEBUG_HELP)
            else:
                self.console.print(f"{Colors.colorize('Unknown command', Colors.RED)}")
        return 'quit'
