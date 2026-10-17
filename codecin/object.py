"""可重定位 ASM/PL 目标文件与链接器。

一个 ``.co`` 对象包含:
  * 未解析标签的操作数 (``('label', name)``);
  * 本地代码标签 / 数据标签;
  * 初始内存段;
  * 入口符号/偏移与内存/SP/Heap 参数。

``link_objects`` 负责合并多个对象、给代码/数据分配新基址、解析标签并
产出可直接交给 AOT/VM 的指令流。``.colib`` 归档由 ``link_inputs`` 按
未定义符号选择性拉取成员; ``gc_sections=True`` 可删除入口不可达代码。
ASM/PL 使用未解析标签对象; CIN 无 ``main`` 的源文件也可作为函数库对象
参与链接。

限制: 跨对象符号支持裸标签、``NAME±N`` 简单重定位, 以及 ``DW/DD/DQ label``
数据段指针重定位; 更复杂的标签算术/多符号表达式仍在对象内解析。
"""

import base64
import hashlib
import json
import os
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .assembler import Assembler
from .config import DEFAULT_MEM_SIZE
from .memory import FastMemory

OBJECT_FORMAT = 'codecin-object'
OBJECT_VERSION = 1
ARCHIVE_FORMAT = 'codecin-archive'
ARCHIVE_VERSION = 1
_STACK_SLOT = 8

Instruction = Tuple[str, List[Tuple[Any, ...]]]


class ObjectError(Exception):
    """目标文件格式/链接错误。"""


def _decode_arg(arg: Sequence[Any]) -> Tuple[Any, ...]:
    out: List[Any] = []
    for value in arg:
        if isinstance(value, list):
            out.append(tuple(value))
        else:
            out.append(value)
    return tuple(out)


def compile_asm_object(program_file: str,
                       mem_size: int = DEFAULT_MEM_SIZE) -> Dict[str, Any]:
    """把 ASM/PL 源文件编译成可重定位对象 (标签保持未解析)。"""
    mem_size = max(256, int(mem_size))
    memory = FastMemory(mem_size)
    asm = Assembler(memory, preserve_labels=True)
    instructions, labels, data_labels = asm.assemble_file(program_file)
    entry_symbol = 'main' if 'main' in labels else None
    entry_offset = labels.get('main', 0)
    segments = [(addr, bytes(data))
                for addr, data in memory.snapshot_segments()]
    return {
        'format': OBJECT_FORMAT,
        'version': OBJECT_VERSION,
        'kind': 'asm',
        'source': os.path.abspath(program_file),
        'instructions': [(op, [tuple(a) for a in args])
                         for op, args in instructions],
        'labels': dict(labels),
        'data_labels': dict(data_labels),
        'data_relocations': [list(r) for r in asm.data_relocations],
        'segments': [[addr, base64.b64encode(data).decode('ascii')]
                     for addr, data in segments],
        'entry_symbol': entry_symbol,
        'entry_offset': entry_offset,
        'has_entry': entry_symbol is not None,
        'mem_size': mem_size,
        'sp': mem_size - _STACK_SLOT,
        'heap_base': mem_size // 2,
    }


