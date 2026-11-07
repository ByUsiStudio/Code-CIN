"""LEGACY: CIN AST -> C 的后端。编译器入口已切到 Go 的 cincc（直接出机器码），
本模块仅保留给 --build-backend 的 C 路径与历史测试，不再由 CLI/mkn 调用。"""
"""直接 AST → C 的原生代码生成（不经过 UCPU 指令集）。

这是「移除自带指令集」迁移的第 0 阶段：把 CIN 源码解析成 AST 后，直接生成 C
并交给本机/交叉 C 编译器产出设备本机的机器码，中间不再产生任何 UCPU 操作码、
BIN/UCBC 字节码或 .sdgcc 容器。

当前覆盖的语言子集（有意收窄，超出即报错，绝不静默降级）：

* ``int`` 局部变量/参数/返回值（``int64_t``）；
* 算术与比较：``+ - * / %``、``< <= > >= == !=``；
* 语句：``decl`` / 赋值 / ``expr`` 调用 / ``while`` / ``block`` / ``return``；
* 内建输出：``print(x)`` / ``println(x)``，仅整数。

不支持的结构（struct/数组/字符串/浮点/指针/if 等）会抛
:class:`NativeGenError`，由调用方决定回退到既有编译器。
"""

import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import toolchain

__all__ = ['NativeGenError', 'emit_c', 'compile_native', 'supports_source']

AST = Any

_BINOPS = {'+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
           '<': '<', '<=': '<=', '>': '>', '>=': '>=', '==': '==', '!=': '!=',
           '&&': '&&', '||': '||', '&': '&', '|': '|', '^': '^',
           '<<': '<<', '>>': '>>'}
_BUILTINS = {
    'strlen': ('__cin_strlen', 1, 'int'),
    'abs': ('__cin_abs', 1, 'int'),
    'indexof': ('__cin_indexof', 2, 'int'),
    'atoi': ('__cin_atoi', 1, 'int'),
    'idiv': ('__cin_idiv', 2, 'int'),
    'min': ('__cin_min', 2, 'int'),
    'max': ('__cin_max', 2, 'int'),
    'substr': ('__cin_substr', 3, 'str'),
    'upper': ('__cin_case', 1, 'str'),
    'lower': ('__cin_case', 1, 'str'),
    'int_to_str': ('__cin_itoa', 1, 'str'),
    'strcmp': ('__cin_strcmp', 2, 'int'),
    'trim': ('__cin_trim', 1, 'str'),
    'sqrt': ('__cin_sqrt', 1, 'float'),
    'pow': ('pow', 2, 'float'),
    'floor': ('floor', 1, 'float'),
    'ceil': ('ceil', 1, 'float'),
    'round': ('round', 1, 'float'),
    'sin': ('sin', 1, 'float'),
    'cos': ('cos', 1, 'float'),
    'tan': ('tan', 1, 'float'),
    'fabs': ('fabs', 1, 'float'),
    'float_to_str': ('__cin_ftoa', 1, 'str'),
    'time': ('__cin_time', 0, 'int'),
    'rand': ('__cin_rand', 0, 'int'),
    'srand': ('__cin_srand', 1, 'int'),
}

