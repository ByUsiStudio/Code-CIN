"""最小 TOML 读写器 (零依赖, 覆盖工具链所需的 TOML 子集)。

为什么不用 ``tomllib``: 项目 ``requires-python >= 3.8``, 而 ``tomllib`` 从
Python 3.11 才进入标准库。本模块提供**够用的**子集:

* ``[table]`` / ``[a.b]`` / ``[[array-of-tables]]``;
* 基本字符串、字面量字符串、多行字符串 (``\"\"\"`` / ``'''``);
* 整数 (含 0x/0o/0b 与下划线)、浮点、布尔、数组、内联表;
* 注释与行尾注释、点号键、带引号的键。

不支持的 TOML 特性 (日期时间会按 ISO 字符串保留) 在写入时不会被生成。
对外只暴露 :func:`loads` / :func:`dumps` 与 :class:`TomlError`。
"""

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

__all__ = ['TomlError', 'loads', 'dumps']


class TomlError(ValueError):
    """TOML 语法错误 (带行号提示)。"""


_BARE_KEY_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_NUMBER_RE = re.compile(
    r'^[+-]?(?:0[xX][0-9A-Fa-f_]+|0[oO][0-7_]+|0[bB][01_]+'
    r'|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?)$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(?:[Tt ].*)?$')
_ESCAPES = {
    'b': '\b', 't': '\t', 'n': '\n', 'f': '\f', 'r': '\r',
    '"': '"', '\\': '\\', '/': '/',
}


def _line_of(text: str, pos: int) -> int:
    return text.count('\n', 0, pos) + 1


def _logical_statements(text: str) -> List[Tuple[str, int]]:
    """把源码切成逻辑语句 (表头 / 键值), 去掉注释并保留行号。"""
    if text.startswith('\ufeff'):
        text = text[1:]
    out: List[Tuple[str, int]] = []
    buf: List[str] = []
    start_line = 1
    line = 1
    quote: Optional[str] = None
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if quote == '"' and ch == '\\':
                buf.append(ch)
                i += 1
                if i < n:
                    buf.append(text[i])
                    if text[i] == '\n':
                        line += 1
                    i += 1
                continue
            if text.startswith(quote, i):
                buf.append(quote)
                i += len(quote)
                quote = None
                continue
            buf.append(ch)
            if ch == '\n':
                line += 1
            i += 1
            continue

        if ch == '#':
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if ch in ('"', "'"):
            triple = ch * 3
            if text.startswith(triple, i):
                quote = triple
                buf.append(triple)
                i += 3
                continue
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth = max(0, depth - 1)
        if ch in '\r\n':
            if ch == '\r' and i + 1 < n and text[i + 1] == '\n':
                i += 1
            raw = ''.join(buf).strip()
            if depth == 0 and raw:
                out.append((raw, start_line))
                buf = []
                start_line = line + 1
            else:
                buf.append('\n')
            line += 1
            i += 1
            continue
        if not buf:
            start_line = line
        buf.append(ch)
        i += 1
    if quote:
        raise TomlError(f'未闭合的字符串 (从第 {start_line} 行开始)')
    raw = ''.join(buf).strip()
    if raw:
        out.append((raw, start_line))
    return out


def _split_key_path(text: str, line: int) -> List[str]:
    """解析 ``a.b."c d"`` 形式的键路径。"""
    parts: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == '.':
            part = ''.join(buf).strip()
            if not part:
                raise TomlError(f'第 {line} 行: 空键')
            parts.append(part)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    part = ''.join(buf).strip()
    if not part:
        raise TomlError(f'第 {line} 行: 空键')
    parts.append(part)
    return parts


def _find_equals(text: str, line: int) -> int:
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == '=':
            return i
        i += 1
    raise TomlError(f'第 {line} 行: 缺少 "="')


