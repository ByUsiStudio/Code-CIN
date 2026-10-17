"""CIN/ASM 指令流优化器 (--optimize 0..3)。

优化发生在 UCBC 编码之前，只改 ``(opcode, operands)`` 列表与代码标签，
不触碰数据段。所有 pass 都以“语义等价”为前提：

  * 被任何代码标签指向的指令永不被删除 (保护分支/函数入口/循环头);
  * 只删除无副作用的整数/移动指令；SYS、内存、浮点/向量、栈操作一律保守;
  * 删除后重新映射标签和入口索引。

级别:
  -O0  不做任何优化
  -O1  去掉 NOP / MOV rd,rd / 跳到下一条的 JMP
  -O2  + 常量折叠 (MOV imm + 整数立即数运算)
  -O3  + 无条件跳转后的不可达代码删除
"""

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

Instruction = Tuple[str, List[Tuple[object, ...]]]

#: 不改变标志位、可安全折叠的二元立即数指令。
_FOLD_BIN = {'ADD', 'SUB', 'MUL', 'AND', 'OR', 'XOR', 'SHL', 'SHR'}
_FOLD_BIN_I = {'ADDI', 'XORI', 'ORI', 'ANDI', 'LSL', 'LSR', 'ASR'}
_TERMINATORS = {'JMP', 'HALT', 'RET', 'BR', 'JAL', 'JALR'}

_MASK64 = 0xFFFFFFFFFFFFFFFF


def _reg(arg: Tuple[object, ...]) -> Optional[int]:
    return int(arg[1]) if arg and arg[0] == 'reg' else None


def _imm(arg: Tuple[object, ...]) -> Optional[int]:
    return int(arg[1]) & _MASK64 if arg and arg[0] == 'imm' else None


def _target(arg: Tuple[object, ...], labels: Dict[str, int]) -> Optional[int]:
    if arg and arg[0] == 'label':
        return labels.get(str(arg[1]))
    if arg and arg[0] == 'imm':
        return int(arg[1])
    return None


def _i64(v: int) -> int:
    v &= _MASK64
    return v - (1 << 64) if v >= (1 << 63) else v


def _fold(op: str, a: int, b: int) -> Optional[int]:
    """折叠整数运算；除零/未知操作返回 None。"""
    if op in ('ADD', 'ADDI'):
        return (a + b) & _MASK64
    if op == 'SUB':
        return (a - b) & _MASK64
    if op == 'MUL':
        return (a * b) & _MASK64
    if op == 'AND':
        return a & b
    if op == 'OR':
        return a | b
    if op in ('XOR', 'XORI'):
        return a ^ b
    if op == 'ORI':
        return a | b
    if op == 'ANDI':
        return a & b
    if op in ('SHL', 'LSL'):
        return (a << (b & 63)) & _MASK64
    if op in ('SHR', 'LSR'):
        return (a & _MASK64) >> (b & 63)
    if op == 'ASR':
        return (_i64(a) >> (b & 63)) & _MASK64
    return None


def _protected(labels: Dict[str, int], entry_pc: int,
               extra: Iterable[int]) -> Set[int]:
    out = {int(v) for v in labels.values() if int(v) >= 0}
    if entry_pc >= 0:
        out.add(int(entry_pc))
    out.update(int(x) for x in extra if int(x) >= 0)
    return out


def _remap(instructions: Sequence[Instruction],
           labels: Dict[str, int],
           entry_pc: int) -> Tuple[List[Instruction], Dict[str, int], int]:
    """删除已被标记为 None 的指令并重映射标签/入口。"""
    mapping: Dict[int, int] = {}
    kept: List[Instruction] = []
    for old, ins in enumerate(instructions):
        if ins is None:         # type: ignore[comparison-overlap]
            continue
        mapping[old] = len(kept)
        kept.append(ins)
    if not kept:
        return [], {}, 0
    new_labels: Dict[str, int] = {}
    for name, old in labels.items():
        new_labels[name] = mapping.get(int(old), len(kept))
    new_entry = mapping.get(int(entry_pc), 0)
    return kept, new_labels, new_entry