_PREAMBLE = ('#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n'
             '#include <stdlib.h>\n#include <math.h>\n#include <time.h>\n\n'
             'static double __cin_sqrt(double x) { return sqrt(x); }\n'
             'static int64_t __cin_time(void) { return (int64_t)time(NULL); }\n'
             'static int64_t __cin_rand(void) { return (int64_t)rand(); }\n'
             'static int64_t __cin_srand(int64_t seed) { '
             'srand((unsigned)seed); return 0; }\n'
             'static int64_t __cin_strlen(const char *s) { '
             'return (int64_t)strlen(s); }\n'
             'static int64_t __cin_abs(int64_t v) { return v < 0 ? -v : v; }\n'
             'static int64_t __cin_indexof(const char *h, const char *n) {\n'
             '    const char *p = strstr(h, n);\n'
             '    return p ? (int64_t)(p - h) : -1;\n'
             '}\n'
             'static int64_t __cin_atoi(const char *s) { '
             'return (int64_t)strtoll(s, NULL, 10); }\n'
             'static int64_t __cin_idiv(int64_t a, int64_t b) { '
             'return b ? a / b : 0; }\n'
             'static int64_t __cin_min(int64_t a, int64_t b) { '
             'return a < b ? a : b; }\n'
             'static int64_t __cin_max(int64_t a, int64_t b) { '
             'return a > b ? a : b; }\n'
             'static void __cin_print_int(int64_t v) { printf("%lld", '
             '(long long)v); }\n'
             'static void __cin_println_int(int64_t v) { '
             'printf("%lld\\n", (long long)v); }\n'
             'static void __cin_print_bool(int64_t v) { '
             'printf("%s", v ? "true" : "false"); }\n'
             'static void __cin_println_bool(int64_t v) { '
             'printf("%s\\n", v ? "true" : "false"); }\n'
             'static void __cin_print_str(const char *s) { printf("%s", s); }\n'
             'static void __cin_println_str(const char *s) { '
             'printf("%s\\n", s); }\n'
             'static void __cin_print_float(double v) { printf("%g", v); }\n'
             'static void __cin_println_float(double v) { '
             'printf("%g\\n", v); }\n'
             'static char __cin_arena[65536];\n'
             'static size_t __cin_arena_used = 0;\n'
             'static char *__cin_alloc(size_t n) {\n'
             '    char *p;\n'
             '    if (__cin_arena_used + n > sizeof(__cin_arena)) return NULL;\n'
             '    p = __cin_arena + __cin_arena_used;\n'
             '    __cin_arena_used += n;\n'
             '    return p;\n'
             '}\n'
             'static const char *__cin_substr(const char *s, int64_t start, '
             'int64_t len) {\n'
             '    int64_t n = (int64_t)strlen(s), e;\n'
             '    char *p;\n'
             '    if (start < 0) start = 0;\n'
             '    if (start > n) start = n;\n'
             '    if (len < 0) len = 0;\n'
             '    e = start + len;\n'
             '    if (e > n) e = n;\n'
             '    p = __cin_alloc((size_t)(e - start) + 1);\n'
             '    if (!p) return "";\n'
             '    memcpy(p, s + start, (size_t)(e - start));\n'
             '    p[e - start] = 0;\n'
             '    return p;\n'
             '}\n'
             'static const char *__cin_case(const char *s, int up) {\n'
             '    size_t n = strlen(s), i;\n'
             '    char *p = __cin_alloc(n + 1);\n'
             '    if (!p) return "";\n'
             '    for (i = 0; i < n; i++) {\n'
             '        char c = s[i];\n'
             '        if (up && c >= 97 && c <= 122) c -= 32;\n'
             '        if (!up && c >= 65 && c <= 90) c += 32;\n'
             '        p[i] = c;\n'
             '    }\n'
             '    p[n] = 0;\n'
             '    return p;\n'
             '}\n'
             'static const char *__cin_itoa(int64_t v) {\n'
             '    char *p = __cin_alloc(32);\n'
             '    if (!p) return "";\n'
             '    snprintf(p, 32, "%lld", (long long)v);\n'
             '    return p;\n'
             '}\n'
             'static int64_t __cin_strcmp(const char *a, const char *b) {\n'
             '    return (int64_t)strcmp(a, b);\n'
             '}\n'
             'static const char *__cin_ftoa(double v) {\n'
             '    char *p = __cin_alloc(64);\n'
             '    if (!p) return "";\n'
             '    snprintf(p, 64, "%g", v);\n'
             '    return p;\n'
             '}\n'
             'static const char *__cin_trim(const char *s) {\n'
             '    size_t b = 0, e = strlen(s);\n'
             '    char *p;\n'
             '    while (s[b] == 32 || s[b] == 9 || s[b] == 10 || s[b] == 13)\n'
             '        b++;\n'
             '    while (e > b && (s[e-1] == 32 || s[e-1] == 9 || '
             's[e-1] == 10 || s[e-1] == 13)) e--;\n'
             '    p = __cin_alloc(e - b + 1);\n'
             '    if (!p) return "";\n'
             '    memcpy(p, s + b, e - b);\n'
             '    p[e - b] = 0;\n'
             '    return p;\n'
             '}\n'
             'static const char *__cin_concat(const char *a, const char *b) {\n'
             '    size_t na = strlen(a), nb = strlen(b);\n'
             '    char *p;\n'
             '    if (__cin_arena_used + na + nb + 1 > sizeof(__cin_arena))\n'
             '        return "";\n'
             '    p = __cin_arena + __cin_arena_used;\n'
             '    memcpy(p, a, na);\n'
             '    memcpy(p + na, b, nb + 1);\n'
             '    __cin_arena_used += na + nb + 1;\n'
             '    return p;\n'
             '}\n\n')