class _ValueParser:
    def __init__(self, text: str, line: int):
        self.text = text
        self.line = line
        self.pos = 0

    def error(self, message: str) -> TomlError:
        return TomlError(f'第 {self.line} 行: {message} (在 {self.text!r})')

    def skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def parse(self) -> Any:
        self.skip()
        value = self.value()
        self.skip()
        if self.pos != len(self.text):
            raise self.error(f'值后面有多余内容 {self.text[self.pos:]!r}')
        return value

    def value(self) -> Any:
        self.skip()
        if self.pos >= len(self.text):
            raise self.error('缺少值')
        ch = self.text[self.pos]
        if ch == '"' or ch == "'":
            return self.string()
        if ch == '[':
            return self.array()
        if ch == '{':
            return self.inline_table()
        return self.scalar()

    def string(self) -> str:
        quote = self.text[self.pos]
        triple = quote * 3
        if self.text.startswith(triple, self.pos):
            return self.multiline(triple)
        self.pos += 1
        out: List[str] = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == quote:
                self.pos += 1
                return ''.join(out)
            if quote == '"' and ch == '\\':
                out.append(self.escape())
                continue
            out.append(ch)
            self.pos += 1
        raise self.error('未闭合的字符串')

    def multiline(self, triple: str) -> str:
        self.pos += 3
        # TOML 允许紧跟在开定界符后的一个换行不进入内容
        if self.text.startswith('\r\n', self.pos):
            self.pos += 2
        elif self.text.startswith('\n', self.pos):
            self.pos += 1
        out: List[str] = []
        while self.pos < len(self.text):
            if self.text.startswith(triple, self.pos):
                self.pos += 3
                return ''.join(out)
            ch = self.text[self.pos]
            if triple == '"""' and ch == '\\':
                out.append(self.escape())
                continue
            out.append(ch)
            self.pos += 1
        raise self.error('未闭合的多行字符串')

    def escape(self) -> str:
        self.pos += 1
        if self.pos >= len(self.text):
            raise self.error('反斜杠后缺少转义字符')
        ch = self.text[self.pos]
        self.pos += 1
        if ch in _ESCAPES:
            return _ESCAPES[ch]
        if ch in ('u', 'U'):
            length = 4 if ch == 'u' else 8
            raw = self.text[self.pos:self.pos + length]
            if len(raw) != length or not re.fullmatch(r'[0-9A-Fa-f]+', raw):
                raise self.error('非法 Unicode 转义')
            self.pos += length
            try:
                return chr(int(raw, 16))
            except ValueError:
                raise self.error('非法 Unicode 码点') from None
        raise self.error(f'未知转义 \\{ch}')

    def array(self) -> List[Any]:
        self.pos += 1
        out: List[Any] = []
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise self.error('未闭合的数组')
            if self.text[self.pos] == ']':
                self.pos += 1
                return out
            out.append(self.value())
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ',':
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == ']':
                self.pos += 1
                return out
            raise self.error('数组元素之间缺少逗号或 ]')

    def inline_table(self) -> Dict[str, Any]:
        self.pos += 1
        out: Dict[str, Any] = {}
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise self.error('未闭合的内联表')
            if self.text[self.pos] == '}':
                self.pos += 1
                return out
            start = self.pos
            quote: Optional[str] = None
            while self.pos < len(self.text):
                ch = self.text[self.pos]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in ('"', "'"):
                    quote = ch
                elif ch == '=':
                    break
                elif ch in (',', '}'):
                    raise self.error('内联表缺少 "="')
                self.pos += 1
            key_text = self.text[start:self.pos].strip()
            if not key_text:
                raise self.error('内联表空键')
            self.pos += 1  # '='
            keys = _split_key_path(key_text, self.line)
            value = self.value()
            target = out
            for key in keys[:-1]:
                target = target.setdefault(key, {})
                if not isinstance(target, dict):
                    raise self.error(f'键 {key!r} 与已有值类型冲突')
            if keys[-1] in target:
                raise self.error(f'重复键 {keys[-1]!r}')
            target[keys[-1]] = value
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ',':
                self.pos += 1
                continue
            if self.pos < len(self.text) and self.text[self.pos] == '}':
                self.pos += 1
                return out
            raise self.error('内联表元素之间缺少逗号或 }')

    def scalar(self) -> Any:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in ',]}':
            self.pos += 1
        raw = self.text[start:self.pos].strip()
        if not raw:
            raise self.error('缺少值')
        low = raw.lower()
        if low == 'true':
            return True
        if low == 'false':
            return False
        if low in ('inf', '+inf'):
            return float('inf')
        if low == '-inf':
            return float('-inf')
        if low in ('nan', '+nan', '-nan'):
            return float('nan')
        if _DATE_RE.match(raw):
            return raw
        if _NUMBER_RE.match(raw):
            cleaned = raw.replace('_', '')
            try:
                if re.match(r'^[+-]?0[xX]', cleaned):
                    return int(cleaned, 16)
                if re.match(r'^[+-]?0[oO]', cleaned):
                    return int(cleaned, 8)
                if re.match(r'^[+-]?0[bB]', cleaned):
                    return int(cleaned, 2)
                if any(c in cleaned for c in '.eE'):
                    return float(cleaned)
                return int(cleaned)
            except ValueError:
                raise self.error(f'非法数字 {raw!r}') from None
        raise self.error(f'非法值 {raw!r} (字符串请加引号)')


def _assign(root: Dict[str, Any], keys: List[str], value: Any, line: int) -> None:
    target = root
    for key in keys[:-1]:
        nxt = target.get(key)
        if nxt is None:
            nxt = {}
            target[key] = nxt
        if isinstance(nxt, list):
            if not nxt or not isinstance(nxt[-1], dict):
                raise TomlError(f'第 {line} 行: 键 {key!r} 不是表')
            nxt = nxt[-1]
        if not isinstance(nxt, dict):
            raise TomlError(f'第 {line} 行: 键 {key!r} 已存在且不是表')
        target = nxt
    last = keys[-1]
    if last in target:
        raise TomlError(f'第 {line} 行: 重复键 {last!r}')
    target[last] = value


