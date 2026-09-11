"""CIN 高级语言编译器。

将 CIN 源码 (类 C 语法: 函数/struct/多维数组/字符串/浮点/控制流)
编译为 UCPU 字节码 IR。

语法扩展 (2026):
  - 语句: break/continue, do-while, switch/case/default (case 常量表达式)
  - 表达式: ?: 三目 (短路), ++/-- 前缀与后缀, 复合赋值 += -= *= /= %=
  - 字面量/类型: 0x/0b/0o 进制与 u/L/f 后缀、数字下划线、字符字面量 'a';
    char/short/long/unsigned 类型别名 (64 位槽模型, 与 int 同宽)
  - 内建: int_to_str/itoa, float_to_str/ftoa, bool_to_str

运行时约定:
  - 所有值均为 64 位槽: int 为有符号整数; float 为 float64 位模式;
    bool 为 0/1; string/struct/数组为内存指针。
  - 浮点运算经 SYS 宿主调用 (FADD/FSUB/...), 数学函数同理。
  - 栈帧: [参数...][返回地址][局部变量/数组...] , SP 指向帧内最低地址。
  - struct 采用浅拷贝语义 (变量保存对象指针, 堆分配, 不回收)。
  - 定长数组就地存放 (数据段/栈帧); int[][] 为指向行指针数组的指针。
"""

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .console import Console
from .errors import CompilerError
from .isa import Syscall

# ---------------- 类型表示 ----------------
# 'int' / 'float' / 'bool' / 'string' / 'void'
# ('struct', Name)
# ('array', elem_type, size)       定长数组 (多维: elem 仍为 array)
# ('ptrarray', elem_type)          动态/参数数组 (int[] / int[][])

SCALARS = {'int', 'float', 'bool', 'string', 'void'}

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]


def _is_scalar(t) -> bool:
    return isinstance(t, str)


def _is_struct(t) -> bool:
    return isinstance(t, tuple) and t[0] == 'struct'


def _is_fixed_array(t) -> bool:
    return isinstance(t, tuple) and t[0] == 'array'


def _is_ptr_array(t) -> bool:
    return isinstance(t, tuple) and t[0] == 'ptrarray'


def _array_elem(t):
    return t[1]


def _type_slots(t) -> int:
    """类型占据的 qword 槽数。"""
    if _is_fixed_array(t):
        elem, size = t[1], t[2]
        return size * _type_slots(elem)
    return 1  # 标量/struct指针/ptrarray 均为 1 槽


# ==================== 词法分析 ====================

_KEYWORDS = {
    'struct', 'function', 'return', 'if', 'else', 'while', 'for', 'do',
    'switch', 'case', 'default', 'break', 'continue', 'true', 'false',
    'int', 'float', 'bool', 'string', 'void', 'char', 'short', 'long',
    'unsigned',
    'set', 'add', 'subtract', 'multiply', 'divide', 'increment', 'decrement',
}

# 声明起始的标量类型词 (含新支持的类型别名, 均映射到 64 位槽 int 模型)
BASE_TYPE_WORDS = ('int', 'float', 'bool', 'string', 'char', 'short', 'long',
                   'unsigned')

# 复合赋值: 词法 token -> C 风格运算符
_COMPOUND_ASSIGN = {
    'PLUSEQ': '+=', 'MINUSEQ': '-=', 'STAREQ': '*=',
    'SLASHEQ': '/=', 'PERCENTEQ': '%=',
    'ANDEQ': '&=', 'OREQ': '|=', 'XOREQ': '^=',
    'SHLEQ': '<<=', 'SHREQ': '>>=',
}

_SINGLE_OPS = {
    '{': 'LBRACE', '}': 'RBRACE', '(': 'LPAREN', ')': 'RPAREN',
    '[': 'LBRACKET', ']': 'RBRACKET', ',': 'COMMA', ';': 'SEMI',
    '+': 'PLUS', '-': 'MINUS', '*': 'STAR', '/': 'SLASH', '%': 'PERCENT',
    '=': 'ASSIGN', '<': 'LT', '>': 'GT', '!': 'BANG', '.': 'DOT',
    '?': 'QUESTION', ':': 'COLON',
    '&': 'AMP', '|': 'PIPE', '^': 'CARET', '~': 'TILDE',
}

# 复合赋值运算符 -> 底层运算符
_COMPOUND_TO_BASE = {
    '+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%',
    '&=': '&', '|=': '|', '^=': '^', '<<=': '<<', '>>=': '>>',
}


@dataclass
class Token:
    kind: str
    value: Any
    line: int
    filename: Optional[str] = None