def _c_string(text: str) -> str:
    out = []
    for byte in text.encode('utf-8'):
        if byte == 0x22 or byte == 0x5C:
            out.append('\\' + chr(byte))
        elif 32 <= byte < 127:
            out.append(chr(byte))
        else:
            out.append('\\%03o' % byte)
    return '"' + ''.join(out) + '"'


class NativeGenError(RuntimeError):
    """AST 超出直接原生后端当前支持的语言子集。"""


def _typename(t: Any) -> str:
    if isinstance(t, tuple) and t[0] == 'struct':
        return f'cin_{t[1]}'
    if isinstance(t, tuple) and t[0] == 'ptrarray':
        return 'int64_t *'
    if t == 'int':
        return 'int64_t'
    if t == 'bool':
        return 'int64_t'
    if t == 'float':
        return 'double'
    if t == 'string':
        return 'const char *'
    if t == 'void':
        return 'void'
    raise NativeGenError(f'直接原生后端暂不支持类型: {t!r}')


def _field_ctype(t: Any) -> str:
    if t == 'int':
        return 'int64_t'
    if t == 'bool':
        return 'int64_t'
    if t == 'float':
        return 'double'
    if t == 'string':
        return 'const char *'
    raise NativeGenError(f'struct 字段类型 {t!r} 暂不支持')


