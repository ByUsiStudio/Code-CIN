import math
import os
import random
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .assembler import Assembler
from .cache import Cache
from .config import Config
from .console import Colors, Console, Panel
from .errors import CPUSimulatorError, ExecutionError
from .isa import Constants, Syscall
from .logger import Logger
from .memory import FastMemory
from .registers import RegisterFile, VectorRegisterFile
from .stats import Statistics

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]

MASK64 = 0xFFFFFFFFFFFFFFFF
MASK32 = 0xFFFFFFFF


def _f_to_bits(f: float) -> int:
    return struct.unpack('<Q', struct.pack('<d', f))[0]


def _bits_to_f(b: int) -> float:
    return struct.unpack('<d', struct.pack('<Q', b & MASK64))[0]


def _format_float(v: float) -> str:
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    s = repr(v)
    if 'e' not in s and 'E' not in s and '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s or "0"


class CPU:
    # 建议 7: 指令处理器自动注册 (方法名 _op_XXX -> 指令 XXX)。
    # 别名: 无事件模型的 ARM64 等待/事件指令, 语义上等价 NOP (预留扩展点)。
    _OP_HANDLER_PREFIX = '_op_'
    _OP_ALIASES = {
        'WFE': '_op_nop',   # Wait For Event: 无中断模型, 视作 NOP
        'WFI': '_op_nop',   # Wait For Interrupt: 无中断模型, 视作 NOP
        'SEV': '_op_nop',   # Send Event: 无多核事件模型, 视作 NOP
    }

    def __init__(self, config: Config, filename: Optional[str] = None,
                 crom_file: Optional[str] = None,
                 from_bin: bool = False,
                 console: Optional[Console] = None):

        self.console = console or Console()
        self.config = config
        self.filename = filename

        # debug 模式强制 DEBUG 级别 (超详细日志)
        if config.debug_mode and config.log_level.upper() != 'DEBUG':
            config.log_level = 'DEBUG'
        self.logger = Logger(self.console, config.log_level)
        if config.log_file:
            self.logger.set_log_file(config.log_file)
        # 逐指令超详细追踪开关 (DEBUG 级别启用)
        self._trace = self.logger.is_debug

        self.memory = FastMemory(config.mem_size)
        if config.mmu:
            from .memory import Mmu
            self.memory.attach_mmu(Mmu(config.mem_size))
        self.cache = Cache(config.cache_size, config.cache_assoc)
        # 内存访问日志 (DEBUG 级别生效)
        self.memory.attach_logger(self.logger)

        self.regs = RegisterFile(self.console)
        self.vec_regs = VectorRegisterFile()

        self.pstate: Dict[str, bool] = {'N': False, 'Z': False, 'C': False, 'V': False}
        self.pc = 0
        self.sp = (config.mem_size - Constants.STACK_SLOT) & ~0x7
        self.heap_ptr = config.mem_size // 2

        self.logger.dump("CPU 初始化", {
            'memory': f"0x{config.mem_size:x} bytes",
            'cache': f"{config.cache_size} lines x {config.cache_assoc}-way",
            'sp_init': f"0x{self.sp:x}",
            'heap_base': f"0x{self.heap_ptr:x}",
            'native': config.use_native,
            'jit': config.enable_jit,
            'log_level': config.log_level,
        })

        self.instructions: List[Instruction] = []
        self.labels: Dict[str, int] = {}
        self.data_labels: Dict[str, int] = {}
        self.entry_pc = 0

        self.stats = Statistics()
        self.jit = None
        if config.enable_jit and not config.debug_mode and not config.step_mode:
            try:
                from .jit import JITCompiler
                self.jit = JITCompiler(logger=self.logger)
                self.logger.info("JIT compiler enabled")
            except Exception as e:
                self.logger.warning(f"JIT unavailable: {e}")
        self.running = False
        self.is_debugging = False
        self.breakpoints: set = set()
        self.debug_server = None
        # 建议 5: 'continue' 后豁免一次当前 PC 的断点命中 (防止原地重入)
        self._resume_bp_pc: Optional[int] = None

        self.input_buffer: str = ""
        self._input_pos = 0
        self.output_buffer: List[str] = []
        self._capture_output = False
        self.native_engine = None
        self.native_used = False
        # 执行期是否发生过错误 (供 CLI 决定退出码; 旧实现任何运行时错误
        # 都只记日志, 进程仍以 0 退出)
        self.execution_failed = False

        self._sys_buffers = [bytearray(64) for _ in range(8)]
        self._sys_buf_idx = 0
        self._rand = random.Random(
            config.seed if config.seed is not None else None)

        self._init_dispatch()

        if filename:
            self.load_program(filename, crom_file=crom_file, from_bin=from_bin)

    def load_program(self, filename: str, crom_file: Optional[str] = None,
                     from_bin: bool = False) -> None:
        if not os.path.exists(filename):
            raise CPUSimulatorError(f"File '{filename}' not found")

        ext = os.path.splitext(filename)[1].lower()

        if ext == '.cin':
            from .cin import CINCompiler
            compiler = CINCompiler(self.console, logger=self.logger)
            result = compiler.compile(filename,
                                      bounds_check=self.config.bounds_check)
            self.instructions = result.instructions
            self.labels = result.labels
            self.data_labels = result.data_labels
            for addr, data in result.data_writes:
                self.memory.write_block(addr, data)
            # pc 0 为 bootstrap (CALL main; HALT), main 标签为函数体入口
            self.entry_pc = 0
            self.pc = 0
            self.logger.info(f"CIN compiled: {len(self.instructions)} instructions")
            return

        if from_bin or ext == '.bin':
            from . import crom as crom_mod
            crom_mod.load_bin(self, filename)
            self.logger.info(f"Binary loaded: {len(self.instructions)} instructions")
            return

        # .pl / .asm
        if crom_file is None:
            base = os.path.splitext(filename)[0]
            candidate = base + '.crom'
            if os.path.exists(candidate):
                crom_file = candidate
        if crom_file and os.path.exists(crom_file):
            from . import crom as crom_mod
            crom_mod.load_crom(self.memory, crom_file, self.logger,
                               enable_mmu=self.config.mmu)

        asm = Assembler(self.memory, self.console, strict=self.config.strict_mode,
                        logger=self.logger)
        self.instructions, self.labels, self.data_labels = asm.assemble_file(filename)
        self.entry_pc = self.labels.get('main', 0)
        self.pc = self.entry_pc
        self.cache.warmup(self.instructions)
        self.logger.info(f"Assembly successful: {len(self.instructions)} instructions")
        if self.logger.is_debug:
            self.logger.dump("汇编标签表", {name: f"0x{pc:x}"
                                        for name, pc in
                                        sorted(self.labels.items())})
    def _init_dispatch(self) -> None:
        """自动注册指令处理器 (建议 7)。

        约定: CPU 上任意名为 ``_op_XXX`` 的可调用方法即指令 ``XXX`` 的处理器,
        不再手工维护 112 行映射表; 别名 (WFE/WFI/SEV -> NOP 语义) 见 _OP_ALIASES。
        dispatch 键集合由 test_dispatch_complete 与 Opcode 枚举逐一校验。
        """
        t: Dict[str, Any] = {}
        prefix = self._OP_HANDLER_PREFIX
        for name in dir(self):
            if not name.startswith(prefix):
                continue
            opcode_name = name[len(prefix):].upper()
            handler = getattr(self, name)
            if opcode_name and callable(handler):
                t.setdefault(opcode_name, handler)
        for opcode_name, handler_name in self._OP_ALIASES.items():
            t[opcode_name] = getattr(self, handler_name)
        self._dispatch = t

    # ==================== 操作数辅助 ====================

    def _reg(self, n: int) -> int:
        if n == Constants.SP_REG:
            return self.sp & MASK64
        return self.regs.read(n)

    def _set_reg(self, n: int, v: int) -> None:
        v &= MASK64
        if self._trace:
            old = self._reg(n)
            if old != v:
                sv = v if v < (1 << 63) else v - (1 << 64)
                self.logger.trace(f"  R[{n}] {self._fmt_reg(n, old)} -> "
                                  f"0x{v:016x} ({sv})")
        if n == Constants.SP_REG:
            self.sp = v
        else:
            self.regs.write(n, v)

    @staticmethod
    def _fmt_reg(n: int, v: int) -> str:
        return f"0x{v:016x}"

    def _fmt_op_trace(self, op: Operand) -> str:
        """格式化操作数并附带当前值 (超详细追踪)。"""
        kind = op[0]
        try:
            if kind == 'reg':
                n = op[1]
                v = self._reg(n)
                return f"X{n}=0x{v:x}({v if v < (1 << 63) else v - (1 << 64)})"
            if kind == 'imm':
                return f"#{op[1]}"
            if kind == 'mem':
                addr = self._mem_addr(op)
                return (f"[{op[1] if op[1] >= 0 else 'abs'}"
                        f"{op[2]:+d}]@0x{addr:x}"
                        f"=0x{self.memory.read_qword(addr):x}")
            if kind == 'cond':
                return (f"{op[1]}="
                        f"{int(self._condition(op[1]))}"
                        f"{{N={int(self.pstate['N'])} Z={int(self.pstate['Z'])} "
                        f"C={int(self.pstate['C'])} V={int(self.pstate['V'])}}}")
            if kind == 'label':
                return f"{op[1]}->0x{self.labels.get(op[1], -1):x}"
            if kind == 'str':
                return f'"@{op[1]}"'
            if kind == 'float':
                return f"#{op[1]}f"
        except Exception:
            pass
        return self._fmt_operand(op)

    def _trace_flags(self) -> str:
        p = self.pstate
        return (f"N={int(p['N'])} Z={int(p['Z'])} "
                f"C={int(p['C'])} V={int(p['V'])}")

    def _trace_cache(self, addr: int, hit: bool, kind: str) -> None:
        cs = self.cache.get_stats()
        self.logger.trace(
            f"  CACHE {'HIT ' if hit else 'MISS'} {kind} @0x{addr:x} "
            f"(hit_rate={cs['hit_rate'] * 100:.1f}%)")

    def _val(self, op: Operand) -> Union[int, float]:
        kind = op[0]
        if kind == 'reg':
            return self._reg(op[1])
        if kind == 'imm':
            return op[1] & MASK64
        if kind == 'vec':
            return int(self.vec_regs.read_scalar(op[1]))
        if kind == 'veclane':
            return int(self.vec_regs.read_scalar(op[1], op[2]))
        if kind == 'float':
            return op[1]
        if kind == 'mem':
            addr = self._mem_addr(op)
            return self.memory.read_qword(addr)
        if kind == 'cond':
            return 1 if self._condition(op[1]) else 0
        if kind == 'str':
            return op[1]
        if kind == 'label':
            target = self.labels.get(op[1])
            if target is None:
                raise ExecutionError(f"Undefined label: {op[1]}")
            return target
        raise ExecutionError(f"Unsupported operand kind: {kind}")

    def _mem_addr(self, op: Operand) -> int:
        # ('mem', base, off): base == -1 表示绝对地址
        base = op[1] if len(op) > 1 else -1
        off = op[2] if len(op) > 2 else 0
        if isinstance(off, tuple):
            off = self._val(off)
        if base >= 0:
            return (self._reg(base) + off) & MASK64
        return off & MASK64

    def _condition(self, cond: str) -> bool:
        n, z, c, v = (self.pstate['N'], self.pstate['Z'],
                      self.pstate['C'], self.pstate['V'])
        table = {
            'EQ': z, 'NE': not z,
            'CS': c, 'CC': not c,
            'MI': n, 'PL': not n,
            'VS': v, 'VC': not v,
            'HI': c and not z, 'LS': not c or z,
            'GE': n == v, 'LT': n != v,
            'GT': not z and (n == v), 'LE': z or (n != v),
            'AL': True, 'NV': False,
        }
        return table.get(cond.upper(), False)

    def _set_flags_sub(self, a: int, b: int, result: int) -> None:
        a &= MASK64
        b &= MASK64
        result &= MASK64
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        sr = sa - sb
        old = dict(self.pstate)
        self.pstate['Z'] = (result == 0)
        self.pstate['N'] = bool(result & (1 << 63))
        self.pstate['C'] = a >= b            # 无借位
        self.pstate['V'] = (sr > (1 << 63) - 1) or (sr < -(1 << 63))
        if self._trace and old != self.pstate:
            self.logger.trace(f"  FLAGS <- {self._trace_flags()} "
                              f"(a=0x{a:x} b=0x{b:x})")

    def _set_flags_add(self, a: int, b: int, result: int) -> None:
        a &= MASK64
        b &= MASK64
        result &= MASK64
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        ss = sa + sb
        old = dict(self.pstate)
        self.pstate['Z'] = (result == 0)
        self.pstate['N'] = bool(result & (1 << 63))
        self.pstate['C'] = (a + b) > MASK64
        self.pstate['V'] = (ss > (1 << 63) - 1) or (ss < -(1 << 63))
        if self._trace and old != self.pstate:
            self.logger.trace(f"  FLAGS <- {self._trace_flags()} "
                              f"(a=0x{a:x} b=0x{b:x})")

    # ==================== 栈 ====================

    def _push(self, value: int) -> None:
        self.sp = (self.sp - Constants.STACK_SLOT) & MASK64
        if self.sp < self.heap_ptr + 4096:
            raise ExecutionError("Stack overflow (collides with heap)")
        self.memory.write_qword(self.sp, value & MASK64)
        if self._trace:
            self.logger.trace(f"  PUSH @0x{self.sp:x} <- "
                              f"0x{value & MASK64:016x}")

    def _pop(self) -> int:
        if self.sp >= len(self.memory) - Constants.STACK_SLOT:
            raise ExecutionError("Stack underflow")
        value = self.memory.read_qword(self.sp)
        self.sp = (self.sp + Constants.STACK_SLOT) & MASK64
        if self._trace:
            self.logger.trace(f"  POP  @0x{self.sp - 8:x} -> "
                              f"0x{value:016x}")
        return value

    def _check_stack(self) -> None:
        if self.sp < self.heap_ptr + 4096:
            raise ExecutionError("Stack overflow (collides with heap)")

    # ==================== I/O ====================

    def _emit_text(self, text: str) -> None:
        if self._capture_output:
            self.output_buffer.append(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    def _read_input_int(self) -> int:
        if self._input_pos < len(self.input_buffer):
            end = self.input_buffer.find('\n', self._input_pos)
            if end < 0:
                end = len(self.input_buffer)
            line = self.input_buffer[self._input_pos:end].strip()
            self._input_pos = end + 1
            try:
                return int(line)
            except ValueError:
                return 0
        try:
            return int(input())
        except (ValueError, EOFError):
            return 0

    # ==================== 基础指令 ====================

    def _op_mov(self, args):
        self._set_reg(args[0][1], self._val(args[1]))
        return True

    def _op_load(self, args):
        rd = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_read()
        hit = self.cache.read(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'R')
        self._set_reg(rd, self.memory.read_dword(addr))
        return True

    def _op_store(self, args):
        rs = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_write()
        hit = self.cache.write(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'W')
        self.memory.write_dword(addr, self._reg(rs))
        return True

    def _op_add(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) + self._val(args[1]))
        return True

    def _op_sub(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) - self._val(args[1]))
        return True

    def _op_mul(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) * self._val(args[1]))
        return True

    def _op_div(self, args):
        rd = args[0][1]
        d = self._val(args[1])
        if d == 0:
            raise ExecutionError("Division by zero")
        a = self._reg(rd)
        self._set_reg(rd, self._sdiv(a, d))
        return True

    @staticmethod
    def _sdiv(a: int, b: int) -> int:
        """有符号 64 位除法。"""
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        q = abs(sa) // abs(sb)
        if (sa < 0) != (sb < 0):
            q = -q
        return q & MASK64

    def _op_and(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) & self._val(args[1]))
        return True

    def _op_or(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) | self._val(args[1]))
        return True

    def _op_xor(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) ^ self._val(args[1]))
        return True

    def _op_shl(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) << (self._val(args[1]) & 63))
        return True

    def _op_shr(self, args):
        rd = args[0][1]
        self._set_reg(rd, (self._reg(rd) & MASK64) >> (self._val(args[1]) & 63))
        return True

    def _op_inc(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) + 1)
        return True

    def _op_dec(self, args):
        rd = args[0][1]
        self._set_reg(rd, self._reg(rd) - 1)
        return True

    def _op_cmp(self, args):
        a = self._val(args[0])
        b = self._val(args[1])
        self._set_flags_sub(a, b, (a - b) & MASK64)
        return True

    def _op_jmp(self, args):
        self.pc = self._val(args[0])
        return True

    def _op_jz(self, args):
        if self.pstate['Z']:
            self.pc = self._val(args[0])
        return True

    def _op_jnz(self, args):
        if not self.pstate['Z']:
            self.pc = self._val(args[0])
        return True

    def _op_je(self, args):
        if self.pstate['Z']:
            self.pc = self._val(args[0])
        return True

    def _op_jl(self, args):
        if self.pstate['N'] != self.pstate['V']:
            self.pc = self._val(args[0])
        return True

    def _op_jg(self, args):
        if not self.pstate['Z'] and self.pstate['N'] == self.pstate['V']:
            self.pc = self._val(args[0])
        return True

    def _op_push(self, args):
        self._check_stack()
        self._push(self._val(args[0]))
        return True

    def _op_pop(self, args):
        self._set_reg(args[0][1], self._pop())
        return True

    def _op_call(self, args):
        self._check_stack()
        self._push(self.pc)
        self.pc = self._val(args[0])
        return True

    def _op_ret(self, args):
        self.pc = self._pop()
        return True

    def _op_in(self, args):
        if self.config.allow_io:
            self._set_reg(args[0][1], self._read_input_int())
        return True

    def _op_out(self, args):
        if not self.config.allow_io:
            return True
        op = args[0]
        if op[0] == 'str':
            text = self.memory.read_string(op[1])
        else:
            val = self._val(op)
            if op[0] == 'float' or (op[0] == 'vec') or (op[0] == 'veclane'):
                text = _format_float(float(val))
            else:
                iv = int(val)
                text = '\n' if iv == 10 else str(iv)
        if self._trace:
            shown = text.replace('\n', '\\n')
            self.logger.trace(f"  OUT -> {shown!r}")
        self._emit_text(text)
        return True

    def _op_halt(self, args):
        return False

    # ==================== ARM64 扩展 ====================

    def _op_adds(self, args):
        rd, rn = args[0][1], args[1][1]
        a, b = self._reg(rn), self._val(args[2])
        r = (a + b) & MASK64
        self._set_flags_add(a, b, r)
        self._set_reg(rd, r)
        return True

    def _op_subs(self, args):
        rd, rn = args[0][1], args[1][1]
        a, b = self._reg(rn), self._val(args[2])
        r = (a - b) & MASK64
        self._set_flags_sub(a, b, r)
        self._set_reg(rd, r)
        return True

    def _op_addc(self, args):
        rd, rn = args[0][1], args[1][1]
        a, b = self._reg(rn), self._val(args[2])
        r = (a + b + (1 if self.pstate['C'] else 0)) & MASK64
        self._set_flags_add(a, b, r)
        self._set_reg(rd, r)
        return True

    def _op_subc(self, args):
        rd, rn = args[0][1], args[1][1]
        a, b = self._reg(rn), self._val(args[2])
        r = (a - b - (0 if self.pstate['C'] else 1)) & MASK64
        self._set_flags_sub(a, b, r)
        self._set_reg(rd, r)
        return True

    def _op_lsl(self, args):
        rd, rn = args[0][1], args[1][1]
        self._set_reg(rd, (self._reg(rn) << (self._val(args[2]) & 63)) & MASK64)
        return True

    def _op_lsr(self, args):
        rd, rn = args[0][1], args[1][1]
        self._set_reg(rd, (self._reg(rn) & MASK64) >> (self._val(args[2]) & 63))
        return True

    def _op_asr(self, args):
        rd, rn = args[0][1], args[1][1]
        v = self._reg(rn)
        sv = v if v < (1 << 63) else v - (1 << 64)
        r = sv >> (self._val(args[2]) & 63)
        self._set_reg(rd, r & MASK64)
        return True

    def _op_ror(self, args):
        rd, rn = args[0][1], args[1][1]
        v = self._reg(rn) & MASK64
        amt = self._val(args[2]) & 63
        r = ((v >> amt) | (v << (64 - amt))) & MASK64 if amt else v
        self._set_reg(rd, r)
        return True

    def _op_mvn(self, args):
        self._set_reg(args[0][1], (~self._val(args[1])) & MASK64)
        return True

    def _op_eor(self, args):
        rd, rn = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rn) ^ self._val(args[2]))
        return True

    def _op_bic(self, args):
        rd, rn = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rn) & (~self._val(args[2]) & MASK64))
        return True

    def _op_orn(self, args):
        rd, rn = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rn) | (~self._val(args[2]) & MASK64))
        return True

    def _op_ldr(self, args):
        rd = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_read()
        hit = self.cache.read(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'R')
        self._set_reg(rd, self.memory.read_dword(addr))
        return True

    def _op_str(self, args):
        rs = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_write()
        hit = self.cache.write(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'W')
        self.memory.write_dword(addr, self._reg(rs))
        return True

    def _op_ldp(self, args):
        rt, rt2 = args[0][1], args[1][1]
        addr = self._mem_addr(args[2]) if args[2][0] == 'mem' else self._val(args[2])
        self.stats.record_memory_read()
        self._set_reg(rt, self.memory.read_qword(addr))
        self._set_reg(rt2, self.memory.read_qword(addr + 8))
        return True

    def _op_stp(self, args):
        rt, rt2 = args[0][1], args[1][1]
        addr = self._mem_addr(args[2]) if args[2][0] == 'mem' else self._val(args[2])
        self.stats.record_memory_write()
        self.memory.write_qword(addr, self._reg(rt))
        self.memory.write_qword(addr + 8, self._reg(rt2))
        return True

    def _op_cbz(self, args):
        if self._reg(args[0][1]) == 0:
            self.pc = self._val(args[1])
        return True

    def _op_cbnz(self, args):
        if self._reg(args[0][1]) != 0:
            self.pc = self._val(args[1])
        return True

    def _op_tbz(self, args):
        rn, bit = self._reg(args[0][1]), self._val(args[1]) & 63
        if not (rn & (1 << bit)):
            self.pc = self._val(args[2])
        return True

    def _op_tbnz(self, args):
        rn, bit = self._reg(args[0][1]), self._val(args[1]) & 63
        if rn & (1 << bit):
            self.pc = self._val(args[2])
        return True

    def _op_b(self, args):
        # 可选条件操作数 ('cond', 'EQ'); 条件不满足则不跳转
        cond_op = next((a for a in args if a[0] == 'cond'), None)
        if cond_op is not None and not self._condition(cond_op[1]):
            return True
        for op in args:
            if op[0] != 'cond':
                self.pc = self._val(op)
        return True

    def _op_bl(self, args):
        self._check_stack()
        self._push(self.pc)
        self.pc = self._val(args[0])
        return True

    def _op_br(self, args):
        self.pc = self._reg(args[0][1])
        return True

    def _op_nop(self, args):
        return True

    def _op_csel(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2]
        cond = args[3][1] if args[3][0] == 'cond' else 'AL'
        self._set_reg(rd, self._reg(rn) if self._condition(cond) else self._val(rm))
        return True

    def _op_csinc(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2]
        cond = args[3][1] if args[3][0] == 'cond' else 'AL'
        if self._condition(cond):
            self._set_reg(rd, self._reg(rn))
        else:
            self._set_reg(rd, self._val(rm) + 1)
        return True

    def _op_csinv(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2]
        cond = args[3][1] if args[3][0] == 'cond' else 'AL'
        if self._condition(cond):
            self._set_reg(rd, self._reg(rn))
        else:
            self._set_reg(rd, (~self._val(rm)) & MASK64)
        return True

    def _op_csneg(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2]
        cond = args[3][1] if args[3][0] == 'cond' else 'AL'
        if self._condition(cond):
            self._set_reg(rd, self._reg(rn))
        else:
            self._set_reg(rd, (-self._val(rm)) & MASK64)
        return True

    @staticmethod
    def _sign_extend(v: int, bits: int) -> int:
        sign = 1 << (bits - 1)
        v &= (1 << bits) - 1
        return (v ^ sign) - sign

    def _op_sxtb(self, args):
        self._set_reg(args[0][1], self._sign_extend(self._val(args[1]), 8) & MASK64)
        return True

    def _op_sxth(self, args):
        self._set_reg(args[0][1], self._sign_extend(self._val(args[1]), 16) & MASK64)
        return True

    def _op_sxtw(self, args):
        self._set_reg(args[0][1], self._sign_extend(self._val(args[1]), 32) & MASK64)
        return True

    def _op_uxtb(self, args):
        self._set_reg(args[0][1], self._val(args[1]) & 0xFF)
        return True

    def _op_uxth(self, args):
        self._set_reg(args[0][1], self._val(args[1]) & 0xFFFF)
        return True

    def _op_clz(self, args):
        v = self._val(args[1]) & MASK64
        self._set_reg(args[0][1], 64 - v.bit_length() if v else 64)
        return True

    def _op_cls(self, args):
        v = self._val(args[1]) & MASK64
        sv = v if v < (1 << 63) else v - (1 << 64)
        if sv >= 0:
            self._set_reg(args[0][1], 63 - sv.bit_length() if sv else 63)
        else:
            self._set_reg(args[0][1], 63 - (~sv & MASK64).bit_length() + 1)
        return True

    def _op_rbit(self, args):
        v = self._val(args[1]) & MASK64
        r = int(f'{v:064b}'[::-1], 2)
        self._set_reg(args[0][1], r)
        return True

    def _op_rev(self, args):
        v = self._val(args[1]) & MASK64
        b = v.to_bytes(8, 'little')
        self._set_reg(args[0][1], int.from_bytes(b[::-1], 'little'))
        return True

    # ==================== 浮点 / 向量 ====================

    def _op_fadd(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2][1]
        self.vec_regs.write_scalar(rd, self.vec_regs.read_scalar(rn) + self.vec_regs.read_scalar(rm))
        return True

    def _op_fsub(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2][1]
        self.vec_regs.write_scalar(rd, self.vec_regs.read_scalar(rn) - self.vec_regs.read_scalar(rm))
        return True

    def _op_fmul(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2][1]
        self.vec_regs.write_scalar(rd, self.vec_regs.read_scalar(rn) * self.vec_regs.read_scalar(rm))
        return True

    def _op_fdiv(self, args):
        rd, rn, rm = args[0][1], args[1][1], args[2][1]
        b = self.vec_regs.read_scalar(rm)
        if b == 0:
            raise ExecutionError("Float division by zero")
        self.vec_regs.write_scalar(rd, self.vec_regs.read_scalar(rn) / b)
        return True

    def _op_fcmp(self, args):
        rn, rm = args[0][1], args[1][1]
        a, b = self.vec_regs.read_scalar(rn), self.vec_regs.read_scalar(rm)
        self.pstate['Z'] = (a == b)
        self.pstate['N'] = (a < b)
        self.pstate['C'] = (a >= b)
        self.pstate['V'] = False
        return True

    def _op_fcvt(self, args):
        rd, rs = args[0][1], args[1][1]
        if args[1][0] in ('vec', 'veclane'):
            # float -> int
            self._set_reg(rd, int(self.vec_regs.read_scalar(rs)))
        else:
            # int -> float
            self.vec_regs.write_scalar(rd, float(self._reg(rs)))
        return True

    def _op_fabs(self, args):
        self.vec_regs.write_scalar(args[0][1], abs(self.vec_regs.read_scalar(args[1][1])))
        return True

    def _op_fneg(self, args):
        self.vec_regs.write_scalar(args[0][1], -self.vec_regs.read_scalar(args[1][1]))
        return True

    def _op_ldrs(self, args):
        rd = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_read()
        self.vec_regs.write_scalar(rd, self.memory.read_float(addr))
        return True

    def _op_strs(self, args):
        rs = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_write()
        self.memory.write_float(addr, self.vec_regs.read_scalar(rs))
        return True

    def _vec_bin(self, args, fn):
        rd, rn, rm = args[0][1], args[1][1], args[2][1]
        v1 = self.vec_regs.read_vector(rn)
        v2 = self.vec_regs.read_vector(rm)
        self.vec_regs.write_vector(rd, [fn(v1[i], v2[i]) for i in range(Constants.VECTOR_LANES)])
        return True

    def _op_vadd(self, args):
        return self._vec_bin(args, lambda a, b: a + b)

    def _op_vsub(self, args):
        return self._vec_bin(args, lambda a, b: a - b)

    def _op_vmul(self, args):
        return self._vec_bin(args, lambda a, b: a * b)

    def _op_vdiv(self, args):
        def f(a, b):
            if b == 0:
                raise ExecutionError("Vector division by zero")
            return a / b
        return self._vec_bin(args, f)

    def _op_vld1(self, args):
        rd = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_read()
        data = self.memory.read_block(addr, 16)
        self.vec_regs.write_vector(rd, [float(x) for x in struct.unpack('<4f', data)])
        return True

    def _op_vst1(self, args):
        rs = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_write()
        vec = self.vec_regs.read_vector(rs)
        self.memory.write_block(addr, struct.pack('<4f', *[float(x) for x in vec]))
        return True

    # ==================== RISC-V 扩展 ====================

    def _load_width(self, args, bits: int, signed: bool):
        rd = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_read()
        hit = self.cache.read(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'R')
        if bits == 8:
            v = self.memory.read_byte(addr)
            if signed:
                v = self._sign_extend(v, 8)
        elif bits == 16:
            v = self.memory.read_word(addr)
            if signed:
                v = self._sign_extend(v, 16)
        elif bits == 32:
            v = self.memory.read_dword(addr)
            if signed:
                v = self._sign_extend(v, 32)
        else:
            v = self.memory.read_qword(addr)
        self._set_reg(rd, v & MASK64)
        return True

    def _store_width(self, args, bits: int):
        rs = args[0][1]
        addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
        self.stats.record_memory_write()
        hit = self.cache.write(addr)
        self.stats.record_cache(hit)
        if self._trace:
            self._trace_cache(addr, hit, 'W')
        v = self._reg(rs)
        if bits == 8:
            self.memory.write_byte(addr, v & 0xFF)
        elif bits == 16:
            self.memory.write_word(addr, v & 0xFFFF)
        elif bits == 32:
            self.memory.write_dword(addr, v & MASK32)
        else:
            self.memory.write_qword(addr, v & MASK64)
        return True

    def _op_lb(self, args):
        return self._load_width(args, 8, True)

    def _op_lh(self, args):
        return self._load_width(args, 16, True)

    def _op_lw(self, args):
        return self._load_width(args, 32, True)

    def _op_ld(self, args):
        return self._load_width(args, 64, False)

    def _op_sb(self, args):
        return self._store_width(args, 8)

    def _op_sh(self, args):
        return self._store_width(args, 16)

    def _op_sw(self, args):
        return self._store_width(args, 32)

    def _op_sd(self, args):
        return self._store_width(args, 64)

    def _op_addi(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rs1) + self._val(args[2]))
        return True

    def _op_slti(self, args):
        rd, rs1 = args[0][1], args[1][1]
        a = self._reg(rs1)
        b = self._val(args[2])
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        self._set_reg(rd, 1 if sa < sb else 0)
        return True

    def _op_sltiu(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, 1 if (self._reg(rs1) & MASK64) < (self._val(args[2]) & MASK64) else 0)
        return True

    def _op_xori(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rs1) ^ self._val(args[2]))
        return True

    def _op_ori(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rs1) | self._val(args[2]))
        return True

    def _op_andi(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, self._reg(rs1) & self._val(args[2]))
        return True

    def _op_slli(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, (self._reg(rs1) << (self._val(args[2]) & 63)) & MASK64)
        return True

    def _op_srli(self, args):
        rd, rs1 = args[0][1], args[1][1]
        self._set_reg(rd, (self._reg(rs1) & MASK64) >> (self._val(args[2]) & 63))
        return True

    def _op_srai(self, args):
        rd, rs1 = args[0][1], args[1][1]
        v = self._reg(rs1)
        sv = v if v < (1 << 63) else v - (1 << 64)
        self._set_reg(rd, (sv >> (self._val(args[2]) & 63)) & MASK64)
        return True

    def _branch_rs(self, args, pred_taken: bool, predicted: bool):
        self.stats.record_branch(self.pc, pred_taken, predicted)
        if pred_taken:
            self.pc = self._val(args[2])

    def _op_beq(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        taken = self._reg(args[0][1]) == self._reg(args[1][1])
        self._branch_rs(args, taken, predicted)
        return True

    def _op_bne(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        taken = self._reg(args[0][1]) != self._reg(args[1][1])
        self._branch_rs(args, taken, predicted)
        return True

    def _op_blt(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        a, b = self._reg(args[0][1]), self._reg(args[1][1])
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        self._branch_rs(args, sa < sb, predicted)
        return True

    def _op_bge(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        a, b = self._reg(args[0][1]), self._reg(args[1][1])
        sa = a if a < (1 << 63) else a - (1 << 64)
        sb = b if b < (1 << 63) else b - (1 << 64)
        self._branch_rs(args, sa >= sb, predicted)
        return True

    def _op_bltu(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        taken = (self._reg(args[0][1]) & MASK64) < (self._reg(args[1][1]) & MASK64)
        self._branch_rs(args, taken, predicted)
        return True

    def _op_bgeu(self, args):
        predicted = self.stats.performance_counters.branch_predictor.predict(self.pc)
        taken = (self._reg(args[0][1]) & MASK64) >= (self._reg(args[1][1]) & MASK64)
        self._branch_rs(args, taken, predicted)
        return True

    def _op_jalr(self, args):
        # JALR rd, rs1, imm  /  jalr rs1  (单寄存器形式视为 ret, rd 丢弃)
        regs = [a[1] for a in args if a[0] == 'reg']
        imm = 0
        for a in args:
            if a[0] == 'imm':
                imm = a[1]
            elif a[0] == 'mem':
                imm = a[2]
                if a[1] >= 0 and len(regs) < 2:
                    regs.append(a[1])
        if len(regs) >= 2:
            rd, rs1 = regs[0], regs[1]
        elif len(regs) == 1:
            rd, rs1 = 31, regs[0]
        else:
            rd, rs1 = 31, 31
        if rd != 31:
            self._set_reg(rd, self.pc)
        self.pc = (self._reg(rs1) + imm) & MASK64
        return True

    def _op_jal(self, args):
        rd = args[0][1] if args[0][0] == 'reg' else 31
        if rd != 31:
            self._set_reg(rd, self.pc)
        target = args[1] if args[0][0] == 'reg' else args[0]
        self.pc = self._val(target)
        return True

    def _op_lui(self, args):
        rd = args[0][1]
        imm = self._val(args[1]) if args[1][0] != 'imm' else args[1][1]
        self._set_reg(rd, (imm & 0xFFFFF) << 12)
        return True

    def _op_auipc(self, args):
        rd = args[0][1]
        imm = self._val(args[1]) if args[1][0] != 'imm' else args[1][1]
        self._set_reg(rd, self.pc + ((imm & 0xFFFFF) << 12))
        return True

    # ==================== SYS 宿主调用 ====================

    def _heap_dup_string(self, data: bytes) -> int:
        """在堆上分配 NUL 结尾字符串副本, 返回指针。"""
        size = (len(data) + 15) & ~0xF
        ptr = self.heap_ptr
        self.heap_ptr += size
        if self.heap_ptr > self.sp:
            raise ExecutionError("Heap exhausted (string operation)")
        self.memory.write_block(ptr, data)
        return ptr

    def _sys_buffer(self) -> int:
        idx = self._sys_buf_idx % len(self._sys_buffers)
        self._sys_buf_idx += 1
        # 缓冲放在内存高地址区下方 (heap 之后)
        addr = self.heap_ptr + 2048 + idx * 64
        return addr

    def _op_sys(self, args):
        if not args or args[0][0] != 'imm':
            raise ExecutionError("SYS requires an immediate call id")
        call_id = args[0][1]
        x0 = self._reg(0)
        x1 = self._reg(1)
        x2 = self._reg(2)
        if self._trace:
            name = Syscall(call_id).name if call_id in Syscall._value2member_map_ \
                else f'UNKNOWN({call_id})'
            self.logger.trace(f"  SYS #{call_id} ({name}) "
                              f"x0=0x{x0:x} x1=0x{x1:x} x2=0x{x2:x}")

        if call_id == Syscall.ABS:
            self._set_reg(0, abs(x0 if x0 < (1 << 63) else x0 - (1 << 64)))
        elif call_id in (Syscall.SQRT,):
            self._set_reg(0, _f_to_bits(math.sqrt(_bits_to_f(x0))))
        elif call_id == Syscall.POW:
            self._set_reg(0, _f_to_bits(math.pow(_bits_to_f(x0), _bits_to_f(x1))))
        elif call_id == Syscall.SIN:
            self._set_reg(0, _f_to_bits(math.sin(_bits_to_f(x0))))
        elif call_id == Syscall.COS:
            self._set_reg(0, _f_to_bits(math.cos(_bits_to_f(x0))))
        elif call_id == Syscall.TAN:
            self._set_reg(0, _f_to_bits(math.tan(_bits_to_f(x0))))
        elif call_id == Syscall.FADD:
            self._set_reg(0, _f_to_bits(_bits_to_f(x0) + _bits_to_f(x1)))
        elif call_id == Syscall.FSUB:
            self._set_reg(0, _f_to_bits(_bits_to_f(x0) - _bits_to_f(x1)))
        elif call_id == Syscall.FMUL:
            self._set_reg(0, _f_to_bits(_bits_to_f(x0) * _bits_to_f(x1)))
        elif call_id == Syscall.FDIV:
            b = _bits_to_f(x1)
            if b == 0:
                raise ExecutionError("Float division by zero (SYS)")
            self._set_reg(0, _f_to_bits(_bits_to_f(x0) / b))
        elif call_id == Syscall.FCMP:
            a, b = _bits_to_f(x0), _bits_to_f(x1)
            self._set_reg(0, -1 if a < b else (1 if a > b else 0))
        elif call_id == Syscall.FTOI:
            self._set_reg(0, int(_bits_to_f(x0)))
        elif call_id == Syscall.ITOF:
            self._set_reg(0, _f_to_bits(float(x0 if x0 < (1 << 63) else x0 - (1 << 64))))
        elif call_id == Syscall.RAND:
            self._set_reg(0, self._rand.randrange(1 << 31))
        elif call_id == Syscall.SRAND:
            self._rand.seed(x0)
        elif call_id == Syscall.TIME:
            self._set_reg(0, int(time.time()))
        elif call_id == Syscall.STRLEN:
            n = 0
            while self.memory.read_byte(x0 + n) != 0:
                n += 1
            self._set_reg(0, n)
        elif call_id == Syscall.STRCMP:
            i = 0
            while True:
                ca = self.memory.read_byte(x0 + i)
                cb = self.memory.read_byte(x1 + i)
                if ca != cb or ca == 0:
                    self._set_reg(0, ca - cb)
                    break
                i += 1
        elif call_id in (Syscall.STRCPY, Syscall.STRCAT):
            dst = x0
            if call_id == Syscall.STRCAT:
                while self.memory.read_byte(dst) != 0:
                    dst += 1
            src = x1
            i = 0
            while True:
                ch = self.memory.read_byte(src + i)
                self.memory.write_byte(dst + i, ch)
                i += 1
                if ch == 0:
                    break
            self._set_reg(0, x0)
        elif call_id == Syscall.MALLOC:
            size = (x0 + 15) & ~0xF
            ptr = self.heap_ptr
            self.heap_ptr += size
            if self.heap_ptr > self.sp:
                raise ExecutionError("Heap exhausted")
            self._set_reg(0, ptr)
        elif call_id == Syscall.PRINT_FLOAT:
            self._emit_text(_format_float(_bits_to_f(x0)))
        elif call_id == Syscall.ITOA:
            addr = self._sys_buffer()
            signed = x0 if x0 < (1 << 63) else x0 - (1 << 64)
            self.memory.write_string(addr, str(signed))
            self._set_reg(0, addr)
        elif call_id == Syscall.FTOA:
            addr = self._sys_buffer()
            self.memory.write_string(addr, _format_float(_bits_to_f(x0)))
            self._set_reg(0, addr)
        elif call_id == Syscall.PRINT_STR:
            self._emit_text(self.memory.read_string(x0))
        elif call_id == Syscall.STR_CONCAT:
            sa = self.memory.read_string(x0)
            sb = self.memory.read_string(x1)
            data = (sa + sb).encode('utf-8') + b'\x00'
            size = (len(data) + 15) & ~0xF
            ptr = self.heap_ptr
            self.heap_ptr += size
            if self.heap_ptr > self.sp:
                raise ExecutionError("Heap exhausted (string concat)")
            self.memory.write_block(ptr, data)
            self._set_reg(0, ptr)
        elif call_id == Syscall.BOOL_STR:
            addr = self._sys_buffer()
            self.memory.write_string(addr, "true" if x0 != 0 else "false")
            self._set_reg(0, addr)
        elif call_id == Syscall.ABORT:
            # 运行时中止 (assert / 边界检查): 消息指针位于 x0
            msg = self.memory.read_string(x0, 512)
            raise ExecutionError(f"Runtime abort: {msg}" if msg
                                 else "Runtime abort")
        elif call_id == Syscall.SUBSTR:
            # substr(s, start, len): 越界自动裁剪
            s = self.memory.read_string(x0)
            n = len(s)
            start = 0 if x1 < 0 else (n if x1 > n else x1)
            length = 0 if x2 < 0 else x2
            self._set_reg(0, self._heap_dup_string(
                s[start:start + length].encode('utf-8') + b'\x00'))
        elif call_id == Syscall.INDEXOF:
            hay = self.memory.read_string(x0)
            needle = self.memory.read_string(x1)
            idx = hay.find(needle)
            self._set_reg(0, idx)
        elif call_id == Syscall.TOUPPER:
            data = self.memory.read_string(x0)
            self._set_reg(0, self._heap_dup_string(
                data.upper().encode('utf-8') + b'\x00'))
        elif call_id == Syscall.TOLOWER:
            data = self.memory.read_string(x0)
            self._set_reg(0, self._heap_dup_string(
                data.lower().encode('utf-8') + b'\x00'))
        elif call_id == Syscall.FLOOR:
            self._set_reg(0, _f_to_bits(math.floor(_bits_to_f(x0))))
        elif call_id == Syscall.CEIL:
            self._set_reg(0, _f_to_bits(math.ceil(_bits_to_f(x0))))
        elif call_id == Syscall.ROUND:
            # 与 lib/math.cin f_round 一致: floor(x + 0.5) (半值向 +inf)
            self._set_reg(0, _f_to_bits(math.floor(_bits_to_f(x0) + 0.5)))
        elif call_id == Syscall.ATOI:
            s = self.memory.read_string(x0).strip()
            try:
                self._set_reg(0, int(s) & MASK64)
            except ValueError:
                self._set_reg(0, 0)
        elif call_id == Syscall.TRIM:
            self._set_reg(0, self._heap_dup_string(
                self.memory.read_string(x0).strip().encode('utf-8') + b'\x00'))
        elif call_id == Syscall.LTRIM:
            self._set_reg(0, self._heap_dup_string(
                self.memory.read_string(x0).lstrip().encode('utf-8') + b'\x00'))
        elif call_id == Syscall.RTRIM:
            self._set_reg(0, self._heap_dup_string(
                self.memory.read_string(x0).rstrip().encode('utf-8') + b'\x00'))
        elif Syscall.AUDIOPLAY <= call_id <= Syscall.TERMUXSMS:
            raise ExecutionError(
                "host builtins (GUI/audio/system/Termux) require the native "
                "Go runtime (run without --no-native)")
        else:
            raise ExecutionError(f"Unknown SYS call id: {call_id}")
        return True

    # ==================== 执行引擎 ====================

    def execute(self, opcode: str, args: List[Operand]) -> bool:
        if self._trace:
            ops = ' '.join(self._fmt_op_trace(a) for a in args)
            self.logger.trace(
                f"PC=0x{self.pc:04x} #{self.stats.instruction_count:08d} "
                f"{opcode} {ops}  SP=0x{self.sp:x}")
        self.pc += 1
        handler = self._dispatch.get(opcode)
        if handler is None:
            raise ExecutionError(f"Unimplemented instruction: {opcode}")
        result = handler(args)
        self.stats.record_instruction(opcode)
        if self._trace:
            self.logger.trace(
                f"  => pc=0x{self.pc:04x} {self._trace_flags()}")
        return result

    def step(self) -> bool:
        if self.pc < 0 or self.pc >= len(self.instructions):
            return False
        opcode, args = self.instructions[self.pc]
        return self.execute(opcode, args)

    def _try_native_run(self) -> Optional[bool]:
        """尝试使用 Go 原生库执行整个程序; 不可用/不支持时返回 None。"""
        if not self.config.use_native or self.config.debug_mode or self.config.step_mode:
            return None
        if self.config.enable_jit:
            return None
        try:
            from . import native
        except Exception:
            return None
        engine = native.get_engine(self.logger)
        if engine is None:
            return None

        from .native import encode_program
        all_labels = dict(self.labels)
        all_labels.update(self.data_labels)
        bytecode = encode_program(self.instructions, self.entry_pc, all_labels)
        mem_image = self.memory.get_snapshot()
        self.logger.debug(
            f"Native run: bytecode={len(bytecode)}B mem={len(mem_image)}B "
            f"entry={self.entry_pc} sp=0x{self.sp:x} "
            f"heap=0x{self.heap_ptr:x} max_steps={self.config.max_instructions}")
        import time as _time
        t0 = _time.perf_counter()
        result = engine.run(
            bytecode=bytecode,
            mem=mem_image,
            entry=self.entry_pc,
            sp=self.sp,
            heap_base=self.heap_ptr,
            input_data=self.input_buffer.encode('utf-8'),
            max_steps=self.config.max_instructions,
        )
        elapsed = _time.perf_counter() - t0
        if result is None:
            self.logger.debug("Native run failed (result None)")
            return None

        self.logger.debug(
            f"Native result: status={result.get('status')} "
            f"halted={result.get('halted')} steps={result.get('steps')} "
            f"pc={result.get('pc')} elapsed={elapsed * 1000:.2f}ms "
            f"error={result.get('error')}")

        if result.get('error') == 'unsupported':
            # 原生库遇到不支持的指令: 同步状态后回退解释执行
            self._apply_native_state(result)
            self.logger.debug(
                f"Native engine hit unsupported opcode, falling back to "
                f"interpreter at PC=0x{result.get('pc', 0):x}")
            return None

        if result.get('error'):
            # 原生运行时错误 (status=3): 同步状态并按解释器行为抛出
            self._apply_native_state(result)
            self.native_used = True
            raise ExecutionError(result.get('error') or 'native runtime error')

        # 应用原生执行结果
        self._apply_native_state(result)
        self.native_used = True
        if result.get('output'):
            self._emit_text(result['output'])
        return not result.get('halted', False) or result.get('error') is None

    def _apply_native_state(self, result: dict) -> None:
        if 'regs' in result:
            self.regs.set_all(list(result['regs'])[:32])
        if 'sp' in result:
            self.sp = result['sp'] & MASK64
        if 'pc' in result:
            self.pc = result['pc']
        if 'vec_regs' in result:
            self.vec_regs.set_all(result['vec_regs'])
        if 'mem' in result:
            mem = result['mem']
            self.memory.write_block(0, bytes(mem[:len(self.memory)]))
        if 'steps' in result:
            # 原生 VM 已整程序执行完毕: 统计改为一次性批量写入, 不再按指令数在
            # Python 里逐条调用 record_instruction (实测可让原生路径快约 7 倍,
            # 且原写法算出的 opcode 直方图随后就被 clear 掉, 纯属浪费)。
            steps = int(result.get('steps') or 0)
            counted = min(steps, self.config.max_instructions)
            self.stats.instruction_count = steps
            self.stats.opcode_count.clear()
            self.stats.hot_instructions['?'] += counted
            self.stats.inst_profiler.cycles['?'] += counted
            self.stats.performance_counters.counters['instructions'] += counted
        if 'heap_ptr' in result:
            self.heap_ptr = result['heap_ptr']

    def run(self) -> None:
        if self.config.debug_server_port is not None:
            return self._run_remote(self.config.debug_server_port)
        self.running = True
        self.stats.start()
        self.logger.info("Starting program execution")

        native_outcome = None
        if (self.config.use_native and not self.config.debug_mode
                and not self.config.step_mode
                and not self.config.bounds_check
                and not self.config.mmu):
            native_outcome = self._try_native_run()

        try:
            if native_outcome is not None:
                # 原生执行完成
                if not native_outcome:
                    self.logger.info("HALT (native)")
            elif self._can_use_fast_path():
                self._run_fast()
            else:
                self._run_interpreted()
        except KeyboardInterrupt:
            self.logger.info("User interrupt")
            self.console.print(f"\n{Colors.colorize('User interrupt', Colors.YELLOW)}")
        except Exception as e:
            self.execution_failed = True
            if self.config.debug_mode:
                # 超详细: rich 彩色完整堆栈
                self.logger.exception(f"Execution error: {e}")
            else:
                self.logger.error(f"Execution error: {e}")
                self.console.print(Panel(
                    f"{e}", title="Execution Error", border_style='red'))
        finally:
            self.running = False
            self.stats.stop()
            self.cache.flush()
            self.logger.info("Program execution finished")

            if self.config.profile or self.config.debug_mode:
                self.display_state("Execution Complete")
                cache_stats = self.cache.get_stats()
                self.stats.display_summary(self.console, cache_stats,
                                           native_used=self.native_used)

            if self.config.auto_save_crom:
                from . import crom as crom_mod
                crom_file = os.path.splitext(self.filename)[0] + '.crom' if self.filename else 'program.crom'
                crom_mod.save_crom(self.memory, crom_file,
                                   compress=self.config.compress_crom,
                                   logger=self.logger)

    def _run_remote(self, port: int) -> None:
        """A4: 远程驱动式调试 (单线程服务; 不启用原生/JIT/本地单步会话)。"""
        from .debugger import DebugServer
        self.running = False
        self.stats.start()
        self.logger.info(f"Remote debug server on port {port} "
                         f"(waiting for client)")
        srv = DebugServer(self, port)
        self.debug_server = srv
        try:
            srv.drive()
        except KeyboardInterrupt:
            self.logger.info("User interrupt")
        finally:
            self.running = False
            self.stats.stop()
            self.cache.flush()
            self.logger.info("Remote debug session ended")

    def _can_use_fast_path(self) -> bool:
        """无调试/断点/单步/JIT/追踪/节流时, 可走紧凑解释循环。"""
        return (not self.config.debug_mode
                and not self.config.step_mode
                and not self.config.interactive_mode
                and not self.breakpoints
                and self.debug_server is None
                and self.jit is None
                and not self._trace
                and self.config.execution_interval <= 0)

    def _run_fast(self) -> None:
        """紧凑解释循环 (快路径)。

        与 _run_interpreted 的语义差异仅在"没有"哪些检查: 断点命中、远程
        调试轮询、单步会话、JIT 尝试、逐指令追踪与执行节流。这些在快路径的
        启用条件里都已确认不生效, 因此逐指令语义 (pc 先自增、统计记账、HALT
        返回 False) 完全一致。
        """
        instructions = self.instructions
        execute = self.execute
        logger = self.logger
        limit = self.config.max_instructions
        n = len(instructions)
        while True:
            pc = self.pc
            if pc < 0 or pc >= n:
                logger.info("Program ended normally")
                return
            if self.stats.instruction_count >= limit:
                raise ExecutionError(
                    f"instruction limit reached ({limit} steps)")
            opcode, args = instructions[pc]
            if not execute(opcode, args):
                logger.info("HALT instruction executed")
                return

    def _run_interpreted(self) -> None:
        while self.running:
            if self.pc < 0 or self.pc >= len(self.instructions):
                self.logger.info("Program ended normally")
                break

            if self.stats.instruction_count >= self.config.max_instructions:
                # 与 Go 原生 VM 保持一致的语义: 步数用尽不是"正常结束",
                # 而是失败 (旧行为只是 warning + break, 会让被截断的程序
                # 以退出码 0 报告成功)。
                raise ExecutionError(
                    f"instruction limit reached "
                    f"({self.config.max_instructions} steps)")

            # 建议 5: 'continue' 后豁免一次当前 PC 的断点命中,
            # 确保至少执行一条指令 (与 GDB 语义一致, 避免死循环重入)
            resume_bp = self._resume_bp_pc is not None
            if resume_bp:
                self._resume_bp_pc = None

            if not resume_bp:
                if self.debug_server and self.debug_server.check_conditional_breakpoints():
                    self.is_debugging = True
                    self.debug_command_loop()
                    continue

                if self.pc in self.breakpoints:
                    self.console.print(
                        f"{Colors.colorize(f'Breakpoint hit at PC={self.pc:#x}', Colors.YELLOW)}")
                    self.is_debugging = True
                    self.debug_command_loop()
                    continue

            # 建议 6: --step 与断点命中共用 DebugSession 命令集
            if self.config.step_mode and self.config.interactive_mode:
                from .debugger import DebugSession
                action = DebugSession(self).run_step_mode()
                if action in ('quit', 'halted'):
                    if action == 'quit':
                        self.running = False
                    break
                if action == 'run':
                    self.config.step_mode = False
                continue

            if self.jit is not None:
                jit_result = self.jit.try_step(self)
                if jit_result is True:
                    continue
                if jit_result is False:
                    self.logger.info("Program ended (JIT)")
                    break
                # None: 该指令不适合 JIT, 回退解释执行

            opcode, args = self.instructions[self.pc]
            continue_exec = self.execute(opcode, args)

            if not continue_exec:
                self.logger.info("HALT instruction executed")
                break

            if self.config.execution_interval > 0:
                time.sleep(self.config.execution_interval)

    # ==================== 状态显示 / 调试 ====================

    def display_state(self, title: str = "CPU State", opcode: Optional[str] = None,
                      args: Optional[List[Operand]] = None) -> None:
        # 建议 6: 渲染逻辑迁移至 codecin/debugger.py (状态显示与执行解耦)
        from .debugger import display_state as _render_state
        _render_state(self, title, opcode, args)

    @staticmethod
    def _fmt_operand(op: Operand) -> str:
        # 建议 6: 操作数格式化迁移至 codecin/debugger.py
        from .debugger import fmt_operand
        return fmt_operand(op)

    def add_breakpoint(self, addr: int) -> None:
        self.breakpoints.add(addr)
        self.logger.info(f"Breakpoint set at PC={addr:#x}")

    def remove_breakpoint(self, addr: int) -> None:
        self.breakpoints.discard(addr)
        self.logger.info(f"Breakpoint removed at PC={addr:#x}")

    def debug_command_loop(self) -> None:
        """断点命中后的交互调试入口 (建议 6: 逻辑已迁移至 codecin/debugger.DebugSession)。"""
        from .debugger import DebugSession
        action = DebugSession(self).run()
        if action == 'halted':
            self.running = False