def tokenize(source: str,
             origin: Optional[List[Optional[Tuple[str, int]]]] = None
             ) -> List[Token]:
    tokens: List[Token] = []
    i, line, n = 0, 1, len(source)

    def at_loc(ln: int) -> str:
        if origin and 0 < ln < len(origin):
            ent = origin[ln]
            if ent is not None:
                f, l = ent
                return f"{f}:{l}"
        return f"line {ln}"

    def skip_comment_block():
        nonlocal i, line
        i += 2
        while i < n - 1 and not (source[i] == '*' and source[i + 1] == '/'):
            if source[i] == '\n':
                line += 1
            i += 1
        i += 2

    while i < n:
        c = source[i]
        if c == '\n':
            tokens.append(Token('NL', '\n', line))
            line += 1
            i += 1
        elif c in ' \t\r':
            i += 1
        elif c == '/' and i + 1 < n and source[i + 1] == '/':
            while i < n and source[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and source[i + 1] == '*':
            skip_comment_block()
        elif c == '"':
            start_line = line
            i += 1
            buf = []
            while i < n and source[i] != '"':
                if source[i] == '\\' and i + 1 < n:
                    esc = source[i + 1]
                    buf.append({'n': '\n', 't': '\t', 'r': '\r',
                                '"': '"', '\\': '\\', '0': '\0'}.get(esc, esc))
                    i += 2
                else:
                    if source[i] == '\n':
                        line += 1
                    buf.append(source[i])
                    i += 1
            i += 1
            tokens.append(Token('STRING', ''.join(buf), start_line))
        elif c.isdigit() or (c == '.' and i + 1 < n and source[i + 1].isdigit()):
            start = i
            is_float = False
            if c == '0' and i + 1 < n and source[i + 1] in 'xXbBoO':
                # 进制前缀: 0x/0X 16, 0b/0B 2, 0o/0O 8
                base = {'x': 16, 'X': 16, 'b': 2, 'B': 2,
                        'o': 8, 'O': 8}[source[i + 1]]
                i += 2
                dstart = i
                allowed = ('0123456789abcdefABCDEF_' if base == 16 else
                           ('01_' if base == 2 else '01234567_'))
                while i < n and source[i] in allowed:
                    i += 1
                if i == dstart or not any(ch != '_' for ch in
                                          source[dstart:i]):
                    raise CompilerError(f"Malformed numeric literal at "
                                        f"{at_loc(line)}")
                # 后缀 (u/U/l/L, f/F): 64 位槽模型忽略宽度差异
                while i < n and source[i] in 'uUlL':
                    i += 1
                if i < n and source[i] in 'fF':
                    is_float = True
                    i += 1
                text = source[start:i]
                norm = '0' + text[1].lower() + text[2:]
                value = int(norm.replace('_', ''), 0)
            else:
                while i < n and (source[i].isdigit() or source[i] == '.'
                                 or source[i] == '_'):
                    if source[i] == '.':
                        is_float = True
                    i += 1
                if i < n and source[i] in 'eE':
                    is_float = True
                    i += 1
                    if i < n and source[i] in '+-':
                        i += 1
                    while i < n and (source[i].isdigit() or source[i] == '_'):
                        i += 1
                while i < n and source[i] in 'uUlL':
                    i += 1
                if i < n and source[i] in 'fF':
                    is_float = True
                    i += 1
                text = source[start:i].replace('_', '')
                value = float(text) if is_float else int(text)
            tokens.append(Token('FLOAT', float(value), line) if is_float
                          else Token('NUMBER', int(value), line))
        elif c.isalpha() or c == '_':
            start = i
            while i < n and (source[i].isalnum() or source[i] == '_'):
                i += 1
            word = source[start:i]
            tokens.append(Token('IDENT', word, line))
        elif c == '-' and i + 1 < n and source[i + 1] == '>':
            tokens.append(Token('ARROW', '->', line))
            i += 2
        elif c == "'":                       # 字符字面量: 'a' '\n' '\'' ...
            start_line = line
            i += 1
            if i >= n:
                raise CompilerError(f"Unterminated char literal at "
                                    f"{at_loc(start_line)}")
            if source[i] == '\\' and i + 1 < n:
                esc = source[i + 1]
                val = {'n': 10, 't': 9, 'r': 13, '0': 0, 'a': 7, 'b': 8,
                       'f': 12, 'v': 11, "'": 39, '"': 34,
                       '\\': 92}.get(esc, ord(esc))
                i += 2
            else:
                val = ord(source[i])
                i += 1
            if i >= n or source[i] != "'":
                raise CompilerError(f"Unterminated char literal at "
                                    f"{at_loc(start_line)}")
            i += 1
            tokens.append(Token('NUMBER', val, line))
        elif c in ('+', '-', '*', '/', '%') and i + 1 < n and source[i + 1] == '=':
            kind = {'+': 'PLUSEQ', '-': 'MINUSEQ', '*': 'STAREQ',
                    '/': 'SLASHEQ', '%': 'PERCENTEQ'}[c]
            tokens.append(Token(kind, c + '=', line))
            i += 2
        elif c in ('&', '|', '^') and i + 1 < n and source[i + 1] == '=':
            kind = {'&': 'ANDEQ', '|': 'OREQ', '^': 'XOREQ'}[c]
            tokens.append(Token(kind, c + '=', line))
            i += 2
        elif c in ('<', '>') and i + 2 < n and source[i + 1] == c \
                and source[i + 2] == '=':
            tokens.append(Token('SHLEQ' if c == '<' else 'SHREQ',
                                c + c + '=', line))
            i += 3
        elif c in ('<', '>') and i + 1 < n and source[i + 1] == c:
            tokens.append(Token('SHL' if c == '<' else 'SHR', c + c, line))
            i += 2
        elif c == '+' and i + 1 < n and source[i + 1] == '+':
            tokens.append(Token('INC', '++', line))
            i += 2
        elif c == '-' and i + 1 < n and source[i + 1] == '-':
            tokens.append(Token('DEC', '--', line))
            i += 2
        elif c == '=' and i + 1 < n and source[i + 1] == '=':
            tokens.append(Token('EQ', '==', line)); i += 2
        elif c == '!' and i + 1 < n and source[i + 1] == '=':
            tokens.append(Token('NEQ', '!=', line)); i += 2
        elif c == '<' and i + 1 < n and source[i + 1] == '=':
            tokens.append(Token('LE', '<=', line)); i += 2
        elif c == '>' and i + 1 < n and source[i + 1] == '=':
            tokens.append(Token('GE', '>=', line)); i += 2
        elif c == '&' and i + 1 < n and source[i + 1] == '&':
            tokens.append(Token('AND', '&&', line)); i += 2
        elif c == '|' and i + 1 < n and source[i + 1] == '|':
            tokens.append(Token('OR', '||', line)); i += 2
        elif c in _SINGLE_OPS:
            tokens.append(Token(_SINGLE_OPS[c], c, line)); i += 1
        else:
            raise CompilerError(f"Unexpected character {c!r} at {at_loc(line)}")

    # 行连接: 圆/方括号深度内或行尾为运算符时 NL 无效;
    # 花括号 {} 块内的 NL 必须保留 (语句以换行终止)
    filtered: List[Token] = []
    depth = 0
    open_kw = {'LPAREN', 'LBRACKET'}
    close_kw = {'RPAREN', 'RBRACKET'}
    continue_ops = {'PLUS', 'MINUS', 'STAR', 'SLASH', 'PERCENT', 'ASSIGN',
                    'LT', 'GT', 'LE', 'GE', 'EQ', 'NEQ', 'AND', 'OR', 'COMMA',
                    'ARROW', 'DOT', 'PLUSEQ', 'MINUSEQ', 'STAREQ', 'SLASHEQ',
                    'PERCENTEQ', 'INC', 'DEC',
                    'AMP', 'PIPE', 'CARET', 'TILDE', 'SHL', 'SHR',
                    'ANDEQ', 'OREQ', 'XOREQ', 'SHLEQ', 'SHREQ'}
    for idx, tok in enumerate(tokens):
        if tok.kind in open_kw:
            depth += 1
        elif tok.kind in close_kw:
            depth = max(0, depth - 1)
        if tok.kind == 'NL':
            if depth > 0:
                continue
            prev = filtered[-1] if filtered else None
            if prev is not None and prev.kind in continue_ops:
                continue
            if filtered and filtered[-1].kind == 'NL':
                continue
        filtered.append(tok)
    filtered.append(Token('EOF', None, line))
    return filtered


# ==================== import 预处理 (B2) ====================
# import "path.cin" 仅在文件顶部/列首识别; 相对源文件目录或仓库 lib/ 目录解析;
# 同一文件每个编译仅包含一次, 循环引用报错。

_IMPORT_RE = re.compile(r'^import\s+["\']([^"\']+)["\']\s*;?\s*$')

_UCPU_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_import(name: str, source_dir: str, lib_dir: str) -> str:
    candidates = [os.path.join(source_dir, name),
                  os.path.join(lib_dir, name) if lib_dir else '',
                  os.path.join(_UCPU_ROOT, name)]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    raise CompilerError(f"Import file not found: {name!r} (searched "
                        f"{source_dir}, {lib_dir}, {_UCPU_ROOT})")


def _collect_module_lines(path: str, loaded: set, active: set,
                          out: List[Tuple[str, str]]) -> None:
    """深度优先收集 (源文件, 文本行) 序列 (import 处内联展开)。

    防重复: 每个 realpath 每编译一次仅包含一次; 循环引用报错。
    """
    real = os.path.realpath(os.path.abspath(path))
    if real in active:
        raise CompilerError(f"Circular import: {path} ({real})")
    if real in loaded:
        return
    active.add(real)
    with open(real, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')
    for line in lines:
        m = _IMPORT_RE.match(line)
        if m:
            target = _resolve_import(
                m.group(1), os.path.dirname(real),
                os.path.join(_UCPU_ROOT, 'lib'))
            _collect_module_lines(target, loaded, active, out)
            continue
        out.append((real, line))
    active.discard(real)
    loaded.add(real)


def load_program_source_mapped(path: str) -> Tuple[str, List[Optional[Tuple[str, int]]]]:
    """读取 CIN 并展开 import, 返回 (文本, 行来源表)。

    origin[merged_line] = (源文件 realpath, 源文件内行号); origin[0] = None。
    """
    real = os.path.realpath(os.path.abspath(path))
    out: List[Tuple[str, str]] = []
    _collect_module_lines(real, set(), set(), out)
    counters: Dict[str, int] = {}
    origin: List[Optional[Tuple[str, int]]] = [None]
    for fname, _line in out:
        counters[fname] = counters.get(fname, 0) + 1
        origin.append((fname, counters[fname]))
    text = '\n'.join(line for _f, line in out)
    return text, origin


def load_program_source(path: str) -> str:
    """读取 CIN 文件并展开 import (返回可直接 tokenize 的源码文本)。"""
    text, _origin = load_program_source_mapped(path)
    return text


def _remap_tokens(tokens: List[Token], origin: List[Optional[Tuple[str, int]]]
                  ) -> None:
    """把拼合文本中的 token 行号重定位到源文件 (file, line)。"""
    for tok in tokens:
        if 0 < tok.line < len(origin):
            ent = origin[tok.line]
            if ent is not None:
                tok.filename, tok.line = ent[0], ent[1]


# ==================== 语法分析 ====================

@dataclass
class StructDef:
    name: str
    fields: List[Tuple[str, Any]] = field(default_factory=list)   # (name, type)
    offsets: Dict[str, int] = field(default_factory=dict)         # qword 偏移
    size_slots: int = 0


@dataclass
class FuncDef:
    name: str
    params: List[Tuple[str, Any]]
    ret_type: Any
    body: list
    line: int


@dataclass
class GlobalVar:
    name: str
    vtype: Any
    addr: int
    init: Optional[list] = None        # AST 表达式
    array_lit: Optional[list] = None   # 数组字面量行


class Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.pos = 0

    def peek(self, k: int = 0) -> Token:
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def next(self) -> Token:
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def accept(self, kind: str) -> Optional[Token]:
        if self.peek().kind == kind:
            return self.next()
        return None

    def expect(self, kind: str) -> Token:
        t = self.peek()
        if t.kind != kind:
            raise CompilerError(f"Expected {kind} but got {t.kind} ({t.value!r}) "
                                f"at {self._loc(t)}")
        return self.next()

    def skip_nl(self) -> None:
        while self.accept('NL'):
            pass

    def _loc(self, tok: Optional[Token] = None) -> str:
        """token 的 'file:line' 定位 (import 展开后为真实源文件)。"""
        t = tok if tok is not None else self.peek()
        return f"{t.filename or '<cin>'}:{t.line}"

    # ---------------- 类型 ----------------

    def parse_type(self) -> Any:
        t = self.expect('IDENT')
        base = t.value
        if base == 'unsigned':
            # unsigned [char|short|int|long]; 缺省为 int
            nxt = self.peek()
            if nxt.kind == 'IDENT' and nxt.value in ('char', 'short', 'int', 'long'):
                self.next()
            vtype = 'int'
        elif base in ('int', 'float', 'bool', 'string', 'void', 'char', 'short',
                      'long'):
            # char/short/long 为整数别名 (64 位槽模型, 与 int 同宽)
            vtype = 'int' if base in ('char', 'short', 'long') else base
        else:
            vtype = ('struct', base)
        # int[] / int[][] 形式 (后缀在类型名上)
        while self.peek().kind == 'LBRACKET' and self.peek(1).kind == 'RBRACKET':
            self.next(); self.next()
            vtype = ('ptrarray', vtype)
        return vtype

    def _is_decl_start(self) -> bool:
        """当前是否处于类型声明起始 (基础类型 / unsigned / struct 类型名)。"""
        t = self.peek()
        if t.kind != 'IDENT':
            return False
        v = t.value
        if v in BASE_TYPE_WORDS:
            return True
        # struct 类型名 (裸标识符) 后跟另一个标识符 = 声明
        return v not in _KEYWORDS and self.peek(1).kind == 'IDENT'

    def parse_dims(self) -> List[int]:
        dims = []
        while self.peek().kind == 'LBRACKET':
            self.next()
            num = self.accept('NUMBER')
            self.expect('RBRACKET')
            dims.append(num.value if num else 0)
        return dims

    # ---------------- 程序 ----------------

    def parse_program(self):
        structs: Dict[str, StructDef] = {}
        globals_: List[GlobalVar] = []
        functions: Dict[str, FuncDef] = {}

        self.skip_nl()
        while self.peek().kind != 'EOF':
            if self.peek().kind == 'IDENT' and self.peek().value == 'struct':
                self._parse_struct(structs)
            elif self.peek().kind == 'IDENT' and self.peek().value == 'function':
                f = self._parse_function()
                functions[f.name] = f
            else:
                self._parse_global(globals_)
            self.skip_nl()
        return structs, globals_, functions

    def _parse_struct(self, structs: Dict[str, StructDef]) -> None:
        self.next()  # struct
        name = self.expect('IDENT').value
        self.expect('LBRACE')
        self.skip_nl()
        sd = StructDef(name=name)
        off = 0
        while self.peek().kind != 'RBRACE':
            ftype = self.parse_type()
            fname = self.expect('IDENT').value
            dims = self.parse_dims()
            t = ftype
            for d in reversed(dims):
                t = ('array', t, d) if d else ('ptrarray', t)
            sd.fields.append((fname, t))
            sd.offsets[fname] = off
            off += _type_slots(t)
            self.accept('SEMI')
            self.skip_nl()
        self.expect('RBRACE')
        sd.size_slots = off
        structs[name] = sd
        self.accept('SEMI')

    def _parse_function(self) -> FuncDef:
        line = self.next().line  # function
        name = self.expect('IDENT').value
        self.expect('LPAREN')
        params: List[Tuple[str, Any]] = []
        while self.peek().kind != 'RPAREN':
            ptype = self.parse_type()
            pname = self.expect('IDENT').value
            dims = self.parse_dims()
            for d in reversed(dims):
                ptype = ('array', ptype, d) if d else ('ptrarray', ptype)
            params.append((pname, ptype))
            if not self.accept('COMMA'):
                break
        self.expect('RPAREN')
        ret_type: Any = 'void'
        if self.accept('ARROW'):
            ret_type = self.parse_type()
        self.expect('LBRACE')
        body = self.parse_block_body(RBRACE_TOKENS)
        self.expect('RBRACE')
        return FuncDef(name, params, ret_type, body, line)

    def _parse_global(self, globals_: List[GlobalVar]) -> None:
        vtype = self.parse_type()
        while True:
            name = self.expect('IDENT').value
            dims = self.parse_dims()
            t = vtype
            for d in reversed(dims):
                t = ('array', t, d) if d else ('ptrarray', t)
            gv = GlobalVar(name=name, vtype=t, addr=0)
            if self.accept('ASSIGN'):
                if self.peek().kind == 'LBRACE':
                    gv.array_lit = self.parse_array_literal()
                else:
                    gv.init = self.parse_expr()
            globals_.append(gv)
            if not self.accept('COMMA'):
                break
        self.accept('SEMI')

    def parse_array_literal(self) -> list:
        self.expect('LBRACE')
        rows: list = []
        current: list = []
        while self.peek().kind != 'RBRACE':
            if self.accept('LBRACE'):
                inner = []
                while self.peek().kind != 'RBRACE':
                    inner.append(self.parse_expr())
                    if not self.accept('COMMA'):
                        break
                self.expect('RBRACE')
                rows.append(inner)
            else:
                current.append(self.parse_expr())
            if not self.accept('COMMA'):
                break
        self.expect('RBRACE')
        return rows if rows else current

    # ---------------- 语句 ----------------

    def parse_block_body(self, _unused=None) -> list:
        stmts = []
        self.skip_nl()
        while self.peek().kind not in ('RBRACE', 'EOF'):
            stmts.append(self.parse_stmt())
            self.skip_nl()
        return stmts

    def parse_stmt(self):
        t = self.peek()
        if t.kind == 'LBRACE':
            self.next()
            body = self.parse_block_body()
            self.expect('RBRACE')
            return ('block', body)
        if t.kind == 'IDENT' and t.value == 'assert':
            return self.parse_assert()
        if t.kind == 'IDENT' and t.value == 'return':
            self.next()
            if self.peek().kind in ('NL', 'SEMI', 'RBRACE'):
                expr = None
            else:
                expr = self.parse_expr()
            self.accept('SEMI')
            return ('return', expr)
        if t.kind == 'IDENT' and t.value == 'if':
            return self.parse_if()
        if t.kind == 'IDENT' and t.value == 'while':
            self.next()
            cond = self.parse_paren_expr()
            body = self.parse_stmt()
            return ('while', cond, body)
        if t.kind == 'IDENT' and t.value == 'for':
            return self.parse_for()
        if t.kind == 'IDENT' and t.value == 'do':
            return self.parse_do()
        if t.kind == 'IDENT' and t.value == 'switch':
            return self.parse_switch()
        if t.kind == 'IDENT' and t.value == 'break':
            self.next(); self.accept('SEMI')
            return ('break',)
        if t.kind == 'IDENT' and t.value == 'continue':
            self.next(); self.accept('SEMI')
            return ('continue',)
        if self._is_decl_start():
            return self.parse_decl()
        if t.kind == 'IDENT' and t.value in ('set', 'add', 'subtract', 'multiply',
                                             'divide', 'increment', 'decrement'):
            return self.parse_cpu_stmt()
        # 表达式语句 / 赋值
        expr = self.parse_assign()
        self.accept('SEMI')
        return ('expr', expr)

    def parse_assign(self):
        target = self.parse_expr()
        t = self.peek().kind
        if t == 'ASSIGN':
            self.next()
            value = self.parse_assign()
            return ('binop', '=', target, value)
        if t in _COMPOUND_ASSIGN:
            self.next()
            value = self.parse_assign()
            return ('binop', _COMPOUND_ASSIGN[t], target, value)
        return target

    def parse_do(self):
        self.next()  # do
        body = self.parse_stmt()
        self.skip_nl()
        if not (self.peek().kind == 'IDENT' and self.peek().value == 'while'):
            raise CompilerError("Expected 'while' after do body at "
                                f"{self._loc()}")
        self.next()
        cond = self.parse_paren_expr()
        self.accept('SEMI')
        return ('dowhile', body, cond)

    def parse_switch(self):
        line = self.peek().line
        self.next()  # switch
        cond = self.parse_paren_expr()
        self.expect('LBRACE')
        self.skip_nl()
        # branches: [('case', const_expr|None, [stmt,...])]  (None = default)
        branches = []
        cur: Optional[Tuple[Any, list]] = None
        while self.peek().kind not in ('RBRACE', 'EOF'):
            self.skip_nl()
            t = self.peek()
            if t.kind == 'IDENT' and t.value == 'case':
                if cur is not None:
                    branches.append(cur)
                self.next()
                const = self.parse_expr()
                self.expect('COLON')
                cur = ('case', const, [])
            elif t.kind == 'IDENT' and t.value == 'default':
                if cur is not None:
                    branches.append(cur)
                self.next()
                self.expect('COLON')
                cur = ('case', None, [])
            else:
                if cur is None:
                    raise CompilerError(
                        "Statement before first case in switch at "
                        f"{self._loc(t)}")
                cur[2].append(self.parse_stmt())
            self.skip_nl()
        if cur is not None:
            branches.append(cur)
        self.expect('RBRACE')
        if not branches:
            raise CompilerError(f"Empty switch at {self._loc()}")
        return ('switch', cond, branches)

    def parse_assert(self):
        tok = self.peek()
        self.next()  # assert
        self.expect('LPAREN')
        cond = self.parse_expr()
        msg = None
        if self.accept('COMMA'):
            msg = self.parse_expr()
        self.expect('RPAREN')
        self.accept('SEMI')
        return ('assert', cond, msg, tok.line, tok.filename)

    def parse_if(self):
        self.next()
        cond = self.parse_paren_expr()
        then_body = self.parse_stmt()
        else_body = None
        self.skip_nl()
        if self.peek().kind == 'IDENT' and self.peek().value == 'else':
            self.next()
            self.skip_nl()
            else_body = self.parse_stmt()
        return ('if', cond, then_body, else_body)

    def parse_for(self):
        self.next()  # for
        self.expect('LPAREN')
        # init
        init = None
        if self.peek().kind != 'SEMI':
            if self._is_decl_start():
                init = self.parse_decl(no_semi=True)
            else:
                init = ('expr', self.parse_expr())
        self.expect('SEMI')
        cond = None
        if self.peek().kind != 'SEMI':
            cond = self.parse_expr()
        self.expect('SEMI')
        update = None
        if self.peek().kind != 'RPAREN':
            update = ('expr', self.parse_assign())
        self.expect('RPAREN')
        body = self.parse_stmt()
        return ('for', init, cond, update, body)

    def parse_decl(self, no_semi: bool = False):
        vtype = self.parse_type()
        decls = []
        while True:
            name = self.expect('IDENT').value
            dims = self.parse_dims()
            t = vtype
            for d in reversed(dims):
                t = ('array', t, d) if d else ('ptrarray', t)
            init = None
            array_lit = None
            if self.accept('ASSIGN'):
                if self.peek().kind == 'LBRACE':
                    array_lit = self.parse_array_literal()
                else:
                    init = self.parse_expr()
            decls.append((name, t, init, array_lit))
            if not self.accept('COMMA'):
                break
        if not no_semi:
            self.accept('SEMI')
        return ('decl', decls)

    def parse_cpu_stmt(self):
        op = self.next().value
        operands = []
        while self.peek().kind not in ('NL', 'SEMI', 'EOF'):
            tok = self.next()
            operands.append((tok.kind, tok.value))
        self.accept('SEMI')
        return ('cpu', op, operands)

    def parse_paren_expr(self):
        self.expect('LPAREN')
        e = self.parse_expr()
        self.expect('RPAREN')
        return e

    # ---------------- 表达式 ----------------

    def parse_expr(self):
        e = self.parse_or()
        if self.accept('QUESTION'):
            a = self.parse_assign()
            self.expect('COLON')
            b = self.parse_assign()
            return ('cond', e, a, b)
        return e

    def parse_or(self):
        left = self.parse_and()
        while self.peek().kind == 'OR':
            self.next()
            right = self.parse_and()
            left = ('binop', '||', left, right)
        return left

    def parse_and(self):
        left = self.parse_bit_or()
        while self.peek().kind == 'AND':
            self.next()
            right = self.parse_bit_or()
            left = ('binop', '&&', left, right)
        return left

    def parse_bit_or(self):
        left = self.parse_bit_xor()
        while self.peek().kind == 'PIPE':
            self.next()
            right = self.parse_bit_xor()
            left = ('binop', '|', left, right)
        return left

    def parse_bit_xor(self):
        left = self.parse_bit_and()
        while self.peek().kind == 'CARET':
            self.next()
            right = self.parse_bit_and()
            left = ('binop', '^', left, right)
        return left

    def parse_bit_and(self):
        left = self.parse_equality()
        while self.peek().kind == 'AMP':
            self.next()
            right = self.parse_equality()
            left = ('binop', '&', left, right)
        return left

    def parse_equality(self):
        left = self.parse_relational()
        while self.peek().kind in ('EQ', 'NEQ'):
            op = '==' if self.next().kind == 'EQ' else '!='
            right = self.parse_relational()
            left = ('binop', op, left, right)
        return left

    def parse_relational(self):
        left = self.parse_shift()
        while self.peek().kind in ('LT', 'GT', 'LE', 'GE'):
            tok = self.next()
            op = {'LT': '<', 'GT': '>', 'LE': '<=', 'GE': '>='}[tok.kind]
            right = self.parse_shift()
            left = ('binop', op, left, right)
        return left

    def parse_shift(self):
        left = self.parse_additive()
        while self.peek().kind in ('SHL', 'SHR'):
            op = '<<' if self.next().kind == 'SHL' else '>>'
            right = self.parse_additive()
            left = ('binop', op, left, right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.peek().kind in ('PLUS', 'MINUS'):
            op = '+' if self.next().kind == 'PLUS' else '-'
            right = self.parse_multiplicative()
            left = ('binop', op, left, right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.peek().kind in ('STAR', 'SLASH', 'PERCENT'):
            tok = self.next()
            op = {'STAR': '*', 'SLASH': '/', 'PERCENT': '%'}[tok.kind]
            right = self.parse_unary()
            left = ('binop', op, left, right)
        return left

    def parse_unary(self):
        if self.peek().kind == 'BANG':
            self.next()
            return ('not', self.parse_unary())
        if self.peek().kind == 'TILDE':
            self.next()
            return ('bitnot', self.parse_unary())
        if self.peek().kind == 'MINUS':
            self.next()
            return ('neg', self.parse_unary())
        if self.peek().kind == 'INC':
            self.next()
            return ('preinc', self.parse_unary())
        if self.peek().kind == 'DEC':
            self.next()
            return ('predec', self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            if self.peek().kind == 'LBRACKET':
                self.next()
                idx = self.parse_expr()
                self.expect('RBRACKET')
                e = ('index', e, idx)
            elif self.peek().kind == 'DOT':
                self.next()
                name = self.expect('IDENT').value
                e = ('member', e, name)
            elif self.peek().kind == 'INC':
                self.next()
                e = ('postinc', e)
            elif self.peek().kind == 'DEC':
                self.next()
                e = ('postdec', e)
            else:
                break
        return e

    def parse_primary(self):
        t = self.peek()
        if t.kind == 'NUMBER':
            self.next()
            return ('num', t.value, False)
        if t.kind == 'FLOAT':
            self.next()
            return ('num', t.value, True)
        if t.kind == 'STRING':
            self.next()
            return ('str', t.value)
        if t.kind == 'IDENT' and t.value == 'true':
            self.next()
            return ('bool', True)
        if t.kind == 'IDENT' and t.value == 'false':
            self.next()
            return ('bool', False)
        if t.kind == 'LPAREN':
            return self.parse_paren_expr()
        if t.kind == 'IDENT':
            name = self.next().value
            if self.peek().kind == 'LPAREN':
                self.next()
                args = []
                while self.peek().kind != 'RPAREN':
                    args.append(self.parse_expr())
                    if not self.accept('COMMA'):
                        break
                self.expect('RPAREN')
                return ('call', name, args)
            return ('var', name)
        raise CompilerError(f"Unexpected token {t.kind} ({t.value!r}) "
                            f"at {self._loc(t)}")


RBRACE_TOKENS = None


# ==================== 代码生成 ====================

class CompileResult:
    def __init__(self):
        self.instructions: List[Instruction] = []
        self.labels: Dict[str, int] = {}
        self.data_labels: Dict[str, int] = {}
        self.data_writes: List[Tuple[int, bytes]] = []


class CINCompiler:
    def __init__(self, console: Optional[Console] = None, logger=None):
        self.console = console or Console()
        self.logger = logger

    def compile(self, filename: str, bounds_check: bool = False) -> CompileResult:
        source, origin = load_program_source_mapped(filename)
        return self.compile_source(source, filename,
                                   bounds_check=bounds_check, origin=origin)

    def compile_source(self, source: str, filename: str = '<cin>',
                       bounds_check: bool = False,
                       origin: Optional[List[Optional[Tuple[str, int]]]] = None
                       ) -> CompileResult:
        dbg = self.logger.debug if (self.logger and self.logger.is_debug) \
            else (lambda msg: None)
        tokens = tokenize(source, origin)
        if origin:
            _remap_tokens(tokens, origin)
        dbg(f"CIN tokenize: {len(tokens)} tokens")
        parser = Parser(tokens)
        structs, globals_, functions = parser.parse_program()
        dbg(f"CIN parse: {len(structs)} structs, {len(globals_)} globals, "
            f"{len(functions)} functions "
            f"({', '.join(list(functions)[:8])}"
            f"{', ...' if len(functions) > 8 else ''})")
        for name, fn in functions.items():
            dbg(f"CIN function {name}: {len(fn.params)} params")

        gen = CodeGen(structs, functions, filename=filename,
                      bounds_check=bounds_check)
        gen.layout_globals(globals_)
        for name, (typ, addr, block) in gen.globals.items():
            dbg(f"CIN global '{name}': type={typ} addr=0x{addr:x} block={block}")
        result = gen.generate(globals_, functions)
        for name in functions:
            dbg(f"CIN codegen function '{name}' done")
        dbg(f"CIN codegen total: {len(result.instructions)} instructions, "
            f"{sum(len(d) for _, d in result.data_writes)} data bytes, "
            f"{len(result.labels)} labels, {len(result.data_writes)} data writes")
        return result


class CodeGen:
    def __init__(self, structs: Dict[str, StructDef],
                 functions: Dict[str, FuncDef], filename: str = '<cin>',
                 bounds_check: bool = False):
        self.structs = structs
        self.functions = functions
        self.res = CompileResult()
        self._filename = filename
        self._bounds = bounds_check

        self.data_ptr = 0
        self.heap_strings: Dict[str, int] = {}

        # 函数生成上下文
        self.func: Optional[FuncDef] = None
        self.locals: Dict[str, Tuple[Any, int, bool]] = {}  # name -> (type, off, is_block)
        self.frame_bytes = 0
        self.break_labels: List[str] = []
        self.continue_labels: List[str] = []
        self.label_counter = 0

        self.globals: Dict[str, Tuple[Any, int, bool]] = {}  # name -> (type, addr, is_block)

    # ---------------- 发射辅助 ----------------

    def emit(self, op: str, *args: Operand) -> int:
        self.res.instructions.append((op, list(args)))
        return len(self.res.instructions) - 1

    def label(self, name: str) -> None:
        self.res.labels[name] = len(self.res.instructions)

    def new_label(self, hint: str) -> str:
        self.label_counter += 1
        return f"_{hint}_{self.label_counter}"

    @staticmethod
    def reg(n: int) -> Operand:
        return ('reg', n)

    @staticmethod
    def imm(v: int) -> Operand:
        return ('imm', v & 0xFFFFFFFFFFFFFFFF)

    def lab(self, name: str) -> Operand:
        return ('label', name)

    # ---------------- 数据段 ----------------

    def _alloc_data(self, nbytes: int, align: int = 8) -> int:
        if align > 1:
            self.data_ptr = (self.data_ptr + align - 1) & ~(align - 1)
        addr = self.data_ptr
        self.data_ptr += nbytes
        return addr

    def _data_string(self, text: str) -> int:
        if text in self.heap_strings:
            return self.heap_strings[text]
        data = text.encode('utf-8') + b'\x00'
        addr = self._alloc_data(len(data), align=1)
        self.res.data_writes.append((addr, data))
        self.res.data_labels[f'str_{addr:x}'] = addr
        self.heap_strings[text] = addr
        return addr

    def _data_qword(self, value: int) -> int:
        addr = self._alloc_data(8)
        self.res.data_writes.append((addr, struct.pack('<Q', value & 0xFFFFFFFFFFFFFFFF)))
        return addr

    def layout_globals(self, globals_: List[GlobalVar]) -> None:
        for gv in globals_:
            t = gv.vtype
            if _is_fixed_array(t):
                addr = self._alloc_data(_type_slots(t) * 8)
                self.globals[gv.name] = (t, addr, True)
                gv.addr = addr
            else:
                addr = self._alloc_data(8)
                self.globals[gv.name] = (t, addr, False)
                gv.addr = addr
                if _is_struct(t):
                    sd = self.structs[t[1]]
                    block = self._alloc_data(sd.size_slots * 8)
                    self.res.data_writes.append(
                        (addr, struct.pack('<Q', block)))

    # ---------------- 初始化数据 ----------------

    def _const_value(self, node) -> Tuple[Any, int]:
        """编译期常量 (全局初始化用): 返回 (type, raw_bits/addr)。"""
        kind = node[0]
        if kind == 'num':
            if node[2]:
                return 'float', struct.unpack('<Q', struct.pack('<d', node[1]))[0]
            return 'int', node[1] & 0xFFFFFFFFFFFFFFFF
        if kind == 'bool':
            return 'bool', 1 if node[1] else 0
        if kind == 'str':
            return 'string', self._data_string(node[1])
        if kind == 'neg' and node[1][0] == 'num':
            inner = node[1]
            if inner[2]:
                return 'float', struct.unpack('<Q', struct.pack('<d', -inner[1]))[0]
            return 'int', (-inner[1]) & 0xFFFFFFFFFFFFFFFF
        if kind == 'bitnot':
            ctype, raw = self._const_value(node[1])
            if ctype not in ('int', 'bool'):
                raise CompilerError(f"Bitwise NOT requires integer, got: {ctype}")
            return 'int', (~raw) & 0xFFFFFFFFFFFFFFFF
        raise CompilerError(f"Non-constant global initializer: {kind}")

    def emit_globals_init(self, globals_: List[GlobalVar]) -> None:
        for gv in globals_:
            t, addr, is_block = self.globals[gv.name]
            if gv.init is not None:
                ctype, raw = self._const_value(gv.init)
                self.res.data_writes.append((addr, struct.pack('<Q', raw)))
            elif gv.array_lit is not None:
                rows = gv.array_lit
                if rows and isinstance(rows[0], list):
                    # 2D 字面量: 仅 ptrarray 有意义 (int[][] mat1 = ...)
                    # 全局 2D 定长数组按行写入
                    for i, row in enumerate(rows):
                        for j, elem in enumerate(row):
                            _, raw = self._const_value(elem)
                            self.res.data_writes.append(
                                (addr + (i * len(row) + j) * 8, struct.pack('<Q', raw)))
                else:
                    for i, elem in enumerate(rows):
                        _, raw = self._const_value(elem)
                        self.res.data_writes.append((addr + i * 8, struct.pack('<Q', raw)))

    # ---------------- 主生成 ----------------

    def generate(self, globals_: List[GlobalVar],
                 functions: Dict[str, FuncDef]) -> CompileResult:
        self.emit_globals_init(globals_)

        # 入口: CALL main; HALT
        self.emit('CALL', self.lab('main'))
        self.emit('HALT')

        for fname, fdef in functions.items():
            self.gen_function(fdef)

        return self.res

    # ---------------- 栈帧 ----------------

    def _prescan_locals(self, body: list) -> List[Tuple[str, Any, int]]:
        """收集函数内所有局部声明 (含 for 初始化), 返回 (name,type,slots)。"""
        found: List[Tuple[str, Any, int]] = []
        seen = set()

        def walk(stmts):
            for s in stmts:
                kind = s[0]
                if kind == 'decl':
                    for name, t, _init, _al in s[1]:
                        if name in seen:
                            continue
                        seen.add(name)
                        found.append((name, t, _type_slots(t)))
                elif kind == 'block':
                    walk(s[1])
                elif kind == 'if':
                    walk([s[2]])
                    if s[3]:
                        walk([s[3]])
                elif kind == 'while':
                    walk([s[2]])
                elif kind == 'dowhile':
                    walk([s[1]])
                elif kind == 'switch':
                    for _bk, _const, stmts in s[2]:
                        walk(stmts)
                elif kind == 'for':
                    if s[1] is not None and s[1][0] == 'decl':
                        for name, t, _i, _a in s[1][1]:
                            if name not in seen:
                                seen.add(name)
                                found.append((name, t, _type_slots(t)))
                    walk([s[4]])
        walk(body)
        return found

    def gen_function(self, f: FuncDef) -> None:
        self.func = f
        self.locals = {}
        self.break_labels = []
        self.continue_labels = []

        # 局部变量布局 (自 FP 向下分配):
        # 标量槽地址 = fp-(8+off); 数组块基址 = fp-(off+slots*8)
        # (元素从基址向高地址生长, 不与后续变量冲突)
        locals_info = self._prescan_locals(f.body)
        off = 0
        for name, t, slots in locals_info:
            self.locals[name] = (t, -(off + slots * 8), _is_fixed_array(t))
            off += slots * 8
        self.frame_bytes = off

        # 参数位置 (相对 FP): fp+16 为第一个压栈参数 (最左)
        # 帧布局: [参数...][返回地址(fp+8)][保存的FP(fp)][局部...]
        nargs = len(f.params)
        for k, (pname, ptype) in enumerate(f.params):
            poff = 16 + (nargs - 1 - k) * 8
            self.locals[pname] = (ptype, poff, False)

        self.label(f.name)
        # prologue: 保存调用方 FP, 建立帧指针, 分配局部空间
        self.emit('PUSH', self.reg(29))
        self.emit('MOV', self.reg(29), self.reg(32))   # FP = SP
        if self.frame_bytes:
            self.emit('ADDI', self.reg(0), self.reg(32), self.imm(-self.frame_bytes))
            self.emit('MOV', self.reg(32), self.reg(0))

        # struct 局部变量: 堆分配对象
        for name, (t, loff, is_block) in list(self.locals.items()):
            if _is_struct(t) and loff < 0:
                sd = self.structs[t[1]]
                self.emit('MOV', self.reg(0), self.imm(sd.size_slots * 8))
                self.emit('SYS', self.imm(Syscall.MALLOC))
                self.emit('MOV', self.reg(2), self.reg(0))  # 对象指针
                self._addr_local(loff)                       # x0 = &slot
                self.emit('SD', self.reg(2), ('mem', 0, 0))

        self.gen_stmts(f.body)

        # epilogue
        self._epilogue()

    def _epilogue(self) -> None:
        # 不得破坏 x0 (返回值)
        self.emit('MOV', self.reg(6), self.reg(29))   # SP = FP
        self.emit('MOV', self.reg(32), self.reg(6))
        self.emit('POP', self.reg(29))                # 恢复调用方 FP
        self.emit('RET')

    def _addr_local(self, off: int) -> None:
        """x0 = FP + off (局部 off 为负, 参数 off 为正)。"""
        if off == 0:
            self.emit('MOV', self.reg(0), self.reg(29))
        else:
            self.emit('ADDI', self.reg(0), self.reg(29), self.imm(off))

    def _addr_var(self, name: str) -> None:
        """x0 = 变量槽地址。"""
        if name in self.locals:
            _t, off, _is_block = self.locals[name]
            self._addr_local(off)
        elif name in self.globals:
            _t, addr, _is_block = self.globals[name]
            self.emit('MOV', self.reg(0), self.imm(addr))
        else:
            raise CompilerError(f"Undefined variable: {name}")

    def _var_type(self, name: str):
        if name in self.locals:
            return self.locals[name][0]
        if name in self.globals:
            return self.globals[name][0]
        return None

    # ---------------- 语句 ----------------

    def gen_stmts(self, stmts: list) -> None:
        for s in stmts:
            self.gen_stmt(s)

    def gen_stmt(self, s) -> None:
        kind = s[0]
        if kind == 'block':
            self.gen_stmts(s[1])
        elif kind == 'return':
            if s[1] is not None:
                t = self.gen_value(s[1])
                self._convert(t, self.func.ret_type)
            self._epilogue()
        elif kind == 'if':
            self.gen_if(s[1], s[2], s[3])
        elif kind == 'while':
            self.gen_while(s[1], s[2])
        elif kind == 'for':
            self.gen_for(s[1], s[2], s[3], s[4])
        elif kind == 'dowhile':
            self.gen_dowhile(s[1], s[2])
        elif kind == 'switch':
            self.gen_switch(s[1], s[2])
        elif kind == 'break':
            if not self.break_labels:
                raise CompilerError("break outside loop")
            self.emit('JMP', self.lab(self.break_labels[-1]))
        elif kind == 'continue':
            if not self.continue_labels:
                raise CompilerError("continue outside loop")
            self.emit('JMP', self.lab(self.continue_labels[-1]))
        elif kind == 'decl':
            self.gen_decl(s[1])
        elif kind == 'cpu':
            self.gen_cpu_stmt(s[1], s[2])
        elif kind == 'assert':
            self.gen_assert(s[1], s[2], s[3],
                            s[4] if len(s) > 4 else self._filename)
        elif kind == 'expr':
            self.gen_expr_stmt(s[1])

    def gen_if(self, cond, then_body, else_body) -> None:
        l_else = self.new_label('else')
        l_end = self.new_label('endif')
        target = l_else if else_body is not None else l_end
        self.gen_cond_jump_false(cond, target)
        self.gen_stmt(then_body)
        if else_body is not None:
            self.emit('JMP', self.lab(l_end))
            self.label(l_else)
            self.gen_stmt(else_body)
        self.label(l_end)

    def gen_while(self, cond, body) -> None:
        l_start = self.new_label('while')
        l_end = self.new_label('wend')
        self.label(l_start)
        self.gen_cond_jump_false(cond, l_end)
        self.break_labels.append(l_end)
        self.continue_labels.append(l_start)
        self.gen_stmt(body)
        self.emit('JMP', self.lab(l_start))
        self.break_labels.pop()
        self.continue_labels.pop()
        self.label(l_end)

    def gen_for(self, init, cond, update, body) -> None:
        if init is not None:
            self.gen_stmt(init)
        l_cond = self.new_label('forc')
        l_update = self.new_label('foru')
        l_end = self.new_label('fore')
        self.label(l_cond)
        if cond is not None:
            self.gen_cond_jump_false(cond, l_end)
        self.break_labels.append(l_end)
        self.continue_labels.append(l_update)
        self.gen_stmt(body)
        self.label(l_update)
        if update is not None:
            self.gen_stmt(update)
        self.emit('JMP', self.lab(l_cond))
        self.break_labels.pop()
        self.continue_labels.pop()
        self.label(l_end)

    def gen_dowhile(self, body, cond) -> None:
        l_body = self.new_label('dbody')
        l_cond = self.new_label('dcond')
        l_end = self.new_label('dend')
        self.label(l_body)
        self.break_labels.append(l_end)
        self.continue_labels.append(l_cond)
        self.gen_stmt(body)
        self.label(l_cond)
        self.gen_cond_jump_false(cond, l_end)
        self.emit('JMP', self.lab(l_body))
        self.break_labels.pop()
        self.continue_labels.pop()
        self.label(l_end)

    def gen_switch(self, cond, branches) -> None:
        """switch: 选择器压栈, CMP 链分派, case 体按文字顺序内联 (C 贯穿语义)。"""
        sel_t = self._expr_type(cond)
        if sel_t not in ('int', 'bool'):
            raise CompilerError(
                f"Switch expression must be integer, got: {sel_t}")
        l_end = self.new_label('swend')
        self.break_labels.append(l_end)
        self.gen_value(cond)
        self.emit('PUSH', self.reg(0))                 # [SP] = selector

        labels: List[Tuple[Optional[int], str]] = []
        default_lbl: Optional[str] = None
        for _kind, const, _stmts in branches:
            if const is None:
                lbl = self.new_label('swdef')
                default_lbl = lbl
                labels.append((None, lbl))
                continue
            ctype, raw = self._const_value(const)
            if ctype not in ('int', 'bool'):
                raise CompilerError(
                    f"case value must be an integer constant, got: {ctype}")
            labels.append((raw & 0xFFFFFFFFFFFFFFFF, self.new_label('swcase')))

        # 分派比较链
        for raw, lbl in labels:
            if raw is None:
                continue
            self.emit('LD', self.reg(0), ('mem', 32, 0))
            self.emit('MOV', self.reg(1), self.imm(raw))
            self.emit('CMP', self.reg(0), self.reg(1))
            self.emit('B', self.lab(lbl), ('cond', 'EQ'))
        self.emit('JMP', self.lab(default_lbl if default_lbl is not None
                                  else l_end))

        # case/default 体 (inline, 贯穿)
        for idx, (_kind, _const, stmts) in enumerate(branches):
            self.label(labels[idx][1])
            self.gen_stmts(stmts)

        self.label(l_end)
        self.break_labels.pop()
        self.emit('ADDI', self.reg(32), self.reg(32), self.imm(8))  # 丢 selector

    def gen_decl(self, decls) -> None:
        for name, t, init, array_lit in decls:
            if array_lit is not None:
                if array_lit and isinstance(array_lit[0], list):
                    self.gen_init_2d_literal(name, t, array_lit)
                else:
                    self.gen_init_1d_literal(name, t, array_lit)
                continue
            if init is not None:
                vt = self.gen_value(init)
                self._convert(vt, t)
                # x0 = value; 存到变量槽
                self.emit('MOV', self.reg(2), self.reg(0))
                self._addr_var(name)
                self.emit('SD', self.reg(2), ('mem', 0, 0))
            elif _is_ptr_array(t) and _is_ptr_array(_array_elem(t)):
                # int[][] result (无尺寸): 预分配 64 个行指针
                self.emit('MOV', self.reg(0), self.imm(64 * 8))
                self.emit('SYS', self.imm(Syscall.MALLOC))
                self.emit('MOV', self.reg(2), self.reg(0))
                self._addr_var(name)
                self.emit('SD', self.reg(2), ('mem', 0, 0))

    def gen_init_1d_literal(self, name: str, t, elems: list) -> None:
        # 定长 1D 数组: 就地写入 (每轮重新取基址, 避免地址累加)
        elem_t = _array_elem(t) if _is_fixed_array(t) else 'int'
        for i, elem in enumerate(elems):
            vt = self.gen_value(elem)
            self._convert(vt, elem_t)
            self.emit('MOV', self.reg(2), self.reg(0))  # value
            self._addr_var(name)                        # x0 = base
            if i:
                self.emit('ADDI', self.reg(0), self.reg(0), self.imm(i * 8))
            self.emit('SD', self.reg(2), ('mem', 0, 0))

    def gen_init_2d_literal(self, name: str, t, rows: list) -> None:
        # int[][] mat1 = {{...},{...}}: 堆分配行指针数组 + 每行
        nrows = len(rows)
        ncols = max(len(r) for r in rows)
        # 外层: nrows 个指针
        self.emit('MOV', self.reg(0), self.imm(nrows * 8))
        self.emit('SYS', self.imm(Syscall.MALLOC))
        self.emit('MOV', self.reg(4), self.reg(0))  # outer
        for i, row in enumerate(rows):
            self.emit('MOV', self.reg(0), self.imm(max(ncols, 1) * 8))
            self.emit('SYS', self.imm(Syscall.MALLOC))
            self.emit('MOV', self.reg(5), self.reg(0))  # row ptr
            # outer[i] = row ptr
            self.emit('MOV', self.reg(0), self.reg(4))
            self.emit('ADDI', self.reg(0), self.reg(0), self.imm(i * 8))
            self.emit('SD', self.reg(5), ('mem', 0, 0))
            for j, elem in enumerate(row):
                vt = self.gen_value(elem)
                self._convert(vt, 'int')
                self.emit('MOV', self.reg(2), self.reg(0))
                self.emit('MOV', self.reg(0), self.reg(5))
                if j:
                    self.emit('ADDI', self.reg(0), self.reg(0), self.imm(j * 8))
                self.emit('SD', self.reg(2), ('mem', 0, 0))
        # 保存 outer 到变量槽
        self.emit('MOV', self.reg(2), self.reg(4))
        self._addr_var(name)
        self.emit('SD', self.reg(2), ('mem', 0, 0))

    def gen_cpu_stmt(self, op: str, operands: list) -> None:
        def operand_value(operand):
            kind, val = operand
            if kind == 'NUMBER':
                return val
            if kind == 'IDENT':
                return ('var', val)
            raise CompilerError(f"Bad CPU-style operand: {operand}")

        if op in ('increment', 'decrement'):
            varname = operands[0][1]
            self._addr_var(varname)
            self.emit('LD', self.reg(0), ('mem', 0, 0))
            self.emit('INC' if op == 'increment' else 'DEC', self.reg(0))
            self.emit('MOV', self.reg(2), self.reg(0))
            self._addr_var(varname)
            self.emit('SD', self.reg(2), ('mem', 0, 0))
            return

        varname = operands[0][1]
        rhs = operand_value(operands[1]) if len(operands) > 1 else None

        if op == 'set':
            if isinstance(rhs, tuple):
                self.gen_value(rhs)
            else:
                self.emit('MOV', self.reg(0), self.imm(int(rhs)))
        else:
            # add/subtract/multiply/divide: var = var op rhs (整数)
            self._addr_var(varname)
            self.emit('LD', self.reg(0), ('mem', 0, 0))
            if isinstance(rhs, tuple):
                self.emit('MOV', self.reg(2), self.reg(0))
                self.gen_value(rhs)
                self.emit('MOV', self.reg(1), self.reg(0))
                self.emit('MOV', self.reg(0), self.reg(2))
            else:
                self.emit('MOV', self.reg(1), self.imm(int(rhs)))
            op_map = {'add': 'ADD', 'subtract': 'SUB',
                      'multiply': 'MUL', 'divide': 'DIV'}
            self.emit(op_map[op], self.reg(0), self.reg(1))
        self.emit('MOV', self.reg(2), self.reg(0))
        self._addr_var(varname)
        self.emit('SD', self.reg(2), ('mem', 0, 0))

    def gen_expr_stmt(self, node) -> None:
        # 赋值
        if node[0] == 'binop' and node[1] == '=':
            pass
        # parse 不产生 '=' binop; 赋值在 parse 层未处理, 这里检测 call/var 链
        self.gen_value(node)

    # ---------------- 运行时断言 / 中止 (A1) ----------------

    def _runtime_abort(self, text: str) -> None:
        """SYS ABORT: 加载静态消息指针并抛出运行时错误。"""
        addr = self._data_string(text)
        self.emit('MOV', self.reg(0), self.imm(addr))
        self.emit('SYS', self.imm(Syscall.ABORT))

    def gen_assert(self, cond, msg, line: int,
                   filename: Optional[str] = None) -> None:
        """assert(cond [, msg]) → 条件不成立时运行时中止 (定位: filename:line)。"""
        l_ok = self.new_label('assertok')
        self.gen_cond_jump_true(cond, l_ok)
        if msg is None:
            self._runtime_abort(
                f"{(filename or self._filename)}:{line}: assertion failed")
        else:
            # [prefix] + 消息 (消息支持任意表达式, 自动字符串化)
            prefix = self._data_string(
                f"[assert {(filename or self._filename)}:{line}] ")
            self.emit('MOV', self.reg(0), self.imm(prefix))
            self.emit('PUSH', self.reg(0))
            self._gen_string_value(msg)
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.STR_CONCAT))
            self.emit('SYS', self.imm(Syscall.ABORT))
        self.label(l_ok)

    # ---------------- 类型转换 ----------------

    def _convert(self, from_type, to_type) -> None:
        """将 x0 从 from_type 转换为 to_type (原地)。"""
        if to_type is None or from_type is None:
            return
        if from_type == to_type:
            return
        if from_type == 'int' and to_type == 'float':
            self.emit('SYS', self.imm(Syscall.ITOF))
        elif from_type == 'float' and to_type == 'int':
            self.emit('SYS', self.imm(Syscall.FTOI))
        elif from_type == 'bool' and to_type in ('int', 'float'):
            if to_type == 'float':
                self.emit('SYS', self.imm(Syscall.ITOF))
        elif from_type == 'int' and to_type == 'bool':
            pass
        # string/struct/array 指针无需转换

    # ---------------- 表达式 ----------------

    def gen_value(self, node) -> Any:
        """求值表达式到 x0, 返回 CIN 类型。"""
        kind = node[0]
        if kind == 'num':
            if node[2]:
                bits = struct.unpack('<Q', struct.pack('<d', float(node[1])))[0]
                self.emit('MOV', self.reg(0), self.imm(bits))
                return 'float'
            self.emit('MOV', self.reg(0), self.imm(int(node[1])))
            return 'int'
        if kind == 'bool':
            self.emit('MOV', self.reg(0), self.imm(1 if node[1] else 0))
            return 'bool'
        if kind == 'str':
            self.emit('MOV', self.reg(0), self.imm(self._data_string(node[1])))
            return 'string'
        if kind == 'var':
            return self._gen_var_value(node[1])
        if kind == 'member':
            return self._gen_member(node[1], node[2], lvalue=False)
        if kind == 'index':
            return self._gen_index(node[1], node[2], lvalue=False)
        if kind == 'call':
            return self._gen_call(node[1], node[2])
        if kind == 'neg':
            t = self.gen_value(node[1])
            self.emit('MOV', self.reg(1), self.reg(0))
            if t == 'float':
                # 浮点: 0.0 - x (x1 为 float64 位模式)
                self.emit('MOV', self.reg(0), self.imm(0))
                self.emit('SYS', self.imm(Syscall.ITOF))
                self.emit('SYS', self.imm(Syscall.FSUB))
            else:
                self.emit('MOV', self.reg(0), self.imm(0))
                self.emit('SUB', self.reg(0), self.reg(1))
            return t
        if kind == 'not':
            t = self.gen_value(node[1])
            self.emit('XORI', self.reg(0), self.reg(0), self.imm(1))
            return 'bool'
        if kind == 'bitnot':
            t = self.gen_value(node[1])
            if t in ('float', 'string'):
                raise CompilerError("Bitwise NOT '~' requires an integer operand")
            self.emit('MVN', self.reg(0), self.reg(0))
            return 'int'
        if kind in ('preinc', 'predec', 'postinc', 'postdec'):
            return self._gen_incdec(kind, node[1])
        if kind == 'cond':
            return self._gen_ternary(node[1], node[2], node[3])
        if kind == 'binop':
            return self._gen_binop(node[1], node[2], node[3])
        raise CompilerError(f"Cannot generate code for expression: {kind}")

    def _gen_var_value(self, name: str) -> Any:
        t = self._var_type(name)
        if t is None:
            raise CompilerError(f"Undefined variable: {name}")
        is_block = False
        if name in self.locals:
            is_block = self.locals[name][2]
        elif name in self.globals:
            is_block = self.globals[name][2]
        if is_block:
            # 定长数组: 值 = 块地址
            self._addr_var(name)
            return t
        self._addr_var(name)
        self.emit('LD', self.reg(0), ('mem', 0, 0))
        return t

    def _gen_lvalue_addr(self, node) -> None:
        """求值左值地址到 x0。"""
        kind = node[0]
        if kind == 'var':
            self._addr_var(node[1])
            return
        if kind == 'member':
            self._gen_member(node[1], node[2], lvalue=True)
            return
        if kind == 'index':
            self._gen_index(node[1], node[2], lvalue=True)
            return
        raise CompilerError(f"Invalid assignment target: {kind}")

    def _struct_field(self, struct_type, fname: str) -> Tuple[Any, int]:
        sd = self.structs[struct_type[1]]
        if fname not in sd.offsets:
            raise CompilerError(f"Struct {struct_type[1]} has no field {fname}")
        ftype = dict(sd.fields)[fname]
        return ftype, sd.offsets[fname]

    def _gen_member(self, obj_node, fname: str, lvalue: bool):
        obj_t = self.gen_value(obj_node)  # x0 = struct 指针
        if not _is_struct(obj_t):
            raise CompilerError(f"Member access on non-struct type: {obj_t}")
        ftype, foff = self._struct_field(obj_t, fname)
        if lvalue:
            if foff:
                self.emit('ADDI', self.reg(0), self.reg(0), self.imm(foff * 8))
            return None
        # 值
        if _is_fixed_array(ftype):
            # 内联数组: 值 = 字段地址, 类型退化为指针视图
            if foff:
                self.emit('ADDI', self.reg(0), self.reg(0), self.imm(foff * 8))
            return self._decay(ftype)
        self.emit('MOV', self.reg(1), self.reg(0))
        if foff:
            self.emit('ADDI', self.reg(1), self.reg(1), self.imm(foff * 8))
        self.emit('LD', self.reg(0), ('mem', 1, 0))
        return ftype

    @staticmethod
    def _decay(t):
        """定长数组作为指针值时的类型视图。"""
        if _is_fixed_array(t):
            elem = t[1]
            if _is_fixed_array(elem):
                return ('array', elem, t[2])  # 多维: 子数组仍定长
            return ('ptrarray', elem)
        return t

    def _gen_index(self, base_node, idx_node, lvalue: bool):
        base_t = self.gen_value(base_node)  # 定长数组=块地址; ptrarray=指针; string=ptr
        if base_t == 'string':
            if lvalue:
                raise CompilerError(
                    "Cannot assign to string element (strings are immutable)")
            self.emit('MOV', self.reg(3), self.reg(0))   # x3 = 字符串基址
            self.gen_value(idx_node)                    # x0 = index
            self.emit('ADD', self.reg(0), self.reg(3))  # x0 = base + i (字节偏移)
            self.emit('LB', self.reg(0), ('mem', 0, 0))  # 读 1 字节 (符号扩展)
            self.emit('ANDI', self.reg(0), self.reg(0), self.imm(0xFF))  # 0..255
            return 'int'
        if _is_fixed_array(base_t):
            elem_t = _array_elem(base_t)
        elif _is_ptr_array(base_t):
            elem_t = _array_elem(base_t)
        else:
            raise CompilerError(f"Indexing non-array type: {base_t}")

        # x0 = base pointer; 计算 elem 地址
        self.emit('MOV', self.reg(3), self.reg(0))  # x3 = base
        self.gen_value(idx_node)                    # x0 = index

        # A1 --bounds-check: 定长数组索引 0 <= i < size
        if self._bounds and _is_fixed_array(base_t):
            size = base_t[2]
            l_ge = self.new_label('bndok')
            self.emit('CMP', self.reg(0), self.imm(0))
            self.emit('B', self.lab(l_ge), ('cond', 'GE'))
            self._runtime_abort("bounds-check: negative array index")
            self.label(l_ge)
            l_lt = self.new_label('bndok')
            self.emit('CMP', self.reg(0), self.imm(size))
            self.emit('B', self.lab(l_lt), ('cond', 'LT'))
            self._runtime_abort(f"bounds-check: index >= length ({size})")
            self.label(l_lt)

        scale = _type_slots(elem_t) * 8
        self.emit('MOV', self.reg(1), self.imm(scale))
        self.emit('MUL', self.reg(0), self.reg(1))  # x0 = index * scale
        self.emit('ADD', self.reg(0), self.reg(3))  # x0 = elem 地址

        if lvalue:
            return None

        if _is_fixed_array(elem_t):
            return elem_t  # 子数组: 值 = 地址
        if _is_ptr_array(elem_t):
            # 行指针 (int[][] 的 a[i]): 若为空则延迟分配 64 元素行
            self.emit('MOV', self.reg(5), self.reg(0))   # x5 = elem 地址
            self.emit('LD', self.reg(1), ('mem', 5, 0))  # x1 = 行指针
            l_done = self.new_label('rowdone')
            self.emit('CMP', self.reg(1), self.imm(0))
            self.emit('B', self.lab(l_done), ('cond', 'NE'))
            self.emit('MOV', self.reg(0), self.imm(64 * 8))
            self.emit('SYS', self.imm(Syscall.MALLOC))
            self.emit('MOV', self.reg(1), self.reg(0))   # x1 = 新行指针
            self.emit('SD', self.reg(1), ('mem', 5, 0))  # 写回 elem 槽
            self.label(l_done)
            self.emit('MOV', self.reg(0), self.reg(1))
            return elem_t
        # 标量元素
        self.emit('LD', self.reg(0), ('mem', 0, 0))
        return elem_t

    # ---------------- 赋值 (在 gen_value 中拦截 binop '=') ----------------

    def _gen_assign(self, target, value_node):
        vt = self.gen_value(value_node)
        # 目标类型
        tt = self._expr_type(target)
        self._convert(vt, tt)
        self.emit('MOV', self.reg(2), self.reg(0))  # value
        self._gen_lvalue_addr(target)               # x0 = addr
        self.emit('SD', self.reg(2), ('mem', 0, 0))
        return tt

    def _expr_type(self, node):
        kind = node[0]
        if kind == 'var':
            return self._var_type(node[1])
        if kind == 'member':
            obj_t = self._expr_type(node[1])
            if _is_struct(obj_t):
                ftype, _ = self._struct_field(obj_t, node[2])
                return ftype
        if kind == 'index':
            base_t = self._expr_type(node[1])
            if base_t == 'string':
                return 'int'
            if _is_fixed_array(base_t):
                elem = _array_elem(base_t)
            elif _is_ptr_array(base_t):
                elem = _array_elem(base_t)
            else:
                elem = None
            if _is_fixed_array(elem):
                return elem
            return elem
        if kind == 'call':
            f = self.functions.get(node[1])
            if f:
                return f.ret_type
            if node[1] in ('min', 'max'):
                at = self._expr_type(node[2][0])
                bt = self._expr_type(node[2][1])
                return 'float' if (at == 'float' or bt == 'float') else 'int'
            return self._builtin_ret_type(node[1])
        if kind == 'num':
            return 'float' if node[2] else 'int'
        if kind == 'bool':
            return 'bool'
        if kind == 'str':
            return 'string'
        if kind == 'binop':
            if node[1] in ('+=', '-=', '*=', '/=', '%=',
                           '&=', '|=', '^=', '<<=', '>>='):
                return self._expr_type(node[2])
            if node[1] in ('&', '|', '^', '<<', '>>'):
                return 'int'
            if node[1] in ('+', '-', '*', '%'):
                lt = self._expr_type(node[2])
                rt = self._expr_type(node[3])
                if lt == 'string' or rt == 'string':
                    return 'string'
                return 'float' if lt == 'float' or rt == 'float' else 'int'
            if node[1] == '/':
                return 'float'
            if node[1] in ('==', '!=', '<', '>', '<=', '>=', '&&', '||'):
                return 'bool'
        if kind == 'cond':
            lt = self._expr_type(node[2])
            rt = self._expr_type(node[3])
            if (lt == 'string') != (rt == 'string'):
                return 'int'
            return 'string' if lt == 'string' else \
                ('float' if lt == 'float' or rt == 'float' else 'int')
        if kind in ('preinc', 'predec', 'postinc', 'postdec'):
            return self._expr_type(node[1])
        if kind == 'neg':
            it = self._expr_type(node[1])
            return 'float' if it == 'float' else 'int'
        if kind == 'not':
            return 'bool'
        if kind == 'bitnot':
            return 'int'
        return None

    # ---------------- 二元运算 ----------------

    def _gen_binop(self, op, left, right):
        if op == '=':
            return self._gen_assign(left, right)
        if op in _COMPOUND_TO_BASE:
            return self._gen_compound(left, _COMPOUND_TO_BASE[op], right)
        if op == '&&' or op == '||':
            return self._gen_logical(op, left, right)
        if op in ('&', '|', '^', '<<', '>>'):
            return self._gen_bitwise(op, left, right)

        lt = self._expr_type(left)
        rt = self._expr_type(right)
        if op == '+' and (lt == 'string' or rt == 'string'):
            self._gen_string_value(left)
            self.emit('PUSH', self.reg(0))
            self._gen_string_value(right)
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.STR_CONCAT))
            return 'string'

        float_mode = (lt == 'float' or rt == 'float') or op == '/'
        # 左操作数
        self.gen_value(left)
        if float_mode and lt == 'int':
            self.emit('SYS', self.imm(Syscall.ITOF))
        if float_mode and lt == 'bool':
            self.emit('SYS', self.imm(Syscall.ITOF))
        self.emit('PUSH', self.reg(0))
        # 右操作数
        self.gen_value(right)
        if float_mode and rt in ('int', 'bool'):
            self.emit('SYS', self.imm(Syscall.ITOF))
        self.emit('MOV', self.reg(1), self.reg(0))
        self.emit('POP', self.reg(0))

        if float_mode:
            sys_map = {'+': Syscall.FADD, '-': Syscall.FSUB,
                       '*': Syscall.FMUL, '/': Syscall.FDIV}
            if op == '%':
                raise CompilerError("Float modulo not supported")
            self.emit('SYS', self.imm(sys_map[op]))
            return 'float'

        if op == '%':
            # x0 = a - (a/b)*b
            self.emit('MOV', self.reg(2), self.reg(1))  # b
            self.emit('PUSH', self.reg(0))             # a
            self.emit('DIV', self.reg(0), self.reg(2))  # a/b
            self.emit('MUL', self.reg(0), self.reg(2))  # (a/b)*b
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SUB', self.reg(0), self.reg(1))
            return 'int'

        op_map = {'+': 'ADD', '-': 'SUB', '*': 'MUL'}
        if op not in op_map:
            raise CompilerError(f"Unsupported int operator: {op}")
        self.emit(op_map[op], self.reg(0), self.reg(1))
        return 'int'

    def _gen_logical(self, op, left, right):
        l_true = self.new_label('lt')
        l_false = self.new_label('lf')
        l_end = self.new_label('le')
        if op == '&&':
            self.gen_cond_jump_false(left, l_false)
            self.gen_cond_jump_false(right, l_false)
        else:  # ||
            self.gen_cond_jump_true(left, l_true)
            self.gen_cond_jump_true(right, l_true)
            self.emit('JMP', self.lab(l_false))
        self.label(l_true)
        self.emit('MOV', self.reg(0), self.imm(1))
        self.emit('JMP', self.lab(l_end))
        self.label(l_false)
        self.emit('MOV', self.reg(0), self.imm(0))
        self.label(l_end)
        return 'bool'

    def _gen_bitwise(self, op, left, right) -> Any:
        """位运算 & | ^ << >> (整数); >> 为算术右移 (保留符号)。"""
        lt = self._expr_type(left)
        rt = self._expr_type(right)
        if lt in ('float', 'string') or rt in ('float', 'string'):
            raise CompilerError(
                f"Bitwise operator '{op}' requires integer operands "
                f"(got {lt} and {rt})")
        self.gen_value(left)
        self.emit('PUSH', self.reg(0))
        self.gen_value(right)
        self.emit('MOV', self.reg(1), self.reg(0))
        self.emit('POP', self.reg(0))
        if op == '&':
            self.emit('AND', self.reg(0), self.reg(1))
        elif op == '|':
            self.emit('OR', self.reg(0), self.reg(1))
        elif op == '^':
            self.emit('XOR', self.reg(0), self.reg(1))
        elif op == '<<':
            self.emit('SHL', self.reg(0), self.reg(1))
        else:  # '>>'
            self.emit('ASR', self.reg(0), self.reg(0), self.reg(1))
        return 'int'

    # ---------------- 复合赋值 / 自增自减 / 三目 ----------------

    def _gen_compound(self, target, op: str, value_node) -> Any:
        """target op= value (op ∈ + - * / % & | ^ << >>); 左值地址只求值一次。"""
        tt = self._expr_type(target)
        if tt not in ('int', 'bool', 'float'):
            raise CompilerError(f"Cannot apply '{op}=' to type: {tt}")
        if tt == 'float' and op not in ('+', '-', '*', '/'):
            raise CompilerError(f"Cannot apply '{op}=' to float")
        float_mode = tt == 'float'

        self._gen_lvalue_addr(target)
        self.emit('PUSH', self.reg(0))                 # [SP] = addr
        vt = self.gen_value(value_node)                # x0 = rhs
        self._convert(vt, tt)
        self.emit('MOV', self.reg(1), self.reg(0))     # b = rhs
        self.emit('LD', self.reg(2), ('mem', 32, 0))   # x2 = addr
        self.emit('LD', self.reg(0), ('mem', 2, 0))    # a = old

        if float_mode:
            fmap = {'+': Syscall.FADD, '-': Syscall.FSUB,
                    '*': Syscall.FMUL, '/': Syscall.FDIV}
            self.emit('SYS', self.imm(fmap[op]))
        elif op == '+':
            self.emit('ADD', self.reg(0), self.reg(1))
        elif op == '-':
            self.emit('SUB', self.reg(0), self.reg(1))
        elif op == '*':
            self.emit('MUL', self.reg(0), self.reg(1))
        elif op == '/':
            self.emit('DIV', self.reg(0), self.reg(1))
        elif op == '%':
            self.emit('MOV', self.reg(3), self.reg(1))  # 除数备份
            self.emit('PUSH', self.reg(0))
            self.emit('DIV', self.reg(0), self.reg(3))
            self.emit('MUL', self.reg(0), self.reg(3))
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SUB', self.reg(0), self.reg(1))
        elif op == '&':
            self.emit('AND', self.reg(0), self.reg(1))
        elif op == '|':
            self.emit('OR', self.reg(0), self.reg(1))
        elif op == '^':
            self.emit('XOR', self.reg(0), self.reg(1))
        elif op == '<<':
            self.emit('SHL', self.reg(0), self.reg(1))
        elif op == '>>':
            self.emit('ASR', self.reg(0), self.reg(0), self.reg(1))
        else:
            raise CompilerError(f"Unsupported compound operator: {op}=")

        self.emit('SD', self.reg(0), ('mem', 2, 0))
        self.emit('ADDI', self.reg(32), self.reg(32), self.imm(8))
        return tt

    def _gen_incdec(self, kind: str, target) -> Any:
        """++/-- (前缀与后缀); 后缀返回旧值, 前缀返回新值。"""
        tt = self._expr_type(target)
        if tt not in ('int', 'bool', 'float'):
            raise CompilerError(f"Cannot {kind} value of type: {tt}")
        op = '+' if 'inc' in kind else '-'
        postfix = kind.startswith('post')
        float_mode = tt == 'float'

        self._gen_lvalue_addr(target)
        self.emit('PUSH', self.reg(0))                 # [SP] = addr
        self.emit('LD', self.reg(2), ('mem', 32, 0))   # x2 = addr
        self.emit('LD', self.reg(0), ('mem', 2, 0))    # a = old
        if postfix:
            self.emit('MOV', self.reg(5), self.reg(0))  # 保存旧值
        if float_mode:
            self.emit('MOV', self.reg(0), self.imm(1))
            self.emit('SYS', self.imm(Syscall.ITOF))    # b = 1.0
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('LD', self.reg(0), ('mem', 2, 0))  # a 回填
            self.emit('SYS', self.imm(Syscall.FADD if op == '+'
                                       else Syscall.FSUB))
        else:
            self.emit('MOV', self.reg(1), self.imm(1))
            self.emit('ADD' if op == '+' else 'SUB',
                      self.reg(0), self.reg(1))
        self.emit('SD', self.reg(0), ('mem', 2, 0))
        self.emit('ADDI', self.reg(32), self.reg(32), self.imm(8))
        if postfix:
            self.emit('MOV', self.reg(0), self.reg(5))
        return tt

    def _gen_ternary(self, cond, a, b) -> Any:
        lt = self._expr_type(a)
        rt = self._expr_type(b)
        if (lt == 'string') != (rt == 'string'):
            raise CompilerError("Cannot mix string and numeric in '?:'")
        tt = 'string' if lt == 'string' else \
            ('float' if (lt == 'float' or rt == 'float') else 'int')
        l_false = self.new_label('cndf')
        l_end = self.new_label('cnde')
        self.gen_cond_jump_false(cond, l_false)
        t1 = self.gen_value(a)
        self._convert(t1, tt)
        self.emit('MOV', self.reg(6), self.reg(0))
        self.emit('JMP', self.lab(l_end))
        self.label(l_false)
        t2 = self.gen_value(b)
        self._convert(t2, tt)
        self.emit('MOV', self.reg(6), self.reg(0))
        self.label(l_end)
        self.emit('MOV', self.reg(0), self.reg(6))
        return tt

    # ---------------- 字符串化 (print/concat) ----------------

    def _gen_string_value(self, node) -> None:
        """求值表达式并将 x0 转为字符串指针。"""
        t = self.gen_value(node)
        if t == 'string':
            return
        if t == 'float':
            self.emit('SYS', self.imm(Syscall.FTOA))
        elif t == 'bool':
            self.emit('SYS', self.imm(Syscall.BOOL_STR))
        else:
            self.emit('SYS', self.imm(Syscall.ITOA))

    def gen_print(self, node, newline: bool) -> None:
        t = self.gen_value(node)
        if t == 'string':
            pass
        elif t == 'float':
            self.emit('SYS', self.imm(Syscall.FTOA))
        elif t == 'bool':
            self.emit('SYS', self.imm(Syscall.BOOL_STR))
        else:
            self.emit('SYS', self.imm(Syscall.ITOA))
        self.emit('SYS', self.imm(Syscall.PRINT_STR))
        if newline:
            self.emit('OUT', self.imm(10))

    # ---------------- 函数调用 ----------------

    def _builtin_ret_type(self, name: str):
        if name in ('sin', 'cos', 'tan', 'sqrt', 'pow', 'floor', 'ceil', 'round'):
            return 'float'
        if name in ('strlen', 'strcmp', 'rand', 'time', 'abs', 'input',
                    'idiv', 'atoi'):
            return 'int'
        if name in ('strcpy', 'int_to_str', 'itoa', 'float_to_str', 'ftoa',
                    'bool_to_str', 'substr', 'upper', 'lower',
                    'trim', 'ltrim', 'rtrim'):
            return 'string'
        return None

    def _gen_call(self, name: str, args: list):
        if name in ('println', 'print'):
            # 单参数 (拼接由表达式完成)
            if args:
                self.gen_print(args[0], newline=(name == 'println'))
            else:
                self.emit('OUT', self.imm(10))
            return 'void'

        math_unary = {'sqrt': Syscall.SQRT, 'sin': Syscall.SIN,
                      'cos': Syscall.COS, 'tan': Syscall.TAN}
        if name in math_unary:
            at = self.gen_value(args[0])
            if at == 'int':
                self.emit('SYS', self.imm(Syscall.ITOF))
            self.emit('SYS', self.imm(math_unary[name]))
            return 'float'
        if name == 'pow':
            self._arg_float(args[0])
            self.emit('PUSH', self.reg(0))
            self._arg_float(args[1])
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.POW))
            return 'float'
        if name == 'abs':
            self.gen_value(args[0])
            self.emit('SYS', self.imm(Syscall.ABS))
            return 'int'
        if name == 'strlen':
            self.gen_value(args[0])
            self.emit('SYS', self.imm(Syscall.STRLEN))
            return 'int'
        if name == 'strcmp':
            self.gen_value(args[0])
            self.emit('PUSH', self.reg(0))
            self.gen_value(args[1])
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.STRCMP))
            return 'int'
        if name == 'strcpy':
            self.gen_value(args[0])
            self.emit('PUSH', self.reg(0))
            self.emit('MOV', self.reg(0), self.imm(self._data_string("")))
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.STR_CONCAT))
            return 'string'
        if name == 'rand':
            self.emit('SYS', self.imm(Syscall.RAND))
            return 'int'
        if name == 'srand':
            self.gen_value(args[0])
            self.emit('SYS', self.imm(Syscall.SRAND))
            return 'void'
        if name in ('int_to_str', 'itoa'):
            self.gen_value(args[0])
            self.emit('SYS', self.imm(Syscall.ITOA))
            return 'string'
        if name in ('float_to_str', 'ftoa'):
            t = self.gen_value(args[0])
            if t in ('int', 'bool'):
                self.emit('SYS', self.imm(Syscall.ITOF))
            self.emit('SYS', self.imm(Syscall.FTOA))
            return 'string'
        if name in ('bool_to_str',):
            self.gen_value(args[0])
            self.emit('SYS', self.imm(Syscall.BOOL_STR))
            return 'string'
        if name == 'substr':
            self._gen_string_value(args[0])
            self.emit('PUSH', self.reg(0))
            self.gen_value(args[1])
            self.emit('PUSH', self.reg(0))
            self.gen_value(args[2])
            self.emit('MOV', self.reg(2), self.reg(0))
            self.emit('POP', self.reg(1))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.SUBSTR))
            return 'string'
        if name == 'indexof':
            self._gen_string_value(args[0])
            self.emit('PUSH', self.reg(0))
            self._gen_string_value(args[1])
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            self.emit('SYS', self.imm(Syscall.INDEXOF))
            return 'int'
        if name in ('upper', 'lower'):
            self._gen_string_value(args[0])
            self.emit('SYS', self.imm(Syscall.TOUPPER if name == 'upper'
                                       else Syscall.TOLOWER))
            return 'string'
        if name == 'time':
            self.emit('SYS', self.imm(Syscall.TIME))
            return 'int'
        if name == 'input':
            self.emit('MOV', self.reg(0), self.imm(0))
            return 'int'

        # 用户函数
        fdef = self.functions.get(name)
        if fdef is None:
            raise CompilerError(f"Unknown function: {name}")
        for k, arg in enumerate(args):
            at = self.gen_value(arg)
            ptype = fdef.params[k][1] if k < len(fdef.params) else None
            self._convert(at, self._param_promote(ptype))
            self.emit('PUSH', self.reg(0))
        self.emit('CALL', self.lab(name))
        # 调用方清理参数 (不得使用 x0, 它持有返回值)
        nargs = len(args)
        if nargs:
            self.emit('ADDI', self.reg(6), self.reg(32), self.imm(nargs * 8))
            self.emit('MOV', self.reg(32), self.reg(6))
        return fdef.ret_type

    @staticmethod
    def _param_promote(ptype):
        # 数组参数衰减
        if _is_fixed_array(ptype):
            return ('ptrarray', _array_elem(ptype))
        return ptype

    def _arg_float(self, node) -> None:
        t = self.gen_value(node)
        if t == 'int' or t == 'bool':
            self.emit('SYS', self.imm(Syscall.ITOF))

    # ---------------- 条件跳转 ----------------

    _COND_FALSE_JUMP = {
        '==': 'NE', '!=': 'EQ',
        '<': 'GE', '>': 'LE',
        '<=': 'GT', '>=': 'LT',
    }

    def gen_cond_jump_false(self, node, label_false: str) -> None:
        kind = node[0]
        if kind == 'bool':
            if not node[1]:
                self.emit('JMP', self.lab(label_false))
            return
        if kind == 'binop' and node[1] == '&&':
            self.gen_cond_jump_false(node[2], label_false)
            self.gen_cond_jump_false(node[3], label_false)
            return
        if kind == 'binop' and node[1] == '||':
            l_true = self.new_label('ortrue')
            self.gen_cond_jump_true(node[2], l_true)
            self.gen_cond_jump_true(node[3], l_true)
            self.emit('JMP', self.lab(label_false))
            self.label(l_true)
            return
        if kind == 'not':
            self.gen_cond_jump_true(node[1], label_false)
            return
        if kind == 'binop' and node[1] in self._COND_FALSE_JUMP:
            op = node[1]
            lt = self._expr_type(node[2])
            rt = self._expr_type(node[3])
            float_mode = lt == 'float' or rt == 'float'
            self.gen_value(node[2])
            if float_mode and lt in ('int', 'bool'):
                self.emit('SYS', self.imm(Syscall.ITOF))
            self.emit('PUSH', self.reg(0))
            self.gen_value(node[3])
            if float_mode and rt in ('int', 'bool'):
                self.emit('SYS', self.imm(Syscall.ITOF))
            self.emit('MOV', self.reg(1), self.reg(0))
            self.emit('POP', self.reg(0))
            if float_mode:
                self.emit('SYS', self.imm(Syscall.FCMP))
                self.emit('CMP', self.reg(0), self.imm(0))
            else:
                self.emit('CMP', self.reg(0), self.reg(1))
            self.emit('B', self.lab(label_false),
                      ('cond', self._COND_FALSE_JUMP[op]))
            return
        # 普通布尔值
        self.gen_value(node)
        self.emit('CMP', self.reg(0), self.imm(0))
        self.emit('JZ', self.lab(label_false))

    def gen_cond_jump_true(self, node, label_true: str) -> None:
        l_false = self.new_label('cf')
        self.gen_cond_jump_false(node, l_false)
        self.emit('JMP', self.lab(label_true))
        self.label(l_false)