class _Generator:
    def __init__(self, functions: Dict[str, Any],
                 structs: Optional[Dict[str, Any]] = None,
                 globals_: Optional[List[Any]] = None):
        self.functions = functions
        self.structs = structs or {}
        self.globals = globals_ or []
        self.global_names = {self._sanitize(g.name) for g in self.globals}
        self.lines: List[str] = []
        self.vars: List[str] = []
        self.types: Dict[str, str] = {}
        self.arrays: Dict[str, int] = {}
        self.buf: List[str] = []

    # ---------- 发射 ----------

    def emit(self, text: str = '') -> None:
        self.lines.append(text)

    @staticmethod
    def _sanitize(name: str) -> str:
        out = ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
        return 'v_' + out if out[:1].isdigit() else out

    @classmethod
    def _fname(cls, name: str) -> str:
        # 用户函数统一加前缀, 避免与 C 的 main / 运行库符号冲突
        return 'fn_' + cls._sanitize(name)

    # ---------- 表达式 ----------

    def expr(self, node: AST) -> str:
        kind = node[0]
        if kind == 'num':
            value = node[1]
            if node[2]:                       # 浮点字面量
                return f'{float(value)!r}'
            return f'INT64_C({int(value)})'
        if kind == 'var':
            return self._sanitize(node[1])
        if kind == 'bool':
            return '1' if node[1] else '0'
        if kind == 'not':
            return f'(!{self.expr(node[1])})'
        if kind == 'bitnot':
            return f'(~{self.expr(node[1])})'
        if kind == 'neg':
            return f'(-{self.expr(node[1])})'
        if kind == 'binop':
            op = node[1]
            lt, rt = self.arg_type(node[2]), self.arg_type(node[3])
            if 'str' in (lt, rt):
                if op in ('==', '!='):
                    cmp = '==' if op == '==' else '!='
                    return (f'(strcmp({self.expr(node[2])}, '
                            f'{self.expr(node[3])}) {cmp} 0)')
                if op == '+':
                    return (f'__cin_concat({self.expr(node[2])}, '
                            f'{self.expr(node[3])})')
                raise NativeGenError(f'字符串不支持运算符: {op!r}')
            if op in _BINOPS:
                return (f'({self.expr(node[2])} {_BINOPS[op]} '
                        f'{self.expr(node[3])})')
            raise NativeGenError(f'直接原生后端暂不支持运算符: {op!r}')
        if kind == 'index':
            return f'{self.expr(node[1])}[{self.expr(node[2])}]'
        if kind == 'member':
            if node[1][0] != 'var':
                raise NativeGenError('暂不支持嵌套 struct 成员访问')
            base = self._sanitize(node[1][1])
            return f'{base}.{node[2]}'
        if kind == 'str':
            return _c_string(node[1])
        if kind == 'call':
            return self.call(node[1], node[2])
        raise NativeGenError(f'直接原生后端暂不支持表达式节点: {kind!r}')

    def arg_type(self, arg: AST) -> str:
        kind = arg[0]
        if kind == 'str':
            return 'str'
        if kind == 'num':
            return 'float' if arg[2] else 'int'
        if kind == 'var':
            t = self.types.get(arg[1], 'int')
            return 'str' if t == 'string' else t
        if kind == 'index':
            base = arg[1]
            if base[0] == 'var':
                t = str(self.types.get(base[1], ''))
                if t.startswith('array:') or t.startswith('ptrarray:'):
                    elem = t.split(':', 1)[1]
                    return 'str' if elem == 'string' else elem
            return 'int'
        if kind == 'member':
            if arg[1][0] != 'var':
                return 'int'
            t = str(self.types.get(arg[1][1], ''))
            if not t.startswith('struct:'):
                return 'int'
            sd = self.structs.get(t.split(':', 1)[1])
            if sd is not None:
                for fname, ftype in sd.fields:
                    if fname == arg[2]:
                        return 'str' if ftype == 'string' else ftype
            return 'int'
        if kind == 'neg':
            return self.arg_type(arg[1])
        if kind == 'not':
            return 'bool'
        if kind == 'bool':
            return 'bool'
        if kind == 'binop':
            lt, rt = self.arg_type(arg[2]), self.arg_type(arg[3])
            if arg[1] in ('==', '!=', '<', '<=', '>', '>=', '&&', '||'):
                return 'bool'
            if 'str' in (lt, rt):
                return 'str'
            return 'float' if 'float' in (lt, rt) else 'int'
        if kind == 'call':
            if arg[1] in _BUILTINS:
                return _BUILTINS[arg[1]][2]
            fn = self.functions.get(arg[1])
            ret = (fn.ret_type if fn is not None else None) or 'int'
            if isinstance(ret, tuple) and ret[0] == 'struct':
                return 'struct:' + ret[1]
            return 'str' if ret == 'string' else ret
        return 'int'

    def call(self, name: str, args: Sequence[AST]) -> str:
        if name in ('print', 'println'):
            if len(args) != 1:
                raise NativeGenError(f'{name}() 需要恰好 1 个参数')
            arg = args[0]
            kind = self.arg_type(arg)
            suffix = 'ln' if name == 'println' else ''
            if kind == 'str':
                inner = _c_string(arg[1]) if arg[0] == 'str' \
                    else self.expr(arg)
                return f'__cin_print{suffix}_str({inner})'
            if kind == 'bool':
                return f'__cin_print{suffix}_bool({self.expr(arg)})'
            if kind == 'float':
                return f'__cin_print{suffix}_float({self.expr(arg)})'
            return f'__cin_print{suffix}_int({self.expr(arg)})'
        if name in _BUILTINS:
            fn, arity, _ret = _BUILTINS[name]
            if len(args) != arity:
                raise NativeGenError(f'{name}() 需要 {arity} 个参数')
            if name in ('upper', 'lower'):
                flag = 1 if name == 'upper' else 0
                return f'{fn}({self.expr(args[0])}, {flag})'
            return f'{fn}({", ".join(self.expr(a) for a in args)})'
        if name not in self.functions:
            raise NativeGenError(f'直接原生后端暂不支持内建/外部函数: {name!r}')
        callee = self.functions[name]
        if len(callee.params) != len(args):
            raise NativeGenError(f'{name}() 参数数量不匹配')
        rendered = ', '.join(self.expr(a) for a in args)
        return f'{self._fname(name)}({rendered})'

    # ---------- 语句 ----------

    def declare(self, name: str, typ: Any) -> str:
        cname = self._sanitize(name)
        if isinstance(typ, tuple) and typ[0] == 'array':
            base, size = typ[1], typ[2]
            if base not in ('int', 'float', 'string', 'bool'):
                raise NativeGenError(f'数组元素类型 {base!r} 暂不支持')
            if not isinstance(size, int):
                raise NativeGenError('数组长度必须是整数字面量')
            self.arrays[cname] = int(size)
            self.types[cname] = 'array:' + base
        elif isinstance(typ, tuple) and typ[0] == 'ptrarray':
            if typ[1] != 'int':
                raise NativeGenError(f'数组类型 {typ[1]!r} 暂不支持')
            self.types[cname] = 'ptrarray'
        elif isinstance(typ, tuple) and typ[0] == 'struct':
            if typ[1] not in self.structs:
                raise NativeGenError(f'未知 struct 类型: {typ[1]!r}')
            self.types[cname] = 'struct:' + typ[1]
        elif typ in ('int', 'bool', 'float', 'string'):
            self.types[cname] = typ
        else:
            raise NativeGenError(f'直接原生后端暂不支持声明类型: {typ!r}')
        if cname not in self.vars:
            self.vars.append(cname)
        return cname

    def assign(self, target: AST, value: str) -> str:
        if target[0] == 'var':
            cname = self._sanitize(target[1])
            if cname not in self.vars and cname not in self.global_names:
                self.vars.append(cname)
                self.types.setdefault(cname, 'int')
            return f'{cname} = {value}'
        if target[0] == 'index':
            return f'{self.expr(target[1])}[{self.expr(target[2])}] = {value}'
        if target[0] == 'member':
            return f'{self.expr(target)} = {value}'
        raise NativeGenError('直接原生后端暂不支持该赋值目标')

    def simple_stmt(self, node: AST) -> str:
        """for 的 init/step 这类单行语句, 返回 C 片段 (不含分号)。"""
        if node[0] == 'decl':
            name, typ, init, _extra = node[1][0]
            cname = self.declare(name, typ)
            if isinstance(typ, tuple):
                raise NativeGenError('for 初始化里不能声明数组')
            return f'{cname} = {self.expr(init) if init else 0}'
        if node[0] == 'expr':
            inner = node[1]
            if inner[0] == 'binop' and inner[1] == '=':
                return self.assign(inner[2], self.expr(inner[3]))
            if inner[0] == 'call':
                return self.call(inner[1], inner[2])
        raise NativeGenError(f'for 的 init/step 不支持该语句: {node[0]!r}')

    def stmt(self, node: AST, indent: int) -> None:
        pad = '    ' * indent
        kind = node[0]
        if kind == 'decl':
            for name, typ, init, _extra in node[1]:
                cname = self.declare(name, typ)
                if isinstance(typ, tuple) and typ[0] == 'array':
                    if init is not None:
                        raise NativeGenError('暂不支持数组初始化列表')
                    continue
                if init is None:
                    if isinstance(typ, tuple) and typ[0] == 'struct':
                        continue            # 已在 hoist 处零初始化
                    if typ == 'string':
                        self.emit(f'{pad}{cname} = "";')
                    else:
                        self.emit(f'{pad}{cname} = 0;')
                else:
                    if typ != 'string' and init[0] == 'str':
                        raise NativeGenError(
                            f'{name}: 字符串字面量不能赋给 {typ!r} 变量')
                    self.emit(f'{pad}{cname} = {self.expr(init)};')
            return
        if kind == 'block':
            for child in node[1]:
                self.stmt(child, indent)
            return
        if kind == 'if':
            self.emit(f'{pad}if ({self.expr(node[1])}) {{')
            self.stmt(node[2], indent + 1)
            if node[3] is not None:
                self.emit(f'{pad}}} else {{')
                self.stmt(node[3], indent + 1)
            self.emit(f'{pad}}}')
            return
        if kind == 'while':
            self.emit(f'{pad}while ({self.expr(node[1])}) {{')
            self.stmt(node[2], indent + 1)
            self.emit(f'{pad}}}')
            return
        if kind == 'for':
            init = self.simple_stmt(node[1]) if node[1] else ''
            cond = self.expr(node[2])
            step = self.simple_stmt(node[3]) if node[3] else ''
            self.emit(f'{pad}for ({init}; {cond}; {step}) {{')
            self.stmt(node[4], indent + 1)
            self.emit(f'{pad}}}')
            return
        if kind == 'break':
            self.emit(f'{pad}break;')
            return
        if kind == 'continue':
            self.emit(f'{pad}continue;')
            return
        if kind == 'return':
            value = node[1]
            self.emit(f'{pad}return {self.expr(value) if value else 0};')
            return
        if kind == 'expr':
            inner = node[1]
            if inner[0] == 'call' and inner[1] in ('print', 'println'):
                self.emit(f'{pad}{self.call(inner[1], inner[2])};')
                return
            if inner[0] == 'binop' and inner[1] == '=':
                self.emit(f'{pad}{self.assign(inner[2], self.expr(inner[3]))};')
                return
            if inner[0] == 'call':
                self.emit(f'{pad}{self.call(inner[1], inner[2])};')
                return
            raise NativeGenError(f'直接原生后端暂不支持表达式语句: {inner[0]!r}')
        raise NativeGenError(f'直接原生后端暂不支持语句: {kind!r}')

    # ---------- 函数 ----------

    def globals_block(self) -> None:
        for gv in self.globals:
            name = self._sanitize(gv.name)
            vtype = gv.vtype
            if isinstance(vtype, tuple) and vtype[0] == 'array':
                self.emit(f'static int64_t {name}[{vtype[2]}];')
            elif vtype == 'float':
                self.emit(f'static double {name} = 0.0;')
            elif vtype == 'string':
                self.emit(f'static const char *{name} = "";')
            elif isinstance(vtype, tuple) and vtype[0] == 'struct':
                self.emit(f'static cin_{vtype[1]} {name};')
            else:
                self.emit(f'static int64_t {name} = 0;')
        if self.globals:
            self.emit('')

    def globals_init(self) -> None:
        for gv in self.globals:
            vtype = gv.vtype
            key = self._sanitize(gv.name)
            if isinstance(vtype, tuple) and vtype[0] == 'struct':
                self.types[key] = 'struct:' + vtype[1]
            elif isinstance(vtype, tuple):
                self.types[key] = 'array'
            else:
                self.types[key] = vtype
        assigns: List[str] = []
        for gv in self.globals:
            if gv.init is None or gv.array_lit is not None:
                continue
            name = self._sanitize(gv.name)
            assigns.append(f'    {name} = {self.expr(gv.init)};')
        self.emit('static void __cin_init_globals(void) {')
        for line in assigns:
            self.emit(line)
        self.emit('}')
        self.emit('')

    def params_of(self, fdef: Any) -> List[str]:
        params: List[str] = []
        for pname, ptype in fdef.params:
            if isinstance(ptype, tuple) and ptype[0] == 'struct':
                if ptype[1] not in self.structs:
                    raise NativeGenError(f'未知 struct 类型: {ptype[1]!r}')
                ctype = f'cin_{ptype[1]}'
            elif isinstance(ptype, tuple) and ptype[0] == 'ptrarray':
                if ptype[1] == 'int':
                    ctype = 'int64_t *'
                elif ptype[1] == 'float':
                    ctype = 'double *'
                elif ptype[1] == 'string':
                    ctype = 'const char **'
                else:
                    raise NativeGenError(
                        f'数组参数元素类型 {ptype[1]!r} 暂不支持')
            elif ptype in ('int', 'bool', 'float', 'string'):
                ctype = 'double' if ptype == 'float' else (
                    'const char *' if ptype == 'string' else 'int64_t')
            else:
                raise NativeGenError(
                    f'{fdef.name}: 参数类型 {ptype!r} 暂不支持')
            params.append(f'{ctype} {self._sanitize(pname)}')
        return params

    def signature(self, fdef: Any) -> str:
        ret = _typename(fdef.ret_type)
        params = self.params_of(fdef)
        return (f'static {ret} {self._fname(fdef.name)}('
                f'{", ".join(params) if params else "void"})')

    def function(self, fdef: Any) -> None:
        ret = _typename(fdef.ret_type)
        param_names = {self._sanitize(p[0]) for p in fdef.params}
        self.vars = list(param_names)
        self.arrays = {}
        self.types = {}
        for _pname, _ptype in fdef.params:
            _key = self._sanitize(_pname)
            if isinstance(_ptype, tuple) and _ptype[0] == 'struct':
                self.types[_key] = 'struct:' + _ptype[1]
            elif isinstance(_ptype, tuple) and _ptype[0] == 'ptrarray':
                self.types[_key] = 'ptrarray:' + str(_ptype[1])
            else:
                self.types[_key] = _ptype
        body: List[str] = []
        saved = self.lines
        self.lines = body
        try:
            for stmt in fdef.body:
                self.stmt(stmt, 1)
        finally:
            self.lines = saved
        head = self.signature(fdef) + ' {\n'
        local = [v for v in self.vars if v not in param_names]
        decls = ''
        if local:
            parts = []
            for v in local:
                size = self.arrays.get(v)
                if size is not None:
                    base = str(self.types.get(v, 'array:int')).split(':', 1)[-1]
                    ctype = {'float': 'double', 'string': 'const char *',
                             'bool': 'int64_t'}.get(base, 'int64_t')
                    parts.append(f'{ctype} {v}[{size}] = {{0}};')
                elif self.types.get(v) == 'float':
                    parts.append(f'double {v} = 0.0;')
                elif self.types.get(v) == 'string':
                    parts.append(f'const char *{v} = "";')
                elif self.types.get(v) == 'ptrarray':
                    parts.append(f'int64_t *{v} = 0;')
                elif str(self.types.get(v, '')).startswith('struct:'):
                    name = self.types[v].split(':', 1)[1]
                    parts.append(f'cin_{name} {v} = {{0}};')
                else:
                    parts.append(f'int64_t {v} = 0;')
            decls = '    ' + ' '.join(parts) + '\n'
        self.emit(head + decls + '\n'.join(body) + '\n}\n')