def compile_cin_object(program_file: str,
                       mem_size: int = DEFAULT_MEM_SIZE) -> Dict[str, Any]:
    """把 CIN 源文件编译成对象；无 main 的文件作为函数库对象。

    数据地址 (字符串/标量全局/struct 指针槽) 以对象内前缀符号 + 重定位保存,
    链接时按数据基址重写。无 main 的库对象会自动去掉 ``CALL main; HALT``
    入口桩。
    """
    from .cin import CINCompiler

    mem_size = max(256, int(mem_size))
    try:
        res = CINCompiler().compile(program_file, relocatable=True)
    except Exception as e:                  # noqa: BLE001
        raise ObjectError(f'CIN 编译失败: {e}') from e
    instructions = list(res.instructions)
    labels = dict(res.labels)
    data_labels = dict(res.data_labels)
    # CIN 数据标签是对象内局部符号 (str_0 等), 必须加对象前缀避免多对象冲突。
    prefix = ('__cin_' + hashlib.sha1(
        os.path.abspath(program_file).encode()).hexdigest()[:10] + '__')
    renames = {name: prefix + name for name in data_labels
               if not name.startswith('global_')}
    if renames:
        data_labels = {renames.get(name, name): addr
                       for name, addr in data_labels.items()}
        rewritten = []
        for op, args in instructions:
            new_args = []
            for arg in args:
                if arg and arg[0] in ('reloc', 'label') \
                        and arg[1] in renames:
                    if arg[0] == 'reloc':
                        new_args.append(('reloc', renames[arg[1]], arg[2]))
                    else:
                        new_args.append(('label', renames[arg[1]]))
                else:
                    new_args.append(arg)
            rewritten.append((op, new_args))
        instructions = rewritten
    data_relocations = [
        (addr, width, renames.get(name, name), addend)
        for addr, width, name, addend in res.data_relocations
    ]
    has_main = 'main' in labels
    if not has_main:
        remove = None
        for i, (op, args) in enumerate(instructions):
            if (op == 'CALL' and args and args[0][0] == 'label'
                    and args[0][1] == 'main'):
                remove = i
                break
        if remove is not None:
            end = remove + 1
            if (end < len(instructions)
                    and instructions[end][0] == 'HALT'):
                end += 1
            del instructions[remove:end]
            shift = end - remove
            labels = {name: (idx - shift if idx >= end else idx)
                      for name, idx in labels.items()}
    segs: List[Tuple[int, bytes]] = []
    if res.data_writes:
        end_max = max(addr + len(data) for addr, data in res.data_writes)
        buf = bytearray(end_max)
        for addr, data in res.data_writes:
            buf[addr:addr + len(data)] = data
        segs.append((0, bytes(buf)))
    return {
        'format': OBJECT_FORMAT,
        'version': OBJECT_VERSION,
        'kind': 'cin',
        'source': os.path.abspath(program_file),
        'instructions': [(op, [tuple(a) for a in args])
                         for op, args in instructions],
        'labels': labels,
        'data_labels': data_labels,
        'data_relocations': [list(r) for r in data_relocations],
        'segments': [[addr, base64.b64encode(data).decode('ascii')]
                     for addr, data in segs],
        'entry_symbol': None,
        'entry_offset': 0,
        'has_entry': has_main,
        'mem_size': mem_size,
        'sp': mem_size - _STACK_SLOT,
        'heap_base': mem_size // 2,
    }


def write_object(obj: Dict[str, Any], path: str) -> str:
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    return os.path.abspath(path)


