"""native AOT 后端: 把 CIN/ASM 编译后的指令流生成独立 Go 程序。

与旧的 AOT 路径 (嵌入 UCBC 字节码 + Go VM 解释执行) 不同, native 后端把
每条指令直接展开成 Go 语句:

  * 整数/控制流/内存/SYS/ARM64/RISC-V 扩展/FP/Vector 指令直接生成
    :class:`engine.NativeRuntime` 原语调用, 由 Go 编译器生成 x86-64/ARM64
    机器码;
  * 少数动态控制流/无法静态求值的内存操作数通过 ``rt.Exec`` 复用现有 VM
    单指令语义, 作为受控回退;
  * 生成代码不包含 UCBC 解码循环, 因此没有逐指令 opcode dispatch。

生成源码由 ``codecin.aot`` 写入临时模块并调用 ``go build`` (交叉编译由
GOOS/GOARCH 控制)。本模块只负责源码生成, 不执行构建。
"""

import struct
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .isa import Cond

Instruction = Tuple[str, List[Tuple[Any, ...]]]
Segments = Sequence[Tuple[int, bytes]]

MASK64 = 0xFFFFFFFFFFFFFFFF
STACK_SLOT = 8


class NativeCodegenError(Exception):
    """native 代码生成失败 (通常是无法解析的标签/操作数)。"""


def _i64(v: int) -> int:
    """把无符号 64 位值转成 Go int64 字面量。"""
    v &= MASK64
    return v - (1 << 64) if v >= (1 << 63) else v


def _u64(v: int) -> int:
    return int(v) & MASK64


def _float_bits(f: float) -> int:
    return struct.unpack('<Q', struct.pack('<d', float(f)))[0]


def _go_bytes(data: bytes, indent: str = '\t') -> str:
    """生成 Go []byte 字面量 (每行 16 字节)。"""
    if not data:
        return 'nil'
    lines = ['[]byte{']
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        lines.append(indent + '\t' + ', '.join(f'0x{b:02x}' for b in chunk) + ',')
    lines.append(indent + '}')
    return '\n'.join(lines)