def _o1(instructions: List[Optional[Instruction]],
        labels: Dict[str, int],
        protected: Set[int]) -> None:
    for i, ins in enumerate(instructions):
        if ins is None or i in protected:
            continue
        op, args = ins
        if op == 'NOP':
            instructions[i] = None
            continue
        if op == 'MOV' and len(args) >= 2:
            rd, rs = _reg(args[0]), _reg(args[1])
            if rd is not None and rs is not None and rd == rs:
                instructions[i] = None
                continue
        if op == 'ADDI' and len(args) >= 3:
            rd, rs = _reg(args[0]), _reg(args[1])
            imm = _imm(args[2])
            if rd is not None and rs is not None and rd == rs and imm == 0:
                instructions[i] = ('MOV', [('reg', rd), ('reg', rs)])
                continue
        if op == 'JMP' and len(args) >= 1:
            target = _target(args[0], labels)
            if target == i + 1:
                instructions[i] = None


def _o2(instructions: List[Optional[Instruction]],
        labels: Dict[str, int],
        protected: Set[int]) -> None:
    # MOV rd, imm + 立即数运算 -> MOV rd, folded
    for i in range(len(instructions) - 1):
        first = instructions[i]
        second = instructions[i + 1]
        if first is None or second is None or (i + 1) in protected:
            continue
        op1, args1 = first
        if op1 != 'MOV' or len(args1) < 2:
            continue
        rd = _reg(args1[0])
        imm = _imm(args1[1])
        if rd is None or imm is None:
            continue
        op2, args2 = second
        if op2 in _FOLD_BIN and len(args2) >= 2:
            # Base 二元形式: ADD rd, imm 的源就是 rd; 右侧是寄存器时无法折叠。
            if _reg(args2[0]) != rd:
                continue
            rhs = _imm(args2[1])
            if rhs is None:
                continue
            folded = _fold(op2, imm, rhs)
            if folded is None:
                continue
            instructions[i] = ('MOV', [('reg', rd), ('imm', folded)])
            instructions[i + 1] = None
            continue
        if op2 in _FOLD_BIN_I and len(args2) >= 3:
            rd2, rs2 = _reg(args2[0]), _reg(args2[1])
            rhs = _imm(args2[2])
            if rd2 is None or rs2 != rd or rhs is None:
                continue
            folded = _fold(op2, imm, rhs)
            if folded is None:
                continue
            # 源寄存器已知; 目标寄存器可以不同 (ADDI rd, rs, imm)。
            instructions[i + 1] = ('MOV', [('reg', rd2), ('imm', folded)])
            if rd2 == rd and i not in protected:
                instructions[i] = None
            continue
        if op2 == 'MOV' and len(args2) >= 2:
            # MOV rd, imm; MOV rd, imm -> 前者可删 (后者覆盖, 且非标签目标)
            rd2 = _reg(args2[0])
            if rd2 == rd and i not in protected:
                instructions[i] = None


def _o3(instructions: List[Optional[Instruction]],
        labels: Dict[str, int],
        protected: Set[int]) -> None:
    unreachable = False
    for i, ins in enumerate(instructions):
        if ins is None:
            continue
        if i in protected:
            unreachable = False
        if unreachable:
            instructions[i] = None
            continue
        op, args = ins
        if op == 'HALT' or op == 'RET':
            unreachable = True
        elif op == 'JMP' and len(args) >= 1:
            target = _target(args[0], labels)
            # 跳到下一条时后续代码仍可达; 其余跳转后到下一条标签之间不可达。
            if target is not None and target != i + 1:
                unreachable = True


def optimize(instructions: Sequence[Instruction],
             labels: Dict[str, int],
             level: int = 0,
             entry_pc: int = 0,
             protected_extra: Iterable[int] = ()) -> Tuple[List[Instruction],
                                                          Dict[str, int],
                                                          int]:
    """按级别优化指令流，返回 ``(instructions, labels, entry_pc)``。

    level <= 0 时原样返回 (不做复制以外的改动)。标签会重新映射到新下标。
    """
    level = max(0, min(3, int(level)))
    if level == 0 or not instructions:
        return list(instructions), dict(labels), int(entry_pc)

    protected = _protected(labels, entry_pc, protected_extra)
    # CALL/JAL 的返回地址是隐式标签; 不保护的话, 删除恰好位于返回点的
    # 指令会让 RET 落到错误位置。
    protected.update(
        i + 1 for i, (op, _args) in enumerate(instructions)
        if op in ('CALL', 'BL', 'JAL') and i + 1 < len(instructions))
    work: List[Optional[Instruction]] = list(instructions)
    if level >= 1:
        _o1(work, labels, protected)
    if level >= 2:
        _o2(work, labels, protected)
    if level >= 3:
        _o3(work, labels, protected)
        # 删除不可达代码后可能暴露新的“跳到下一条”，再跑一次 O1
        _o1(work, labels, protected)

    return _remap(work, labels, entry_pc)