def read_object(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
    except (OSError, ValueError) as e:
        raise ObjectError(f'无法读取对象文件 {path}: {e}') from e
    if not isinstance(obj, dict) or obj.get('format') != OBJECT_FORMAT:
        raise ObjectError(f'不是 Code CIN 对象文件: {path}')
    if obj.get('version') != OBJECT_VERSION:
        raise ObjectError(f'不支持的对象版本: {obj.get("version")}')
    obj['instructions'] = [
        (op, [_decode_arg(a) for a in args])
        for op, args in obj.get('instructions', [])
    ]
    obj['segments'] = [
        (int(addr), base64.b64decode(data))
        for addr, data in obj.get('segments', [])
    ]
    return obj


def _align(value: int, align: int = 16) -> int:
    return (value + align - 1) // align * align


_CONTROL_BRANCH = {'B'}
_CONTROL_JUMP = {'JMP'}
_CONTROL_CALL = {'CALL', 'JAL'}
_CONTROL_STOP = {'RET', 'HALT'}


def _control_target(op: str, args: Sequence[Tuple[Any, ...]]):
    if op in _CONTROL_BRANCH | _CONTROL_JUMP | _CONTROL_CALL:
        for arg in args:
            if arg and arg[0] == 'imm':
                return int(arg[1])
    return None


def gc_instructions(instructions: Sequence[Instruction],
                    labels: Dict[str, int],
                    entry_pc: int) -> Optional[Tuple[List[Instruction],
                                                     Dict[str, int], int]]:
    """保守的链接期代码段 GC: 从入口沿控制流删除不可达函数/桩。

    只要遇到动态控制流 (CALL/JMP 寄存器) 就放弃 GC 返回 None, 避免误删。
    数据指针指向代码的情况由调用方先行禁用 GC。
    """
    n = len(instructions)
    if n == 0:
        return None
    entry = int(entry_pc)
    if not (0 <= entry < n):
        return None

    reachable = set()
    stack = [entry]
    while stack:
        idx = stack.pop()
        if idx < 0 or idx >= n or idx in reachable:
            continue
        reachable.add(idx)
        op, args = instructions[idx]
        if op in _CONTROL_STOP:
            continue
        if op in _CONTROL_JUMP | _CONTROL_BRANCH:
            target = _control_target(op, args)
            if target is None:
                return None
            stack.append(target)
            conditional = any(a and a[0] == 'cond' for a in args)
            if op in _CONTROL_BRANCH and not conditional:
                continue            # 无条件 B
            stack.append(idx + 1)
            continue
        if op in _CONTROL_CALL:
            target = _control_target(op, args)
            if target is None:
                return None
            stack.append(target)
            stack.append(idx + 1)
            continue
        if op in ('BR', 'JR', 'BRANCH'):
            return None
        stack.append(idx + 1)

    old_to_new = {}
    new_ins: List[Instruction] = []
    for old in sorted(reachable):
        old_to_new[old] = len(new_ins)
        new_ins.append(instructions[old])

    def remap(idx: int) -> int:
        if idx not in old_to_new:
            raise ObjectError(f'GC 后仍引用不可达指令: {idx}')
        return old_to_new[idx]

    for i, (op, args) in enumerate(new_ins):
        if op not in _CONTROL_JUMP | _CONTROL_BRANCH | _CONTROL_CALL:
            continue
        new_args = []
        for arg in args:
            if arg and arg[0] == 'imm':
                new_args.append(('imm', remap(int(arg[1]))))
            else:
                new_args.append(arg)
        new_ins[i] = (op, new_args)

    new_labels = {name: remap(idx) for name, idx in labels.items()
                  if idx in reachable}
    return new_ins, new_labels, remap(entry)


def link_objects(objects: Sequence[Dict[str, Any]],
                 gc_sections: bool = False) -> Dict[str, Any]:
    """合并多个可重定位对象, 返回解析后的程序描述。"""
    if not objects:
        raise ObjectError('没有输入对象')

    # 入口对象放到最前: CIN 主对象的数据地址是编译期绝对值, 数据基址必须为 0。
    entry_objs = [o for o in objects
                  if o.get('has_entry') or o.get('entry_symbol')]
    if len(entry_objs) > 1:
        raise ObjectError('多个对象都定义了入口')
    if entry_objs:
        first = entry_objs[0]
        objects = [first] + [o for o in objects if o is not first]

    code_base = 0
    data_cursor = 0
    code_labels: Dict[str, int] = {}
    data_labels: Dict[str, int] = {}
    segments: List[Tuple[int, bytes]] = []
    placements: List[Tuple[Dict[str, Any], int, int]] = []

    for obj in objects:
        if obj.get('format') != OBJECT_FORMAT:
            raise ObjectError('输入不是 Code CIN 对象')
        data_base = _align(data_cursor)
        placements.append((obj, code_base, data_base))
        for name, idx in obj.get('labels', {}).items():
            if name in code_labels or name in data_labels:
                raise ObjectError(f'重复符号: {name}')
            code_labels[name] = code_base + int(idx)
        for name, off in obj.get('data_labels', {}).items():
            if name in code_labels or name in data_labels:
                raise ObjectError(f'重复符号: {name}')
            data_labels[name] = data_base + int(off)
        for addr, data in obj.get('segments', []):
            if isinstance(data, str):
                data = base64.b64decode(data)
            segments.append((data_base + int(addr), bytes(data)))
            data_cursor = max(data_cursor, data_base + int(addr) + len(data))
        code_base += len(obj.get('instructions', []))

    # 数据段重定位: DQ/DD/DW label 等。合并成连续数据缓冲后按符号写回。
    if data_cursor:
        data_buf = bytearray(data_cursor)
        for addr, data in segments:
            data_buf[addr:addr + len(data)] = data
        for obj, _code_base, data_base in placements:
            for off, width, name, addend in obj.get('data_relocations', []):
                if name in code_labels:
                    value = code_labels[name] + int(addend)
                elif name in data_labels:
                    value = data_labels[name] + int(addend)
                else:
                    raise ObjectError(f'未定义符号: {name}')
                w = int(width)
                at = data_base + int(off)
                if w == 2:
                    struct.pack_into('<H', data_buf, at, value & 0xFFFF)
                elif w == 4:
                    struct.pack_into('<I', data_buf, at, value & 0xFFFFFFFF)
                elif w == 8:
                    struct.pack_into('<Q', data_buf, at,
                                     value & 0xFFFFFFFFFFFFFFFF)
                else:
                    raise ObjectError(f'不支持的数据重定位宽度: {w}')
        segments = [(0, bytes(data_buf))]

    # 入口: 恰好一个对象带入口; ASM 用 main 符号, CIN 用入口桩偏移。
    entries = [p for p in placements
               if p[0].get('has_entry') or p[0].get('entry_symbol')]
    if len(entries) > 1:
        raise ObjectError('多个对象都定义了入口')
    if entries:
        obj, base, _data_base = entries[0]
        symbol = obj.get('entry_symbol')
        if symbol:
            if symbol not in code_labels:
                raise ObjectError(f'入口符号未定义: {symbol}')
            entry_pc = code_labels[symbol]
        else:
            entry_pc = base + int(obj.get('entry_offset', 0))
    else:
        obj, base, _data_base = placements[0]
        entry_pc = base + int(obj.get('entry_offset', 0))

    instructions: List[Instruction] = []
    for obj, _code_base, _data_base in placements:
        for op, args in obj.get('instructions', []):
            new_args = []
            for arg in args:
                if arg and arg[0] in ('label', 'reloc'):
                    name = arg[1]
                    addend = int(arg[2]) if arg[0] == 'reloc' else 0
                    if name in code_labels:
                        new_args.append(('imm', code_labels[name] + addend))
                    elif name in data_labels:
                        new_args.append(('imm', data_labels[name] + addend))
                    else:
                        raise ObjectError(f'未定义符号: {name}')
                else:
                    new_args.append(tuple(arg))
            instructions.append((op, new_args))

    # 数据里保存了代码地址时不做 GC, 否则重排指令会让函数指针失效。
    has_code_pointer = False
    for obj, _b, _d in placements:
        for _off, _w, name, _add in obj.get('data_relocations', []):
            if name in code_labels:
                has_code_pointer = True
                break
    if gc_sections and not has_code_pointer:
        gc_res = gc_instructions(instructions, code_labels, entry_pc)
        if gc_res is not None:
            instructions, code_labels, entry_pc = gc_res

    mem_size = max(int(obj.get('mem_size', DEFAULT_MEM_SIZE))
                   for obj, _b, _d in placements)
    return {
        'format': OBJECT_FORMAT,
        'version': OBJECT_VERSION,
        'source': '<linked>',
        'instructions': instructions,
        'labels': code_labels,
        'data_labels': data_labels,
        'segments': segments,
        'entry_pc': entry_pc,
        'mem_size': mem_size,
        'sp': mem_size - _STACK_SLOT,
        'heap_base': mem_size // 2,
    }


def create_archive(objects: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        'format': ARCHIVE_FORMAT,
        'version': ARCHIVE_VERSION,
        'members': list(objects),
    }


def write_archive(objects: Sequence[Dict[str, Any]], path: str) -> str:
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(create_archive(objects), f, ensure_ascii=False,
                  separators=(',', ':'))
        f.write('\n')
    return os.path.abspath(path)


def read_archive(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ObjectError(f'无法读取归档文件 {path}: {e}') from e
    if not isinstance(data, dict) or data.get('format') != ARCHIVE_FORMAT:
        raise ObjectError(f'不是 Code CIN 归档文件: {path}')
    if data.get('version') != ARCHIVE_VERSION:
        raise ObjectError(f'不支持的归档版本: {data.get("version")}')
    members = data.get('members')
    if not isinstance(members, list):
        raise ObjectError(f'归档成员非法: {path}')
    return members


def _defined_symbols(obj: Dict[str, Any]) -> set:
    return set(obj.get('labels', {})) | set(obj.get('data_labels', {}))


def _referenced_symbols(obj: Dict[str, Any]) -> set:
    refs = set()
    for _op, args in obj.get('instructions', []):
        for arg in args:
            if arg and arg[0] in ('label', 'reloc'):
                refs.add(arg[1])
    for _addr, _width, name, _add in obj.get('data_relocations', []):
        refs.add(name)
    return refs


def _select_archive_members(explicit: List[Dict[str, Any]],
                            archives: List[List[Dict[str, Any]]]
                            ) -> List[Dict[str, Any]]:
    """C 风格静态库语义: 只拉取能解决当前未定义符号的成员。"""
    selected = list(explicit)
    defined = set()
    for obj in selected:
        defined |= _defined_symbols(obj)
    unresolved = set()
    for obj in selected:
        unresolved |= _referenced_symbols(obj)
    unresolved -= defined

    changed = True
    while changed:
        changed = False
        for members in archives:
            for obj in members:
                if obj in selected:
                    continue
                gained = _defined_symbols(obj) & unresolved
                if not gained:
                    continue
                selected.append(obj)
                defined |= _defined_symbols(obj)
                unresolved |= _referenced_symbols(obj)
                unresolved -= defined
                changed = True
    if unresolved:
        raise ObjectError('未定义符号: ' + ', '.join(sorted(unresolved)[:8]))
    return selected


def read_input_object(path: str) -> Dict[str, Any]:
    """链接输入: 对象文件或 CIN/ASM/PL 源文件 (源文件即时编译为对象)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.cin':
        return compile_cin_object(path)
    if ext in ('.asm', '.pl'):
        return compile_asm_object(path)
    return read_object(path)


def link_inputs(paths: Sequence[str],
                gc_sections: bool = False) -> Dict[str, Any]:
    """链接对象、源文件与 ``.colib`` 静态库归档。"""
    explicit: List[Dict[str, Any]] = []
    archives: List[List[Dict[str, Any]]] = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.cin', '.asm', '.pl'):
            explicit.append(read_input_object(path))
            continue
        try:
            explicit.append(read_object(path))
            continue
        except ObjectError:
            pass
        archives.append(read_archive(path))
    return link_objects(_select_archive_members(explicit, archives),
                        gc_sections=gc_sections)


def link_files(paths: Sequence[str],
               gc_sections: bool = False) -> Dict[str, Any]:
    return link_inputs(paths, gc_sections=gc_sections)