def supports_source(source: str) -> bool:
    """该源码能否由直接原生后端编译（不抛异常）。"""
    try:
        emit_c(source)
        return True
    except NativeGenError:
        return False


def _strip_imports(text: str) -> str:
    """去掉 ``import`` 行（Parser 不认 import, 由调用方先展开闭包）。"""
    import re
    return re.sub(r'(?m)^[ \t]*import[ \t]+[^\n]*\n?', '', text)


def _parse_one(source: str):
    from .cin import Parser, tokenize
    return Parser(tokenize(_strip_imports(source))).parse_program()


def _merge(parsed_list):
    structs: Dict[str, Any] = {}
    functions: Dict[str, Any] = {}
    globals_: List[Any] = []
    seen_globals = set()
    for structs_i, globals_i, functions_i, _enums_i in parsed_list:
        for name, sd in structs_i.items():
            if name in structs:
                raise NativeGenError(f'struct 重复定义: {name}')
            structs[name] = sd
        for name, fd in functions_i.items():
            if name in functions:
                raise NativeGenError(f'函数重复定义: {name}')
            functions[name] = fd
        for gv in globals_i:
            if gv.extern:
                continue
            if gv.name in seen_globals:
                raise NativeGenError(f'全局变量重复定义: {gv.name}')
            seen_globals.add(gv.name)
            globals_.append(gv)
    return structs, functions, globals_


