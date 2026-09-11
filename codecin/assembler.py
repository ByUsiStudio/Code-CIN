import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .console import Console
from .errors import AssemblerError
from .isa import Constants
from .memory import FastMemory

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]

_RE_XREG = re.compile(r'^[xXrRwW]([0-9]|[12][0-9]|3[01])$')
_RE_VREG = re.compile(r'^[vV]([0-9]|[12][0-9]|3[01])(?:\.([0-3]))?$')
_RE_LABEL = re.compile(r'^[a-zA-Z_.$][a-zA-Z0-9_.$]*$')
_RE_EXPR_NAME = re.compile(r'\b[a-zA-Z_.$][a-zA-Z0-9_.$]*\b')
_RE_EQU_DIRECTIVE = {'EQU', 'SET'}
# 表达式只允许: 数字 / 已定义符号 / + - * / % ( ) 空白
_RE_EXPR_OK = re.compile(r'^[0-9+\-*/%()\s]+$')
# 表达式内的数值字面量 (十进制 / 0x / 0b / 0o, 允许下划线)
_RE_EXPR_NUM = re.compile(
    r'0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*')


def _eval_expr(text: str, symbols: Dict[str, int]) -> Optional[int]:
    """求值简单算术表达式 (数值/已定义符号/四则/取模/括号/一元符号)。

    失败返回 None (调用方回退其它解析路径)。
    """
    text = text.strip().lstrip('#')
    if not text:
        return None

    # 先把数值字面量替换为整数字符串, 避免 0xFF 中的字母与符号名混淆
    def repl_num(match):
        raw = match.group(0).replace('_', '')
        if len(raw) > 1 and raw[:2].lower() in ('0x', '0b', '0o'):
            raw = '0' + raw[1].lower() + raw[2:]
        try:
            return str(int(raw, 0))
        except ValueError:
            return '0'

    text = _RE_EXPR_NUM.sub(repl_num, text)

    def repl_name(match):
        name = match.group(0)
        if name not in symbols:
            raise KeyError(name)
        return str(symbols[name])

    try:
        expr = _RE_EXPR_NAME.sub(repl_name, text)
    except KeyError:
        return None
    if not _RE_EXPR_OK.match(expr):
        return None
    try:
        return int(eval(expr, {'__builtins__': {}}, {}))  # noqa: S307 - 本地汇编器输入
    except Exception:
        return None