class _Generator:
    def __init__(self, instructions: Sequence[Instruction],
                 labels: Dict[str, int],
                 segments: Segments,
                 entry_pc: int,
                 mem_size: int,
                 sp: int,
                 heap_base: int,
                 max_steps: int,
                 program_name: str = 'program',
                 block_mode: bool = False):
        self.instructions = list(instructions)
        self.labels = dict(labels)
        self.segments = list(segments)
        self.entry_pc = int(entry_pc)
        self.mem_size = int(mem_size)
        self.sp = int(sp)
        self.heap_base = int(heap_base)
        self.max_steps = int(max_steps)
        self.program_name = program_name
        self.block_mode = block_mode
        self.lines: List[str] = []

    # ---------------- 基础设施 ----------------

    def _emit(self, text: str = '') -> None:
        self.lines.append(text)

    def _code(self, text: str) -> None:
        self.lines.append('\t' + text)

    def _fail(self, call: str) -> None:
        self._code(f'if e := {call}; e != "" {{')
        if self.block_mode:
            self._code('\trt.Abort(e)')
            self._code('\treturn -2')
        else:
            self._code('\treturn rt.Abort(e)')
        self._code('}')

    def _target(self, arg: Tuple[Any, ...]) -> Optional[int]:
        kind = arg[0]
        if kind == 'label':
            target = self.labels.get(arg[1])
        elif kind == 'imm':
            target = int(arg[1])
        else:
            return None
        if target is None or target < 0 or target >= len(self.instructions):
            return None
        return int(target)

    def _val(self, arg: Tuple[Any, ...]) -> Optional[str]:
        """把可静态求值的操作数转成 Go uint64 表达式。"""
        kind = arg[0]
        if kind == 'reg':
            return f'rt.Reg({int(arg[1])})'
        if kind == 'imm':
            return f'uint64(0x{_u64(arg[1]):x})'
        if kind == 'str':
            return f'uint64(0x{_u64(arg[1]):x})'
        if kind == 'float':
            return f'uint64(0x{_float_bits(arg[1]):x})'
        return None

    def _addr(self, arg: Tuple[Any, ...]) -> Optional[str]:
        kind = arg[0]
        if kind == 'reg':
            return f'rt.Reg({int(arg[1])})'
        if kind == 'imm':
            return f'uint64(0x{_u64(arg[1]):x})'
        if kind == 'mem':
            base = _i64(int(arg[1]))
            off = _i64(int(arg[2]))
            return f'rt.Addr({base}, {off})'
        return None

    def _arg_lit(self, arg: Tuple[Any, ...]) -> str:
        kind = arg[0]
        if kind == 'label':
            target = self.labels.get(arg[1])
            if target is None:
                raise NativeCodegenError(f'undefined label: {arg[1]}')
            return f'{{Kind: engine.KindImm, Value: {target}}}'
        if kind == 'reg':
            return f'{{Kind: engine.KindReg, Value: {int(arg[1])}}}'
        if kind == 'imm':
            return f'{{Kind: engine.KindImm, Value: {_i64(int(arg[1]))}}}'
        if kind == 'mem':
            return (f'{{Kind: engine.KindMem, Value: {_i64(int(arg[1]))}, '
                    f'Extra: {_i64(int(arg[2]))}}}')
        if kind == 'cond':
            return f'{{Kind: engine.KindCond, Value: {self._cond_code(arg[1])}}}'
        if kind == 'float':
            return (f'{{Kind: engine.KindFloat, '
                    f'Value: {_i64(_float_bits(arg[1]))}}}')
        if kind == 'str':
            return f'{{Kind: engine.KindStr, Value: {_i64(int(arg[1]))}}}'
        if kind == 'vec':
            return f'{{Kind: engine.KindVec, Value: {int(arg[1])}}}'
        if kind == 'veclane':
            return (f'{{Kind: engine.KindVecLane, Value: {int(arg[1])}, '
                    f'Extra: {int(arg[2])}}}')
        raise NativeCodegenError(f'unknown operand kind: {kind}')

    def _out_lit(self, arg: Tuple[Any, ...]) -> str:
        """OUT 操作数 -> (kind, value, extra) 三个 Go 表达式。"""
        kind = arg[0]
        if kind == 'reg':
            return 'engine.KindReg', str(int(arg[1])), '0'
        if kind == 'imm':
            return 'engine.KindImm', str(_i64(int(arg[1]))), '0'
        if kind == 'str':
            return 'engine.KindStr', str(_i64(int(arg[1]))), '0'
        if kind == 'float':
            return 'engine.KindFloat', str(_i64(_float_bits(arg[1]))), '0'
        if kind == 'mem':
            return ('engine.KindMem', str(_i64(int(arg[1]))),
                    str(_i64(int(arg[2]))))
        if kind == 'vec':
            return 'engine.KindVec', str(int(arg[1])), '0'
        if kind == 'veclane':
            return ('engine.KindVecLane', str(int(arg[1])),
                    str(int(arg[2])))
        if kind == 'cond':
            return 'engine.KindCond', str(self._cond_code(arg[1])), '0'
        raise NativeCodegenError(f'unsupported OUT operand: {kind}')

    @staticmethod
    def _cond_code(name: str) -> int:
        try:
            return Cond.NAMES.index(str(name).upper())
        except ValueError as e:
            raise NativeCodegenError(f'unknown condition: {name}') from e

    def _exec(self, op: str, args: Sequence[Tuple[Any, ...]], pc: int) -> None:
        lits = ', '.join('engine.Arg' + self._arg_lit(a) for a in args)
        self._fail(f'rt.Exec("{op}", []engine.Arg{{{lits}}}, {pc})')

    # ---------------- 单条指令 ----------------

    def _emit_ins(self, op: str, args: List[Tuple[Any, ...]],
                  idx: int) -> bool:
        """生成一条指令；返回 True 表示已经生成终止控制流。"""

        # ---- 基本移动/算术 ----
        if op == 'MOV':
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Mov({int(args[0][1])}, {v})')
            return False
        if op in ('ADD', 'SUB', 'MUL', 'DIV', 'AND', 'OR', 'XOR',
                  'SHL', 'SHR'):
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
                return False
            method = {'ADD': 'Add', 'SUB': 'Sub', 'MUL': 'Mul', 'DIV': 'Div',
                      'AND': 'And', 'OR': 'Or', 'XOR': 'Xor',
                      'SHL': 'Shl', 'SHR': 'Shr'}[op]
            if op == 'DIV':
                self._fail(f'rt.Div({rd}, {v})')
            else:
                self._code(f'rt.{method}({rd}, {v})')
            return False
        if op == 'ASR':
            # 两操作数形式是 Base ASR rd, rs; 三操作数形式是
            # ARM64/RISC-V 扩展 ASR rd, rn, shift。
            if len(args) >= 3:
                rd, rs = int(args[0][1]), int(args[1][1])
                v = self._val(args[2])
                if v is None:
                    self._exec(op, args, idx)
                else:
                    self._code(f'rt.AsrFrom({rd}, {rs}, {v})')
                return False
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Asr({rd}, {v})')
            return False
        if op in ('ADDI', 'LSL', 'LSR'):
            rd, rs = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
                return False
            if op == 'ADDI':
                self._code(f'rt.AddTo({rd}, {rs}, {v})')
            else:
                method = {'LSL': 'LslFrom', 'LSR': 'LsrFrom'}[op]
                self._code(f'rt.{method}({rd}, {rs}, {v})')
            return False
        if op in ('XORI', 'ORI', 'ANDI'):
            rd, rs = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
                return False
            go_op = {'XORI': '^', 'ORI': '|', 'ANDI': '&'}[op]
            self._code(f'rt.SetReg({rd}, rt.Reg({rs}) {go_op} {v})')
            return False
        if op in ('INC', 'DEC'):
            rd = int(args[0][1])
            self._code(f'rt.{"Inc" if op == "INC" else "Dec"}({rd})')
            return False
        if op == 'MVN':
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Mvn({rd}, {v})')
            return False
        if op == 'CMP':
            a = self._val(args[0])
            b = self._val(args[1])
            if a is None or b is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Cmp({a}, {b})')
            return False

        # ---- 栈 ----
        if op == 'PUSH':
            v = self._val(args[0])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._fail(f'rt.Push({v})')
            return False
        if op == 'POP':
            self._code('{')
            self._code('\tv, e := rt.Pop()')
            if self.block_mode:
                self._code('\tif e != "" { rt.Abort(e); return -2 }')
            else:
                self._code('\tif e != "" { return rt.Abort(e) }')
            self._code(f'\trt.SetReg({int(args[0][1])}, v)')
            self._code('}')
            return False

        # ---- I/O / 系统调用 ----
        if op == 'IN':
            self._code(f'rt.In({int(args[0][1])})')
            return False
        if op == 'OUT':
            kind, value, extra = self._out_lit(args[0])
            self._fail(f'rt.Out({kind}, {value}, {extra})')
            return False
        if op == 'SYS':
            if args[0][0] != 'imm':
                self._exec(op, args, idx)
            else:
                self._fail(f'rt.Sys(uint64({_u64(args[0][1])}))')
            return False
        if op == 'HALT':
            self._code('return -1' if self.block_mode else 'return true')
            return True

        # ---- 内存 ----
        load_width = {'LOAD': (4, False), 'LW': (4, True), 'LD': (8, True),
                      'LB': (1, True), 'LH': (2, True), 'LDR': (4, False)}
        if op in load_width:
            width, signed = load_width[op]
            addr = self._addr(args[1])
            if addr is None:
                self._exec(op, args, idx)
            else:
                self._fail(f'rt.Load({int(args[0][1])}, {addr}, {width}, '
                           f'{"true" if signed else "false"})')
            return False
        store_width = {'STORE': 4, 'SW': 4, 'STR': 4, 'SD': 8,
                       'SB': 1, 'SH': 2}
        if op in store_width:
            addr = self._addr(args[1])
            if addr is None:
                self._exec(op, args, idx)
            else:
                self._fail(f'rt.Store({int(args[0][1])}, {addr}, '
                           f'{store_width[op]})')
            return False

        # ---- 无条件跳转 ----
        if op == 'JMP':
            target = self._target(args[0])
            if target is not None:
                self._code(f'return {target}' if self.block_mode
                           else f'goto L{target}')
                return True
            v = self._val(args[0])
            if v is not None:
                if self.block_mode:
                    self._code(f'return int({v})')
                else:
                    self._code(f'target = int({v})')
                    self._code('goto Ldispatch')
                return True
            self._exec(op, args, idx)
            if self.block_mode:
                self._code('return rt.PC()')
            else:
                self._code('target = rt.PC()')
                self._code('goto Ldispatch')
            return True

        # ---- 条件跳转 (标志位) ----
        if op in ('JZ', 'JE', 'JNZ', 'JL', 'JG'):
            target = self._target(args[0])
            if target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            cond = {'JZ': 'rt.Z()', 'JE': 'rt.Z()', 'JNZ': '!rt.Z()',
                    'JL': 'rt.N() != rt.V()',
                    'JG': '!rt.Z() && rt.N() == rt.V()'}[op]
            if self.block_mode:
                self._code(f'if {cond} {{ return {target} }}')
            else:
                self._code(f'if {cond} {{ goto L{target} }}')
            return False

        # ---- ARM64 条件分支 ----
        if op == 'B':
            cond_arg = next((a for a in args if a[0] == 'cond'), None)
            target_arg = next((a for a in args if a[0] != 'cond'), None)
            target = self._target(target_arg) if target_arg else None
            if cond_arg is None or target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            code = self._cond_code(cond_arg[1])
            if self.block_mode:
                self._code(f'if rt.Cond({code}) {{ return {target} }}')
            else:
                self._code(f'if rt.Cond({code}) {{ goto L{target} }}')
            return False
        if op in ('CBZ', 'CBNZ'):
            target = self._target(args[1])
            if target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            cmp = '==' if op == 'CBZ' else '!='
            if self.block_mode:
                self._code(f'if rt.Reg({int(args[0][1])}) {cmp} 0 '
                           f'{{ return {target} }}')
            else:
                self._code(f'if rt.Reg({int(args[0][1])}) {cmp} 0 '
                           f'{{ goto L{target} }}')
            return False
        if op in ('TBZ', 'TBNZ'):
            target = self._target(args[2])
            bit = self._val(args[1])
            if target is None or bit is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            cmp = '==' if op == 'TBZ' else '!='
            if self.block_mode:
                self._code(
                    f'if rt.Reg({int(args[0][1])}) & (1 << ({bit} & 63)) '
                    f'{cmp} 0 {{ return {target} }}')
            else:
                self._code(
                    f'if rt.Reg({int(args[0][1])}) & (1 << ({bit} & 63)) '
                    f'{cmp} 0 {{ goto L{target} }}')
            return False

        # ---- RISC-V 条件分支 ----
        if op in ('BEQ', 'BNE', 'BLT', 'BGE', 'BLTU', 'BGEU'):
            target = self._target(args[2])
            if target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            rs1, rs2 = int(args[0][1]), int(args[1][1])
            if op == 'BEQ':
                cond = f'rt.Reg({rs1}) == rt.Reg({rs2})'
            elif op == 'BNE':
                cond = f'rt.Reg({rs1}) != rt.Reg({rs2})'
            elif op == 'BLT':
                cond = f'int64(rt.Reg({rs1})) < int64(rt.Reg({rs2}))'
            elif op == 'BGE':
                cond = f'int64(rt.Reg({rs1})) >= int64(rt.Reg({rs2}))'
            elif op == 'BLTU':
                cond = f'rt.Reg({rs1}) < rt.Reg({rs2})'
            else:
                cond = f'rt.Reg({rs1}) >= rt.Reg({rs2})'
            if self.block_mode:
                self._code(f'if {cond} {{ return {target} }}')
            else:
                self._code(f'if {cond} {{ goto L{target} }}')
            return False

        # ---- 调用/返回 ----
        if op in ('CALL', 'BL'):
            target = self._target(args[0])
            if target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            self._fail(f'rt.Push(uint64({idx + 1}))')
            self._code(f'return {target}' if self.block_mode
                       else f'goto L{target}')
            return True
        if op == 'RET':
            self._code('{')
            self._code('\tv, e := rt.PopRet()')
            if self.block_mode:
                self._code('\tif e != "" { rt.Abort(e); return -2 }')
                self._code('\treturn v')
            else:
                self._code('\tif e != "" { return rt.Abort(e) }')
                self._code('\ttarget = v')
            self._code('}')
            if not self.block_mode:
                self._code('goto Ldispatch')
            return True
        if op == 'JAL':
            if args[0][0] == 'reg':
                rd = int(args[0][1])
                target_arg = args[1]
            else:
                rd = 31
                target_arg = args[0]
            target = self._target(target_arg)
            if target is None:
                self._exec(op, args, idx)
                if self.block_mode:
                    self._code('return rt.PC()')
                else:
                    self._code('target = rt.PC()')
                    self._code('goto Ldispatch')
                return True
            if rd != 31:
                self._code(f'rt.SetReg({rd}, uint64({idx + 1}))')
            self._code(f'return {target}' if self.block_mode
                       else f'goto L{target}')
            return True
        if op == 'JALR':
            self._exec(op, args, idx)
            if self.block_mode:
                self._code('return rt.PC()')
            else:
                self._code('target = rt.PC()')
                self._code('goto Ldispatch')
            return True
        if op == 'BR':
            if self.block_mode:
                self._code(f'return int(rt.Reg({int(args[0][1])}))')
            else:
                self._code(f'target = int(rt.Reg({int(args[0][1])}))')
                self._code('goto Ldispatch')
            return True

        # ---- ARM64/RISC-V 扩展整数 ----
        if op in ('ADDS', 'SUBS', 'ADDC', 'SUBC'):
            rd, rn = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
            else:
                method = {'ADDS': 'AddS', 'SUBS': 'SubS',
                          'ADDC': 'AddC', 'SUBC': 'SubC'}[op]
                self._code(f'rt.{method}({rd}, {rn}, {v})')
            return False
        if op in ('ROR', 'EOR', 'BIC', 'ORN'):
            rd, rn = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
            else:
                method = {'ROR': 'Ror', 'EOR': 'Eor',
                          'BIC': 'Bic', 'ORN': 'Orn'}[op]
                self._code(f'rt.{method}({rd}, {rn}, {v})')
            return False
        if op in ('LDP', 'STP'):
            addr = self._addr(args[2])
            if addr is None:
                self._exec(op, args, idx)
            else:
                if op == 'LDP':
                    self._fail(f'rt.Ldp({int(args[0][1])}, '
                               f'{int(args[1][1])}, {addr})')
                else:
                    self._fail(f'rt.Stp({int(args[0][1])}, '
                               f'{int(args[1][1])}, {addr})')
            return False
        if op in ('CSEL', 'CSINC', 'CSINV', 'CSNEG'):
            rd, rn = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            cond_arg = args[3] if len(args) > 3 else None
            if v is None or cond_arg is None or cond_arg[0] != 'cond':
                self._exec(op, args, idx)
            else:
                cond = self._cond_code(cond_arg[1])
                self._code(f'rt.CondSelect("{op}", {rd}, {rn}, {v}, {cond})')
            return False
        if op in ('SXTB', 'SXTH', 'SXTW', 'UXTB', 'UXTH'):
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                width = {'SXTB': 8, 'SXTH': 16, 'SXTW': 32,
                         'UXTB': 8, 'UXTH': 16}[op]
                method = 'SignExtend' if op.startswith('SX') else 'ZeroExtend'
                self._code(f'rt.{method}({rd}, {v}, {width})')
            return False
        if op in ('CLZ', 'CLS', 'RBIT', 'REV'):
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                method = {'CLZ': 'Clz', 'CLS': 'Cls',
                          'RBIT': 'Rbit', 'REV': 'Rev'}[op]
                self._code(f'rt.{method}({rd}, {v})')
            return False
        if op in ('SLTI', 'SLTIU'):
            rd, rs = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.SetLessImm({rd}, {rs}, {v}, '
                           f'{"true" if op == "SLTI" else "false"})')
            return False
        if op in ('SLLI', 'SRLI', 'SRAI'):
            rd, rs = int(args[0][1]), int(args[1][1])
            v = self._val(args[2])
            if v is None:
                self._exec(op, args, idx)
            else:
                method = {'SLLI': 'LslFrom', 'SRLI': 'LsrFrom',
                          'SRAI': 'AsrFrom'}[op]
                self._code(f'rt.{method}({rd}, {rs}, {v})')
            return False
        if op == 'LUI':
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Lui({rd}, {v})')
            return False
        if op == 'AUIPC':
            rd = int(args[0][1])
            v = self._val(args[1])
            if v is None:
                self._exec(op, args, idx)
            else:
                self._code(f'rt.Auipc({rd}, {v}, {idx})')
            return False

        # ---- 浮点/向量 ----
        if op in ('FADD', 'FSUB', 'FMUL', 'FDIV'):
            rd, rn, rm = int(args[0][1]), int(args[1][1]), int(args[2][1])
            method = {'FADD': 'FAdd', 'FSUB': 'FSub',
                      'FMUL': 'FMul', 'FDIV': 'FDiv'}[op]
            if op == 'FDIV':
                self._fail(f'rt.FDiv({rd}, {rn}, {rm})')
            else:
                self._code(f'rt.{method}({rd}, {rn}, {rm})')
            return False
        if op == 'FCMP':
            self._code(f'rt.FCmp({int(args[0][1])}, {int(args[1][1])})')
            return False
        if op == 'FCVT':
            rd, rs = int(args[0][1]), int(args[1][1])
            if args[1][0] in ('vec', 'veclane'):
                lane = int(args[1][2]) if args[1][0] == 'veclane' else 0
                self._code(f'rt.FCvtToInt({rd}, {rs}, {lane})')
            elif args[1][0] == 'reg':
                self._code(f'rt.FCvtToFloat({rd}, {rs})')
            else:
                self._exec(op, args, idx)
            return False
        if op in ('FABS', 'FNEG'):
            rd, rs = int(args[0][1]), int(args[1][1])
            method = 'FAbs' if op == 'FABS' else 'FNeg'
            self._code(f'rt.{method}({rd}, {rs})')
            return False
        if op in ('LDRS', 'STRS'):
            addr = self._addr(args[1])
            if addr is None:
                self._exec(op, args, idx)
            elif op == 'LDRS':
                self._fail(f'rt.LoadF32({int(args[0][1])}, {addr})')
            else:
                self._fail(f'rt.StoreF32({int(args[0][1])}, {addr})')
            return False
        if op in ('VADD', 'VSUB', 'VMUL', 'VDIV'):
            self._fail(f'rt.VOp("{op}", {int(args[0][1])}, '
                       f'{int(args[1][1])}, {int(args[2][1])})')
            return False
        if op in ('VLD1', 'VST1'):
            addr = self._addr(args[1])
            if addr is None:
                self._exec(op, args, idx)
            elif op == 'VLD1':
                self._fail(f'rt.VLoad1({int(args[0][1])}, {addr})')
            else:
                self._fail(f'rt.VStore1({int(args[0][1])}, {addr})')
            return False

        # ---- 无操作 ----
        if op in ('NOP', 'WFE', 'WFI', 'SEV'):
            # 每条指令前面的 Tick() 已经是一条语句; 无需额外 goto。
            return False

        # ---- 其余: 受控回退到 VM 单指令语义 ----
        self._exec(op, args, idx)
        return False

    # ---------------- 控制流目标分析 ----------------

    def _analyze_targets(self) -> Tuple[Set[int], bool]:
        """返回 (需要生成标签的下标集合, 是否需要 Ldispatch)。

        只为真正的跳转目标/返回地址生成标签, 顺序执行不再逐条 ``goto``。
        这样大程序 (例如 basic.cin 的数万条指令) 不会生成数万个标签和
        goto, Go 编译时间和源码体积都会显著下降。
        """
        n = len(self.instructions)
        needed: Set[int] = set()
        dynamic = False
        has_ret = any(op == 'RET' for op, _ in self.instructions)

        def add(target: Optional[int]) -> None:
            if target is not None and 0 <= target < n:
                needed.add(target)

        if self.entry_pc != 0:
            add(self.entry_pc)

        for idx, (op, args) in enumerate(self.instructions):
            if op in ('CALL', 'BL', 'JAL'):
                # 返回地址只在存在 RET 时才有意义; 否则标签会变成未使用。
                if has_ret and idx + 1 < n:
                    needed.add(idx + 1)
                if op == 'JAL':
                    if args and args[0][0] == 'reg':
                        targ = args[1] if len(args) > 1 else None
                    else:
                        targ = args[0] if args else None
                else:
                    targ = args[0] if args else None
                target = self._target(targ) if targ else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op in ('JMP', 'JZ', 'JE', 'JNZ', 'JL', 'JG'):
                targ = args[0] if args else None
                target = self._target(targ) if targ else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op == 'B':
                target_arg = next((a for a in args if a[0] != 'cond'), None)
                target = self._target(target_arg) if target_arg else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op in ('CBZ', 'CBNZ'):
                targ = args[1] if len(args) > 1 else None
                target = self._target(targ) if targ else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op in ('TBZ', 'TBNZ'):
                targ = args[2] if len(args) > 2 else None
                target = self._target(targ) if targ else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op in ('BEQ', 'BNE', 'BLT', 'BGE', 'BLTU', 'BGEU'):
                targ = args[2] if len(args) > 2 else None
                target = self._target(targ) if targ else None
                if target is None:
                    dynamic = True
                else:
                    add(target)
                continue
            if op in ('JALR', 'BR'):
                dynamic = True

        if dynamic:
            needed.update(range(n))
        return needed, (has_ret or dynamic)

    # ---------------- 函数/程序 ----------------

    def _emit_program_header(self) -> None:
        self._emit(f'// Code generated by codecin native backend for '
                   f'{self.program_name}; DO NOT EDIT.')
        self._emit('package main')
        self._emit()
        self._emit('import (')
        self._emit('\t"io"')
        self._emit('\t"os"')
        self._emit()
        self._emit('\t"codecin-native/engine"')
        self._emit(')')
        self._emit()
        self._emit('var nativeSegments = []engine.MemSeg{')
        for addr, data in self.segments:
            self._emit(f'\t{{Addr: {int(addr)}, Data: {_go_bytes(data, chr(9))}}},')
        self._emit('}')
        self._emit()
        self._emit('func main() {')
        self._emit('\tengine.ConsoleInit()')
        self._emit('\tengine.SetProgramArgs(os.Args[1:])')
        self._emit('\tvar in []byte')
        self._emit('\tif fi, err := os.Stdin.Stat(); err == nil && '
                   'fi.Mode()&os.ModeCharDevice == 0 {')
        self._emit('\t\tif data, err := io.ReadAll(os.Stdin); err == nil {')
        self._emit('\t\t\tin = data')
        self._emit('\t\t}')
        self._emit('\t}')
        self._emit(f'\trt := engine.NewNativeRuntime({self.mem_size}, '
                   f'{self.sp}, {self.heap_base}, nativeSegments, '
                   f'{self.max_steps})')
        self._emit('\trt.SetInput(in)')
        self._emit('\tok := run(rt)')
        self._emit('\tos.Exit(rt.Finish(ok))')
        self._emit('}')
        self._emit()

    def _block_starts(self) -> List[int]:
        """基本块入口: 跳转目标、CALL/JAL 返回点、控制指令的下一条。"""
        n = len(self.instructions)
        starts: Set[int] = set()
        if 0 <= self.entry_pc < n:
            starts.add(self.entry_pc)
        dynamic = False

        def add(target: Optional[int]) -> None:
            if target is not None and 0 <= target < n:
                starts.add(target)

        for idx, (op, args) in enumerate(self.instructions):
            target: Optional[int] = None
            is_control = True
            if op in ('JMP', 'JZ', 'JE', 'JNZ', 'JL', 'JG'):
                target = self._target(args[0]) if args else None
            elif op == 'B':
                target_arg = next((a for a in args if a[0] != 'cond'), None)
                target = self._target(target_arg) if target_arg else None
            elif op in ('CBZ', 'CBNZ'):
                target = self._target(args[1]) if len(args) > 1 else None
            elif op in ('TBZ', 'TBNZ', 'BEQ', 'BNE', 'BLT', 'BGE',
                        'BLTU', 'BGEU'):
                target = self._target(args[2]) if len(args) > 2 else None
            elif op in ('CALL', 'BL'):
                target = self._target(args[0]) if args else None
            elif op == 'JAL':
                if args and args[0][0] == 'reg':
                    targ = args[1] if len(args) > 1 else None
                else:
                    targ = args[0] if args else None
                target = self._target(targ) if targ else None
            elif op in ('RET', 'HALT'):
                target = None
            elif op in ('JALR', 'BR'):
                target = None
                dynamic = True
            else:
                is_control = False
            if not is_control:
                continue
            if idx + 1 < n:
                starts.add(idx + 1)
            if target is None:
                dynamic = True
            else:
                add(target)

        if dynamic:
            starts.update(v for v in self.labels.values() if 0 <= v < n)
        if not starts:
            starts.add(0)
        return sorted(starts)

    def _generate_blocks(self) -> str:
        self._emit_program_header()
        n = len(self.instructions)
        starts = self._block_starts()
        blocks = [(s, starts[i + 1] if i + 1 < len(starts) else n)
                  for i, s in enumerate(starts)]

        self._emit('func run(rt *engine.NativeRuntime) bool {')
        self._emit(f'\tpc := {self.entry_pc}')
        self._emit('\tfor {')
        self._emit('\t\tswitch pc {')
        for i, (start, _end) in enumerate(blocks):
            self._emit(f'\t\tcase {start}:')
            self._emit(f'\t\t\tpc = block{i}(rt)')
        self._emit(f'\t\tcase {n}:')
        self._emit('\t\t\treturn true')
        self._emit('\t\tdefault:')
        self._emit('\t\t\treturn rt.Abort("invalid control-flow target")')
        self._emit('\t\t}')
        self._emit('\t\tif pc == -1 { return true }')
        self._emit('\t\tif pc == -2 { return false }')
        self._emit('\t}')
        self._emit('}')
        self._emit()

        for i, (start, end) in enumerate(blocks):
            self._emit(f'func block{i}(rt *engine.NativeRuntime) int {{')
            for idx in range(start, end):
                op, args = self.instructions[idx]
                self._emit('\tif e := rt.Tick(); e != "" {')
                self._emit('\t\trt.Abort(e)')
                self._emit('\t\treturn -2')
                self._emit('\t}')
                self._emit_ins(op, args, idx)
                self._emit()
            self._emit(f'\treturn {end}')
            self._emit('}')
            self._emit()
        return '\n'.join(self.lines) + '\n'

    def generate(self) -> str:
        if not self.instructions:
            self.instructions = [('HALT', [])]
        self.entry_pc = max(0, min(self.entry_pc, len(self.instructions) - 1))
        if self.block_mode:
            return self._generate_blocks()
        self._emit_program_header()
        self._emit('func run(rt *engine.NativeRuntime) bool {')
        self._emit('\tvar target int')
        self._emit('\t_ = target')
        needed, dispatch = self._analyze_targets()
        if self.entry_pc != 0:
            self._emit(f'\tgoto L{self.entry_pc}')
        self._emit()
        for idx, (op, args) in enumerate(self.instructions):
            if idx in needed:
                self._emit(f'L{idx}:')
            self._emit('\tif e := rt.Tick(); e != "" {')
            self._emit('\t\treturn rt.Abort(e)')
            self._emit('\t}')
            self._emit_ins(op, args, idx)
            self._emit()
        if dispatch:
            self._emit('Ldispatch:')
            self._emit('\tswitch target {')
            for idx in sorted(needed):
                if idx >= len(self.instructions):
                    continue
                self._emit(f'\tcase {idx}:')
                self._emit(f'\t\tgoto L{idx}')
            self._emit(f'\tcase {len(self.instructions)}:')
            self._emit('\t\treturn true')
            self._emit('\tdefault:')
            self._emit('\t\treturn rt.Abort("invalid control-flow target")')
            self._emit('\t}')
        self._emit('\treturn true')
        self._emit('}')
        return '\n'.join(self.lines) + '\n'


def generate_go_source(instructions: Sequence[Instruction],
                       labels: Dict[str, int],
                       segments: Segments,
                       entry_pc: int = 0,
                       mem_size: int = 1 << 30,
                       sp: Optional[int] = None,
                       heap_base: Optional[int] = None,
                       max_steps: int = 100000000,
                       program_name: str = 'program',
                       block_mode: Optional[bool] = None) -> str:
    """生成 native AOT 的 Go 源码。

    ``block_mode=None`` 时按指令数自动选择: 超过 800 条使用基本块拆分,
    避免单个 Go 函数过大导致 `go build` 极慢。
    """
    if sp is None:
        sp = int(mem_size) - STACK_SLOT
    if heap_base is None:
        heap_base = int(mem_size) // 2
    if block_mode is None:
        block_mode = len(instructions) > 800
    return _Generator(instructions, labels, segments, entry_pc, mem_size,
                      sp, heap_base, max_steps, program_name,
                      block_mode=block_mode).generate()