def emit_c(source: str, filename: Optional[str] = None,
           require_main: bool = True) -> str:
    """把 CIN 源码编译成 C 源码（不经过任何指令集）。

    ``filename`` 给出时会解析其 ``import`` 闭包并合并为同一编译单元。
    """
    from .cin import collect_imported_files

    parsed = []
    if filename:
        for path in collect_imported_files(filename):
            try:
                with open(path, encoding='utf-8-sig') as f:
                    text = f.read()
                parsed.append(_parse_one(text))
            except NativeGenError:
                raise
            except Exception as e:  # noqa: BLE001
                raise NativeGenError(f'{path}: 解析失败: {e}') from e
    if not parsed:
        try:
            parsed = [_parse_one(source)]
        except Exception as e:  # noqa: BLE001 - 统一转换为 NativeGenError
            raise NativeGenError(f'解析失败: {e}') from e

    structs, functions, globals_ = _merge(parsed)

    if 'main' not in functions:
        if require_main:
            raise NativeGenError('缺少 main 函数')
        main_fn = None
    else:
        main_fn = functions['main']
        if main_fn.ret_type not in (None, 'int', 'void'):
            raise NativeGenError('main 必须返回 int 或 void')

    gen = _Generator(functions, structs, globals_)
    gen.emit(_PREAMBLE)
    for sd in (structs or {}).values():
        fields = ' '.join(f'{_field_ctype(t)} {n};' for n, t in sd.fields)
        gen.emit(f'typedef struct {{ {fields} }} cin_{sd.name};')
    if structs:
        gen.emit('')
    gen.globals_block()
    for fdef in functions.values():
        gen.emit(gen.signature(fdef) + ';')
    gen.emit('')
    gen.globals_init()
    for fdef in functions.values():
        gen.function(fdef)
    if main_fn is not None:
        gen.emit('int main(void) {')
        gen.emit('    __cin_init_globals();')
        if main_fn.ret_type == 'void':
            gen.emit(f'    {_Generator._fname("main")}();')
            gen.emit('    return 0;')
        else:
            gen.emit(f'    int64_t rc = {_Generator._fname("main")}();')
            gen.emit('    return (int)rc;')
        gen.emit('}')
    return '\n'.join(gen.lines) + '\n'