class Assembler:
    def __init__(self, memory: FastMemory, console: Optional[Console] = None,
                 strict: bool = False, logger=None):
        self.memory = memory
        self.console = console or Console()
        self.strict = strict
        self.logger = logger
        self.instructions: List[Instruction] = []
        self.labels: Dict[str, int] = {}
        self.data_labels: Dict[str, int] = {}
        self.equ: Dict[str, int] = {}   # .equ/.set 常量

    def _dbg(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.debug(msg)

    def assemble_file(self, filename: str) -> Tuple[List[Instruction], Dict[str, int], Dict[str, int]]:
        lines = self._preprocess(filename, set())
        self._dbg(f"ASM preprocess: {len(lines)} lines from {filename}")
        result = self._assemble_lines(lines, filename)
        self._dbg(f"ASM assembled: {len(result[0])} instructions, "
                  f"{len(result[1])} labels, {len(result[2])} data labels")
        return result

    def assemble_source(self, source: str, filename: str = '<source>'
                        ) -> Tuple[List[Instruction], Dict[str, int], Dict[str, int]]:
        lines = []
        for line_num, raw in enumerate(source.splitlines(), 1):
            cleaned = self._strip_comment(raw)
            if cleaned.strip():
                lines.append((cleaned.strip(), line_num, filename))
        return self._assemble_lines(lines, filename)

    def _strip_comment(self, raw: str) -> str:
        for marker in (';', '//'):
            idx = raw.find(marker)
            if idx >= 0:
                raw = raw[:idx]
        result = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                result.append(ch)
                i += 1
                while i < len(raw):
                    result.append(raw[i])
                    if raw[i] == '"' and raw[i - 1] != '\\':
                        i += 1
                        break
                    i += 1
                continue
            if ch == '#':
                at_start = len(''.join(result).strip()) == 0
                rest = raw[i + 1:]
                if at_start:
                    if rest.startswith('include'):
                        result.append(raw[i:])
                        return ''.join(result)
                    break
                prev = raw[i - 1] if i > 0 else ' '
                if prev in ' \t' and (rest == '' or rest[0] in ' \t'):
                    break
            result.append(ch)
            i += 1
        return ''.join(result)

    def _preprocess(self, filename: str, loaded: Set[str]) -> List[Tuple[str, int, str]]:
        abs_path = os.path.abspath(filename)
        if abs_path in loaded:
            return []
        loaded.add(abs_path)
        dir_path = os.path.dirname(abs_path)

        if not os.path.exists(abs_path):
            raise AssemblerError('', f"File '{filename}' not found", filename=filename)

        out: List[Tuple[str, int, str]] = []
        with open(abs_path, 'r', encoding='utf-8') as f:
            for line_num, raw in enumerate(f, 1):
                stripped = raw.strip()
                if stripped.startswith('#include'):
                    parts = stripped.split(None, 1)
                    if len(parts) < 2:
                        raise AssemblerError(stripped, "#include format error",
                                             line_num, filename)
                    inc_file = parts[1].strip().strip('"<>')
                    inc_path = os.path.join(dir_path, inc_file)
                    if not os.path.exists(inc_path):
                        raise AssemblerError(stripped,
                                             f"Include file '{inc_file}' not found",
                                             line_num, filename)
                    out.extend(self._preprocess(inc_path, loaded))
                    continue
                cleaned = self._strip_comment(raw)
                if cleaned.strip():
                    out.append((cleaned.strip(), line_num, filename))
        return out

    def _parse_equ(self, line: str, line_num: int, fname: str) -> None:
        """.equ/.set NAME <expr>  (名称/值/四则/括号/已定义符号)。"""
        head = line.split(None, 1)
        rest = (head[1] if len(head) > 1 else '').lstrip(',').strip()
        m = re.match(r'^([A-Za-z_.$][A-Za-z0-9_.$]*)\s*(?:=\s*)?(.*)$', rest)
        if not m or not m.group(2).strip():
            raise AssemblerError(
                line, ".equ format: .equ NAME <expr> (例: .equ N, 8*4)",
                line_num, fname)
        name, expr = m.group(1), m.group(2).strip().lstrip(',').strip()
        sym = dict(self.equ)
        sym.update(self.labels)
        sym.update(self.data_labels)
        val = _eval_expr(expr, sym)
        if val is None:
            raise AssemblerError(line, f"Bad .equ expression: {expr!r}",
                                 line_num, fname)
        self.equ[name] = val

    def _assemble_lines(self, lines: List[Tuple[str, int, str]], filename: str
                        ) -> Tuple[List[Instruction], Dict[str, int], Dict[str, int]]:
        self.instructions = []
        self.labels = {}
        self.data_labels = {}
        self.equ = {}
        instr_index = 0
        data_addr = 0
        section = 'TEXT'
        text_lines: List[Tuple[str, int, int, str]] = []

        i = 0
        while i < len(lines):
            line, line_num, fname = lines[i]

            upper = line.upper()
            if upper in ('.TEXT', '.CODE', 'TEXT', 'CODE'):
                section = 'TEXT'
                i += 1
                continue
            if upper in ('.DATA', 'DATA'):
                section = 'DATA'
                i += 1
                continue

            # .equ/.set 常量定义 (需带点前缀, 避免与 PL 关键字 'set' 冲突)
            head_tok = line.split(None, 1)
            if head_tok and head_tok[0].lower() in ('.equ', '.set'):
                self._parse_equ(line, line_num, fname)
                i += 1
                continue

            rest_line = line
            if ':' in line and not line.startswith('['):
                before, after = line.split(':', 1)
                label = before.strip()
                if _RE_LABEL.match(label):
                    if section == 'TEXT':
                        self.labels[label] = instr_index
                    else:
                        self.data_labels[label] = data_addr
                    rest_line = after.strip()
                    if not rest_line:
                        i += 1
                        continue
                else:
                    raise AssemblerError(line, f"Invalid label: {label}", line_num, fname)

            if section == 'DATA':
                sym = dict(self.equ)
                sym.update(self.labels)
                sym.update(self.data_labels)
                data_addr = self._handle_data(rest_line, data_addr, line_num,
                                              fname, sym)
                i += 1
                continue

            text_lines.append((rest_line, instr_index, line_num, fname))
            instr_index += 1
            i += 1

        # 指令二次解析: 符号表 = 标签 + 数据标签 + .equ (标签优先)
        sym_final = dict(self.equ)
        sym_final.update(self.labels)
        sym_final.update(self.data_labels)
        for line, idx, line_num, fname in text_lines:
            instr = self._parse_instruction(line, sym_final, line_num, fname)
            self.instructions.append(instr)

        return self.instructions, self.labels, self.data_labels

    def _handle_data(self, line: str, data_addr: int, line_num: int, fname: str,
                     symbols: Optional[Dict[str, int]] = None) -> int:
        # 先按空白拆出指令名 (如 ASCIZ "..."), 其余按逗号拆分
        head_tokens = line.split(None, 1)
        if not head_tokens:
            return data_addr
        directive = head_tokens[0].upper().lstrip('.')
        rest = head_tokens[1] if len(head_tokens) > 1 else ''
        parts = [directive] + self._split_operands(rest)
        if not parts:
            return data_addr
        if directive in ('BYTE',):
            directive = 'DB'
        elif directive in ('WORD',):
            directive = 'DW'
        elif directive in ('DWORD',):
            directive = 'DD'
        elif directive in ('QWORD',):
            directive = 'DQ'
        if directive not in Constants.DATA_DIRECTIVES:
            raise AssemblerError(line, f"Unknown data directive: {parts[0]}",
                                 line_num, fname)

        width = {'DB': 1, 'DW': 2, 'DD': 4, 'DQ': 8}.get(directive)

        for val_str in parts[1:]:
            val_str = val_str.strip()
            if not val_str:
                continue
            if directive in ('ASCII', 'ASCIZ', 'STRING'):
                text = self._unquote(val_str)
                raw = text.encode('utf-8')
                self.memory.write_block(data_addr, raw)
                data_addr += len(raw)
                if directive in ('ASCIZ', 'STRING'):
                    self.memory.write_byte(data_addr, 0)
                    data_addr += 1
                continue
            for piece in val_str.split(','):
                piece = piece.strip()
                if not piece:
                    continue
                values = self._parse_data_values(piece, line, line_num, fname,
                                                 symbols)
                for val in values:
                    if width == 1:
                        self.memory.write_byte(data_addr, val & 0xFF)
                    elif width == 2:
                        self.memory.write_word(data_addr, val & 0xFFFF)
                    elif width == 4:
                        self.memory.write_dword(data_addr, val & 0xFFFFFFFF)
                    else:
                        self.memory.write_qword(data_addr, val & 0xFFFFFFFFFFFFFFFF)
                    data_addr += width
        return data_addr

    def _parse_data_values(self, token: str, line: str, line_num: int,
                           fname: str,
                           symbols: Optional[Dict[str, int]] = None) -> List[int]:
        token = token.strip().rstrip(',')
        if token.startswith("'") and token.endswith("'") and len(token) >= 3:
            body = token[1:-1]
            if body.startswith('\\'):
                return [{'n': 10, 't': 9, 'r': 13, '0': 0, '\\': 92,
                         "'": 39, '"': 34}.get(body[1], ord(body[1]))]
            return [ord(body[0])]
        if token.startswith('"') and token.endswith('"'):
            vals = [b for b in token[1:-1].encode('utf-8')]
            vals.append(0)
            return vals
        try:
            return [self.parse_immediate(token)]
        except ValueError:
            val = _eval_expr(token, symbols or {})
            if val is not None:
                return [val]
            raise AssemblerError(line, f"Invalid data value: {token}",
                                 line_num, fname)

    @staticmethod
    def _unquote(token: str) -> str:
        token = token.strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            return token[1:-1].encode('utf-8').decode('unicode_escape')
        return token

    @staticmethod
    def parse_immediate(val: str) -> int:
        val = val.strip().lstrip('#').replace('_', '')
        if not val:
            raise ValueError("empty immediate")
        # 数值后缀 (u/U/l/L 及 f/F) 在 64 位槽模型下无宽度差异, 直接忽略
        while val and val[-1] in 'uUlLfF':
            val = val[:-1]
        if not val:
            raise ValueError("empty immediate")
        neg = False
        if val[0] in '+-':
            neg = val[0] == '-'
            val = val[1:]
        if val.lower().startswith('0x'):
            n = int(val, 16)
        elif val.lower().startswith('0b'):
            n = int(val[2:], 2)
        elif val.lower().startswith('0o'):
            n = int(val[2:], 8)
        else:
            n = int(val)
        return -n if neg else n

    def _split_operands(self, text: str) -> List[str]:
        parts = []
        depth = 0
        current = []
        in_str = False
        for ch in text:
            if ch == '"':
                in_str = not in_str
                current.append(ch)
            elif not in_str and ch == '[':
                depth += 1
                current.append(ch)
            elif not in_str and ch == ']':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0 and not in_str:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        tail = ''.join(current).strip()
        if tail:
            parts.append(tail)
        return parts

    def _parse_instruction(self, line: str, symbols: Dict[str, int],
                           line_num: int, fname: str) -> Instruction:
        tokens = line.split(None, 1)
        mnemonic_raw = tokens[0]
        rest = tokens[1] if len(tokens) > 1 else ''

        cond_prefix = None
        mnemonic = mnemonic_raw
        if '.' in mnemonic_raw:
            base, suffix = mnemonic_raw.split('.', 1)
            suffix_up = suffix.upper()
            if suffix_up in Constants.CONDITIONS:
                mnemonic = base
                cond_prefix = suffix_up

        keyword = mnemonic.lower()
        if keyword in Constants.PL_KEYWORDS:
            opcode = Constants.PL_KEYWORDS[keyword]
        else:
            opcode = mnemonic.upper()
            if opcode not in Constants.OPCODE_NAME_TO_ENUM:
                raise AssemblerError(line, f"Unknown instruction: {mnemonic_raw}",
                                     line_num, fname)

        operands: List[Operand] = []
        if cond_prefix:
            operands.append(('cond', cond_prefix))

        for tok in self._split_operands(rest):
            operands.append(self._parse_operand(tok, symbols, line, line_num, fname))

        if self.strict:
            expected = Constants.ARG_COUNTS.get(Constants.OPCODE_NAME_TO_ENUM[opcode], -1)
            if expected >= 0 and len(operands) != expected:
                raise AssemblerError(
                    line,
                    f"Argument count mismatch for {opcode}: expected {expected}, "
                    f"got {len(operands)}", line_num, fname)

        return (opcode, operands)

    def _parse_operand(self, tok: str, symbols: Dict[str, int], line: str,
                       line_num: int, fname: str) -> Operand:
        tok = tok.strip()
        if not tok:
            raise AssemblerError(line, "Empty operand", line_num, fname)

        if tok.startswith('['):
            if not tok.endswith(']'):
                raise AssemblerError(line, f"Malformed memory operand: {tok}",
                                     line_num, fname)
            return self._parse_mem(tok[1:-1], symbols, line, line_num, fname)

        m = _RE_VREG.match(tok)
        if m:
            reg = int(m.group(1))
            if m.group(2) is not None:
                return ('veclane', reg, int(m.group(2)))
            return ('vec', reg)

        if _RE_XREG.match(tok):
            return ('reg', int(tok[1:]))
        if tok.lower() == 'sp':
            return ('reg', Constants.SP_REG)
        if tok.lower() in ('fp',):
            return ('reg', 29)
        if tok.lower() in ('lr',):
            return ('reg', 30)
        if tok.lower() == 'xzr':
            return ('reg', 31)

        if tok.upper() in Constants.CONDITIONS:
            return ('cond', tok.upper())

        if re.match(r'^[+-]?\d+\.\d+([eE][+-]?\d+)?$', tok):
            return ('float', float(tok))

        if tok.startswith('='):
            name = tok[1:].strip()
            if name in symbols:
                return ('imm', symbols[name])
            val = _eval_expr(name, symbols)
            if val is not None:
                return ('imm', val)
            raise AssemblerError(line, f"Undefined symbol: {name}", line_num, fname)

        if tok.startswith('#'):
            inner = tok[1:].strip()
            try:
                return ('imm', self.parse_immediate(tok))
            except ValueError:
                if inner in symbols:
                    return ('imm', symbols[inner])
                val = _eval_expr(inner, symbols)
                if val is not None:
                    return ('imm', val)
                raise AssemblerError(line, f"Bad immediate: {tok}", line_num, fname)

        try:
            return ('imm', self.parse_immediate(tok))
        except ValueError:
            pass

        # 表达式立即数 / 符号算术: 8+4*2 / SIZE-1 / loop+4 / 等
        val = _eval_expr(tok, symbols)
        if val is not None:
            return ('imm', val)

        if _RE_LABEL.match(tok):
            if tok in symbols:
                return ('imm', symbols[tok])
            raise AssemblerError(line, f"Undefined label: {tok}", line_num, fname)

        raise AssemblerError(line, f"Cannot parse operand: {tok}", line_num, fname)

    def _parse_mem(self, inner: str, symbols: Dict[str, int], line: str,
                   line_num: int, fname: str) -> Operand:
        parts = [p.strip() for p in self._split_operands(inner) if p.strip()]
        if not parts:
            raise AssemblerError(line, "Empty memory operand", line_num, fname)

        base = -1
        offset = 0

        first = parts[0]
        if _RE_XREG.match(first) or first.lower() == 'sp':
            base = Constants.SP_REG if first.lower() == 'sp' else int(first[1:])
        else:
            try:
                offset = self.parse_immediate(first)
            except ValueError:
                val = _eval_expr(first.lstrip('#'), symbols)
                if val is None:
                    raise AssemblerError(line, f"Bad memory base: {first}",
                                         line_num, fname)
                offset = val

        if len(parts) > 1:
            second = parts[1]
            try:
                offset += self.parse_immediate(second)
            except ValueError:
                val = _eval_expr(second.lstrip('#'), symbols)
                if val is None:
                    raise AssemblerError(line, f"Bad memory offset: {second}",
                                         line_num, fname)
                offset += val

        return ('mem', base, offset)