def _table_at(root: Dict[str, Any], keys: List[str], line: int,
              array: bool) -> Dict[str, Any]:
    target = root
    for key in keys[:-1]:
        nxt = target.get(key)
        if nxt is None:
            nxt = {}
            target[key] = nxt
        if isinstance(nxt, list):
            if not nxt or not isinstance(nxt[-1], dict):
                raise TomlError(f'第 {line} 行: 键 {key!r} 不是表')
            nxt = nxt[-1]
        if not isinstance(nxt, dict):
            raise TomlError(f'第 {line} 行: 键 {key!r} 已存在且不是表')
        target = nxt
    last = keys[-1]
    if array:
        bucket = target.setdefault(last, [])
        if not isinstance(bucket, list):
            raise TomlError(f'第 {line} 行: 键 {last!r} 已存在且不是表数组')
        entry: Dict[str, Any] = {}
        bucket.append(entry)
        return entry
    nxt = target.get(last)
    if nxt is None:
        nxt = {}
        target[last] = nxt
    if not isinstance(nxt, dict):
        raise TomlError(f'第 {line} 行: 键 {last!r} 已存在且不是表')
    return nxt


def loads(text: str) -> Dict[str, Any]:
    """解析 TOML 文本为 dict; 语法错误抛 :class:`TomlError`。"""
    root: Dict[str, Any] = {}
    current: Dict[str, Any] = root
    for statement, line in _logical_statements(text):
        if statement.startswith('[['):
            if not statement.endswith(']]'):
                raise TomlError(f'第 {line} 行: 表数组头部格式错误')
            keys = _split_key_path(statement[2:-2].strip(), line)
            current = _table_at(root, keys, line, array=True)
            continue
        if statement.startswith('['):
            if not statement.endswith(']'):
                raise TomlError(f'第 {line} 行: 表头部格式错误')
            keys = _split_key_path(statement[1:-1].strip(), line)
            current = _table_at(root, keys, line, array=False)
            continue
        eq = _find_equals(statement, line)
        keys = _split_key_path(statement[:eq].strip(), line)
        value = _ValueParser(statement[eq + 1:].strip(), line).parse()
        _assign(current, keys, value, line)
    return root


def load(path: str) -> Dict[str, Any]:
    """从 UTF-8 文件读取 TOML (容忍 BOM 与 CRLF)。"""
    with open(path, encoding='utf-8-sig') as f:
        return loads(f.read())


# ==================== 写入 ====================


def _format_key(key: str) -> str:
    return key if _BARE_KEY_RE.match(key) else json.dumps(key, ensure_ascii=False)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return 'nan'
        if value == float('inf'):
            return 'inf'
        if value == float('-inf'):
            return '-inf'
        return repr(value)
    if isinstance(value, (datetime, date)):
        return json.dumps(value.isoformat(), ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_format_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(
            f'{_format_key(k)} = {_format_value(v)}' for k, v in value.items()
        ) + '}'
    raise TypeError(f'无法写入 TOML 的类型: {type(value).__name__}')


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(v, dict) for v in value)


def _dump_table(data: Dict[str, Any], path: List[str], out: List[str],
                header: bool, sort_keys: bool) -> None:
    items = sorted(data.items()) if sort_keys else list(data.items())
    scalars = [(k, v) for k, v in items
               if not isinstance(v, dict) and not _is_table_array(v)]
    tables = [(k, v) for k, v in items if isinstance(v, dict)]
    arrays = [(k, v) for k, v in items if _is_table_array(v)]

    if header:
        if out and out[-1] != '':
            out.append('')
        out.append('[' + '.'.join(_format_key(p) for p in path) + ']')
    for key, value in scalars:
        out.append(f'{_format_key(key)} = {_format_value(value)}')
    for key, value in tables:
        _dump_table(value, path + [key], out, header=True, sort_keys=sort_keys)
    for key, values in arrays:
        for value in values:
            if out and out[-1] != '':
                out.append('')
            out.append('[[' + '.'.join(
                _format_key(p) for p in path + [key]) + ']]')
            _dump_table(value, path + [key], out, header=False,
                        sort_keys=sort_keys)


def dumps(data: Dict[str, Any], sort_keys: bool = False) -> str:
    """把 dict 写为 TOML 文本 (末尾带一个换行)。"""
    if not isinstance(data, dict):
        raise TypeError('TOML 顶层必须是 dict')
    out: List[str] = []
    # 顶层标量先写, 再写表; _dump_table(header=False) 正好如此
    top_scalars = [(k, v) for k, v in data.items()
                   if not isinstance(v, dict) and not _is_table_array(v)]
    for key, value in top_scalars:
        out.append(f'{_format_key(key)} = {_format_value(value)}')
    for key, value in data.items():
        if isinstance(value, dict):
            _dump_table(value, [key], out, header=True, sort_keys=sort_keys)
        elif _is_table_array(value):
            for entry in value:
                if out and out[-1] != '':
                    out.append('')
                out.append('[[' + _format_key(key) + ']]')
                _dump_table(entry, [key], out, header=False,
                            sort_keys=sort_keys)
    text = '\n'.join(out).strip() + '\n'
    return text if text.strip() else ''