def compile_native(source: str, out: str, *,
                   filename: Optional[str] = None,
                   target: Optional[str] = None,
                   optimize: int = 2,
                   keep_c: bool = False) -> str:
    """源码 → C → 设备本机可执行文件; 返回产物路径。

    ``filename`` 给出时会编译其 ``import`` 闭包（多文件项目）。
    """
    from .cbackend import CBackendError, c_target, _host_target_name

    c_source = emit_c(source, filename=filename)
    target_name = target or _host_target_name()
    spec = toolchain.get_target(target_name)
    info = toolchain.find_compiler(spec)
    zig = None if info is not None else toolchain.find_zig()
    if info is None and zig is None:
        raise toolchain.ToolchainError(toolchain.hint(spec))
    out = os.path.abspath(out)
    if spec.exe and not out.lower().endswith(spec.exe):
        out += spec.exe
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='.nativegen-')
    try:
        c_path = os.path.join(workdir, 'program.c')
        with open(c_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(c_source)
        if keep_c:
            import shutil
            shutil.copyfile(c_path, out + '.c')
        if info is not None:
            cmd = [info.path, f'-O{optimize}', '-std=c11', '-o', out, c_path]
        else:
            cmd = [zig, 'cc', '-target', spec.triple, f'-O{optimize}',
                   '-std=c11', '-o', out, c_path]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=600, check=False)
        if result.returncode != 0:
            raise NativeGenError(
                f'C 编译失败:\n{(result.stderr or result.stdout).strip()[:2000]}')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    return out
