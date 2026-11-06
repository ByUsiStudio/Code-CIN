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
           '<': '<', '<=': '<=', '>': '>', '>=': '>=', '==': '==', '!=': '!='}
_PREAMBLE = ('#include <stdint.h>\n#include <stdio.h>\n\n'
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
             'printf("%s\\n", s); }\n\n')


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
    if t == 'int':
        return 'int64_t'
    if t == 'void':
        return 'void'
    raise NativeGenError(f'直接原生后端暂不支持类型: {t!r}')


class _Generator:
    def __init__(self, functions: Dict[str, Any]):
        self.functions = functions
        self.lines: List[str] = []
        self.vars: List[str] = []
        self.types: Dict[str, str] = {}
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
                raise NativeGenError('直接原生后端暂不支持浮点字面量')
            return f'INT64_C({int(value)})'
        if kind == 'var':
            return self._sanitize(node[1])
        if kind == 'neg':
            return f'(-{self.expr(node[1])})'
        if kind == 'binop':
            op = node[1]
            if op in _BINOPS:
                return (f'({self.expr(node[2])} {_BINOPS[op]} '
                        f'{self.expr(node[3])})')
            raise NativeGenError(f'直接原生后端暂不支持运算符: {op!r}')
        if kind == 'str':
            raise NativeGenError('字符串字面量目前只能直接用于 print/println')
        if kind == 'call':
            return self.call(node[1], node[2])
        raise NativeGenError(f'直接原生后端暂不支持表达式节点: {kind!r}')

    def call(self, name: str, args: Sequence[AST]) -> str:
        if name in ('print', 'println'):
            if len(args) != 1:
                raise NativeGenError(f'{name}() 需要恰好 1 个参数')
            arg = args[0]
            if arg[0] == 'str':
                fn = '__cin_println_str' if name == 'println' \
                    else '__cin_print_str'
                return f'{fn}({_c_string(arg[1])})'
            if arg[0] == 'var' and self.types.get(arg[1]) == 'bool':
                fn = '__cin_println_bool' if name == 'println' \
                    else '__cin_print_bool'
                return f'{fn}({self.expr(arg)})'
            fn = '__cin_println_int' if name == 'println' else '__cin_print_int'
            return f'{fn}({self.expr(arg)})'
        if name not in self.functions:
            raise NativeGenError(f'直接原生后端暂不支持内建/外部函数: {name!r}')
        callee = self.functions[name]
        if len(callee.params) != len(args):
            raise NativeGenError(f'{name}() 参数数量不匹配')
        rendered = ', '.join(self.expr(a) for a in args)
        return f'{self._fname(name)}({rendered})'

    # ---------- 语句 ----------

    def stmt(self, node: AST, indent: int) -> None:
        pad = '    ' * indent
        kind = node[0]
        if kind == 'decl':
            for name, typ, init, _extra in node[1]:
                if typ not in ('int', 'bool'):
                    raise NativeGenError(f'直接原生后端暂不支持声明类型: {typ!r}')
                cname = self._sanitize(name)
                if cname not in self.vars:
                    self.vars.append(cname)
                self.types[cname] = 'bool' if typ == 'bool' else 'int'
                if init is None:
                    self.emit(f'{pad}{cname} = 0;')
                else:
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
                target = inner[2]
                if target[0] != 'var':
                    raise NativeGenError('直接原生后端暂不支持非变量赋值')
                cname = self._sanitize(target[1])
                if cname not in self.vars:
                    self.vars.append(cname)
                self.emit(f'{pad}{cname} = {self.expr(inner[3])};')
                return
            if inner[0] == 'call':
                self.emit(f'{pad}{self.call(inner[1], inner[2])};')
                return
            raise NativeGenError(f'直接原生后端暂不支持表达式语句: {inner[0]!r}')
        raise NativeGenError(f'直接原生后端暂不支持语句: {kind!r}')

    # ---------- 函数 ----------

    def function(self, fdef: Any) -> None:
        ret = _typename(fdef.ret_type)
        params = []
        for pname, ptype in fdef.params:
            if ptype not in ('int', 'bool'):
                raise NativeGenError(f'{fdef.name}: 参数类型 {ptype!r} 暂不支持')
            params.append(f'int64_t {self._sanitize(pname)}')
        param_names = {self._sanitize(p[0]) for p in fdef.params}
        self.vars = list(param_names)
        self.types = {self._sanitize(p[0]):
                      ('bool' if p[1] == 'bool' else 'int')
                      for p in fdef.params}
        body: List[str] = []
        saved = self.lines
        self.lines = body
        try:
            for stmt in fdef.body:
                self.stmt(stmt, 1)
        finally:
            self.lines = saved
        head = (f'static {ret} {self._fname(fdef.name)}('
                f'{", ".join(params) if params else "void"}) {{\n')
        local = [v for v in self.vars if v not in param_names]
        decls = ''
        if local:
            decls = '    ' + ' '.join(f'int64_t {v} = 0;' for v in local) + '\n'
        self.emit(head + decls + '\n'.join(body) + '\n}\n')


def supports_source(source: str) -> bool:
    """该源码能否由直接原生后端编译（不抛异常）。"""
    try:
        emit_c(source)
        return True
    except NativeGenError:
        return False


def emit_c(source: str) -> str:
    """把 CIN 源码编译成 C 源码（不经过任何指令集）。"""
    from .cin import Parser, tokenize

    try:
        _structs, _globals, functions, _enums = Parser(
            tokenize(source)).parse_program()
    except Exception as e:  # noqa: BLE001 - 统一转换为 NativeGenError
        raise NativeGenError(f'解析失败: {e}') from e

    if 'main' not in functions:
        raise NativeGenError('缺少 main 函数')
    main_fn = functions['main']
    if main_fn.ret_type not in (None, 'int', 'void'):
        raise NativeGenError('main 必须返回 int 或 void')

    gen = _Generator(functions)
    gen.emit(_PREAMBLE)
    for fdef in functions.values():
        gen.function(fdef)
    gen.emit('int main(void) {')
    if main_fn.ret_type == 'void':
        gen.emit(f'    {_Generator._fname("main")}();')
        gen.emit('    return 0;')
    else:
        gen.emit(f'    int64_t rc = {_Generator._fname("main")}();')
        gen.emit('    return (int)rc;')
    gen.emit('}')
    return '\n'.join(gen.lines) + '\n'


def compile_native(source: str, out: str, *,
                   target: Optional[str] = None,
                   optimize: int = 2,
                   keep_c: bool = False) -> str:
    """源码 → C → 设备本机可执行文件; 返回产物路径。"""
    from .cbackend import CBackendError, c_target, _host_target_name

    c_source = emit_c(source)
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
