"""Python 级 JIT: 将无分支基本块动态编译为 Python 函数。

原生 Go 库不可用且显式 --jit 时使用。编译块仅包含直线指令
(数据传输/算术/逻辑/加载存储/栈), 遇到控制流指令即结束块。
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from .isa import Constants

MASK64 = 0xFFFFFFFFFFFFFFFF

# 可安全编译进直线块的指令
_JIT_OPS = {
    'MOV', 'ADD', 'SUB', 'MUL', 'DIV', 'AND', 'OR', 'XOR', 'SHL', 'SHR',
    'INC', 'DEC', 'ADDI', 'XORI', 'ORI', 'ANDI', 'LSL', 'LSR',
    'LDR', 'STR', 'LOAD', 'STORE', 'LB', 'LH', 'LW', 'LD', 'SB', 'SH', 'SW', 'SD',
    'PUSH', 'POP', 'NOP', 'MVN',
}

_BRANCH_OPS = Constants.BRANCH_OPS | {'HALT', 'SYS', 'IN', 'OUT'}


class JITCompiler:
    def __init__(self, logger=None):
        self.logger = logger
        self.blocks: Dict[int, Callable] = {}
        self.block_ranges: Dict[int, Tuple[int, int]] = {}
        self.compilation_count = 0
        self.total_calls = 0
        self.cache_hits = 0

    def _dbg(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.debug(msg)

    # ---------------- 代码生成 ----------------

    def _reg_read(self, n: int) -> str:
        if n == 31:
            return '0'
        if n == Constants.SP_REG:
            return 'cpu.sp'
        return f'R[{n}]'

    def _reg_write(self, n: int, expr: str) -> str:
        if n == 31:
            return ''
        if n == Constants.SP_REG:
            return f'cpu.sp = ({expr}) & MASK'
        return f'R[{n}] = ({expr}) & MASK'

    def _val(self, op: Tuple) -> Optional[str]:
        kind = op[0]
        if kind == 'reg':
            return self._reg_read(op[1])
        if kind == 'imm':
            return str(op[1] & MASK64)
        if kind == 'mem':
            return None  # 内存作为值操作不编译
        return None

    def _mem_addr(self, op: Tuple) -> Optional[str]:
        if op[0] != 'mem':
            return None
        base, off = op[1], op[2]
        if base >= 0:
            return f'({self._reg_read(base)} + {off})'
        return str(off)

    def _gen(self, opcode: str, args: List[Tuple]) -> Optional[List[str]]:
        try:
            if opcode == 'MOV':
                val = self._val(args[1])
                return [self._reg_write(args[0][1], val)] if val is not None else None
            if opcode in ('ADD', 'SUB', 'MUL', 'AND', 'OR', 'XOR'):
                sym = {'ADD': '+', 'SUB': '-', 'MUL': '*', 'AND': '&',
                       'OR': '|', 'XOR': '^'}[opcode]
                val = self._val(args[1])
                rd = args[0][1]
                return [self._reg_write(rd, f'{self._reg_read(rd)} {sym} {val}')] if val else None
            if opcode in ('SHL', 'SHR'):
                sym = '<<' if opcode == 'SHL' else '>>'
                val = self._val(args[1])
                rd = args[0][1]
                if val is None:
                    return None
                if opcode == 'SHR':
                    return [f'_v = ({self._reg_read(rd)} & MASK) >> (({val}) & 63)',
                            self._reg_write(rd, '_v')]
                return [self._reg_write(rd, f'{self._reg_read(rd)} {sym} (({val}) & 63)')]
            if opcode == 'DIV':
                rd = args[0][1]
                val = self._val(args[1])
                if val is None:
                    return None
                # 有符号除法, 向零截断 (与解释器一致)
                return [f'_d = {val}',
                        'if _d == 0: raise ExecutionError("Division by zero")',
                        '_n = ' + self._reg_read(rd),
                        '_sn = _n if _n < (1 << 63) else _n - (1 << 64)',
                        '_sd = _d if _d < (1 << 63) else _d - (1 << 64)',
                        '_q = abs(_sn) // abs(_sd)',
                        '_q = _q if (_sn < 0) == (_sd < 0) else -_q',
                        self._reg_write(rd, '_q')]
            if opcode == 'INC':
                rd = args[0][1]
                return [self._reg_write(rd, f'{self._reg_read(rd)} + 1')]
            if opcode == 'DEC':
                rd = args[0][1]
                return [self._reg_write(rd, f'{self._reg_read(rd)} - 1')]
            if opcode == 'MVN':
                val = self._val(args[1])
                return [self._reg_write(args[0][1], f'~({val})')] if val else None
            if opcode in ('ADDI',):
                rd, rs = args[0][1], args[1][1]
                val = self._val(args[2])
                return [self._reg_write(rd, f'{self._reg_read(rs)} + {val}')] if val else None
            if opcode in ('XORI', 'ORI', 'ANDI'):
                sym = {'XORI': '^', 'ORI': '|', 'ANDI': '&'}[opcode]
                rd, rs = args[0][1], args[1][1]
                val = self._val(args[2])
                return [self._reg_write(rd, f'{self._reg_read(rs)} {sym} {val}')] if val else None
            if opcode in ('LSL', 'LSR'):
                rd, rn = args[0][1], args[1][1]
                val = self._val(args[2])
                if val is None:
                    return None
                if opcode == 'LSL':
                    return [self._reg_write(rd, f'{self._reg_read(rn)} << (({val}) & 63)')]
                return [f'_v = ({self._reg_read(rn)} & MASK) >> (({val}) & 63)',
                        self._reg_write(rd, '_v')]
            if opcode in ('LDR', 'LOAD', 'LW'):
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                rd = args[0][1]
                if addr is None:
                    return None
                return [self._reg_write(rd, f'mem.read_dword({addr})')]
            if opcode in ('STR', 'STORE', 'SW'):
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                rs = args[0][1]
                if addr is None:
                    return None
                return [f'mem.write_dword({addr}, {self._reg_read(rs)} & 0xFFFFFFFF)']
            if opcode == 'LD':
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                return [self._reg_write(args[0][1], f'mem.read_qword({addr})')] if addr else None
            if opcode == 'SD':
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                return [f'mem.write_qword({addr}, {self._reg_read(args[0][1])})'] if addr else None
            if opcode in ('LB', 'LH'):
                width = 1 if opcode == 'LB' else 2
                fn = 'read_byte' if width == 1 else 'read_word'
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                if addr is None:
                    return None
                bits = width * 8
                return [f'_v = mem.{fn}({addr})',
                        f'if _v & {1 << (bits - 1)}: _v -= {1 << bits}',
                        self._reg_write(args[0][1], '_v')]
            if opcode in ('SB', 'SH'):
                mask = '0xFF' if opcode == 'SB' else '0xFFFF'
                fn = 'write_byte' if opcode == 'SB' else 'write_word'
                addr = self._mem_addr(args[1]) if args[1][0] == 'mem' else self._val(args[1])
                if addr is None:
                    return None
                return [f'mem.{fn}({addr}, {self._reg_read(args[0][1])} & {mask})']
            if opcode == 'PUSH':
                val = self._val(args[0])
                if val is None:
                    return None
                return ['cpu.sp = (cpu.sp - 8) & MASK',
                        f'mem.write_qword(cpu.sp, ({val}) & MASK)']
            if opcode == 'POP':
                rd = args[0][1]
                return [f'_v = mem.read_qword(cpu.sp)',
                        'cpu.sp = (cpu.sp + 8) & MASK',
                        self._reg_write(rd, '_v')]
            if opcode == 'NOP':
                return ['pass']
        except Exception:
            return None
        return None

    def compile_block(self, cpu) -> Optional[int]:
        """编译 cpu.pc 处开始的基本块, 返回块结束 PC。"""
        start = cpu.pc
        if start in self.blocks:
            self.cache_hits += 1
            if self.logger is not None and self.logger.is_debug:
                self._dbg(f"JIT cache hit: block @0x{start:x} "
                          f"(hits={self.cache_hits})")
            return self.block_ranges[start][1]

        end = start
        body: List[str] = []
        for i in range(start, min(start + 32, len(cpu.instructions))):
            opcode, args = cpu.instructions[i]
            if opcode in _BRANCH_OPS or opcode not in _JIT_OPS:
                break
            lines = self._gen(opcode, args)
            if lines is None:
                break
            body.extend(f'    {ln}' for ln in lines if ln)
            end = i + 1

        if end <= start:
            return None

        source = (
            "def block(cpu, mem, R):\n"
            "    MASK = 0xFFFFFFFFFFFFFFFF\n"
            + ("\n".join(body) if body else "    pass") + "\n"
            "    return None\n"
        )
        try:
            from .errors import ExecutionError
            namespace: Dict[str, Any] = {'ExecutionError': ExecutionError}
            exec(compile(source, '<jit-block>', 'exec'), namespace)
            func = namespace['block']
        except Exception as e:
            cpu.logger.debug(f"JIT block compile failed at {start}: {e}")
            return None

        self.blocks[start] = func
        self.block_ranges[start] = (start, end)
        self.compilation_count += 1
        self._dbg(f"JIT compiled block 0x{start:x}-0x{end:x} "
                  f"({end - start} instrs, total {self.compilation_count})")
        if self.logger is not None and self.logger.is_debug:
            self._dbg('JIT block source:\n' + source.rstrip())
        return end

    def try_step(self, cpu) -> Optional[bool]:
        """尝试以 JIT 块执行; 不适合 JIT 时返回 None。"""
        self.total_calls += 1
        start = cpu.pc
        if start < 0 or start >= len(cpu.instructions):
            return False
        opcode, _ = cpu.instructions[start]
        if opcode in _BRANCH_OPS or opcode not in _JIT_OPS:
            return None

        end = self.compile_block(cpu)
        if end is None:
            return None

        func = self.blocks.get(start)
        if func is None:
            return None
        try:
            func(cpu, cpu.memory, cpu.regs._regs)
        except Exception as e:
            cpu.logger.debug(f"JIT execution failed: {e}")
            self.blocks.pop(start, None)
            return None

        for i in range(start, end):
            cpu.stats.record_instruction(cpu.instructions[i][0])
        cpu.pc = end
        return True

    def get_stats(self) -> Dict[str, Any]:
        return {
            'blocks_compiled': self.compilation_count,
            'cached_blocks': len(self.blocks),
            'total_calls': self.total_calls,
            'cache_hits': self.cache_hits,
            'hit_rate': self.cache_hits / self.total_calls if self.total_calls else 0.0,
        }
