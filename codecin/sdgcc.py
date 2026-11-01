"""``.sdgcc`` —— Code CIN 编译产物容器 (BIN v3 的带元数据封装)。

自 v6.5 起 ``.sdgcc`` 是**默认产物**: 它把一份完整的 BIN v3 字节流放在
``CODE`` 段里, 再附加元数据、符号与源码清单。这样做的目的:

* **向后兼容**: ``CODE`` 段逐字节等于旧 ``.bin``, 任何只认 BIN v3 的读取器
  都可以直接消费; ``.bin`` 也仍是**合法输入**, :func:`unwrap` 会原样透传。
* **可自描述**: 单文件/多文件模式、目标平台、入口、优化级别、依赖文件都在
  文件内, 不再靠文件名或旁挂 JSON 猜测。
* **零依赖解析**: 固定 28 字节头 + 24 字节/段的表, 顺序读一遍即可, 不需要
  zip/TOML 解析器参与装载 (元数据按需解析)。

二进制布局 (小端)::

    offset  size    field
    0       5       magic  b'SDGCC'
    5       1       version (当前 1)
    6       2       flags  (bit0 多文件, bit1 含符号表, bit2 含源码清单)
    8       16      target (ASCII, NUL 填充; 'host' = 构建机)
    24      4       section count N
    28      24*N    section table: name[8] | offset u64 | size u64
    ...             payload

段名: ``CODE`` (必需, BIN v3), ``META`` (TOML), ``SYMS``, ``SRCS``。
"""

import os
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .isa import Constants

__all__ = [
    'SDGCC_MAGIC',
    'SDGCC_VERSION',
    'Sdgcc',
    'SdgccError',
    'build',
    'is_sdgcc',
    'load',
    'parse',
    'read_code',
    'save',
    'save_program',
    'unwrap',
]

#: 容器 magic (5 字节, 与 BIN 的 ``CPUSA`` 等长, 便于魔数嗅探)。
SDGCC_MAGIC = b'SDGCC'
#: 容器版本。
SDGCC_VERSION = 1

#: 单文件模式 / 多文件模式。
MODE_SINGLE = 'single'
MODE_MULTI = 'multi'

FLAG_MULTI_FILE = 0x0001
FLAG_SYMBOLS = 0x0002
FLAG_SOURCES = 0x0004

TARGET_HOST = 'host'

SECTION_CODE = 'CODE'
SECTION_META = 'META'
SECTION_SYMS = 'SYMS'
SECTION_SRCS = 'SRCS'

_FIXED = struct.Struct('<5sBH16s')       # magic, version, flags, target[16]
_COUNT = struct.Struct('<I')
_ENTRY = struct.Struct('<8sQQ')          # name, offset, size

HEADER_SIZE = _FIXED.size + _COUNT.size  # 28
ENTRY_SIZE = _ENTRY.size                 # 24


class SdgccError(ValueError):
    """``.sdgcc`` 容器损坏/版本不支持。"""


@dataclass
class Sdgcc:
    """解析后的 ``.sdgcc`` 容器。"""

    version: int
    flags: int
    target: str
    sections: Dict[str, bytes] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def multi_file(self) -> bool:
        return bool(self.flags & FLAG_MULTI_FILE)

    @property
    def code(self) -> bytes:
        return self.sections[SECTION_CODE]

    @property
    def symbols(self) -> List[Tuple[int, str]]:
        raw = self.sections.get(SECTION_SYMS)
        if not raw:
            return []
        return _decode_symbols(raw)

    @property
    def sources(self) -> List[str]:
        raw = self.sections.get(SECTION_SRCS)
        if not raw:
            return []
        return _decode_sources(raw)

    @property
    def size(self) -> int:
        return len(self.code)

    def to_dict(self) -> Dict[str, Any]:
        build_info = self.meta.get('build') or {}
        return {
            'format': self.version,
            'mode': MODE_MULTI if self.multi_file else MODE_SINGLE,
            'target': self.target,
            'symbols': len(self.symbols),
            'sources': list(self.sources),
            'code': self.size,
            'entry': build_info.get('entry'),
            'optimize': build_info.get('optimize'),
            'mem_size': build_info.get('mem_size'),
            'bin_version': build_info.get('bin_version'),
            'bytecode_len': build_info.get('bytecode_len'),
        }


# ==================== 编码 ====================


def _pad_name(name: str) -> bytes:
    raw = name.encode('ascii', 'replace')[:8]
    return raw + b'\x00' * (8 - len(raw))


def _encode_symbols(symbols: Iterable[Tuple[int, str]]) -> bytes:
    items = sorted(((int(pc), str(name)) for pc, name in symbols),
                   key=lambda item: item[0])
    out = [_COUNT.pack(len(items))]
    for pc, name in items:
        raw = name.encode('utf-8')
        out.append(struct.pack('<IH', pc, len(raw)))
        out.append(raw)
    return b''.join(out)


def _decode_symbols(raw: bytes) -> List[Tuple[int, str]]:
    if len(raw) < 4:
        raise SdgccError('SYMS 段不完整')
    count = struct.unpack_from('<I', raw, 0)[0]
    offset = 4
    out: List[Tuple[int, str]] = []
    for _ in range(count):
        if offset + 6 > len(raw):
            raise SdgccError('SYMS 段被截断')
        pc, length = struct.unpack_from('<IH', raw, offset)
        offset += 6
        if offset + length > len(raw):
            raise SdgccError('SYMS 段符号名被截断')
        out.append((pc, raw[offset:offset + length].decode('utf-8', 'replace')))
        offset += length
    return out


def _encode_sources(sources: Sequence[str]) -> bytes:
    items = [str(s) for s in sources]
    out = [_COUNT.pack(len(items))]
    for item in items:
        raw = item.encode('utf-8')
        if len(raw) > 0xFFFF:
            raise SdgccError(f'源码路径过长: {item!r}')
        out.append(struct.pack('<H', len(raw)))
        out.append(raw)
    return b''.join(out)


def _decode_sources(raw: bytes) -> List[str]:
    if len(raw) < 4:
        raise SdgccError('SRCS 段不完整')
    count = struct.unpack_from('<I', raw, 0)[0]
    offset = 4
    out: List[str] = []
    for _ in range(count):
        if offset + 2 > len(raw):
            raise SdgccError('SRCS 段被截断')
        length = struct.unpack_from('<H', raw, offset)[0]
        offset += 2
        if offset + length > len(raw):
            raise SdgccError('SRCS 段路径被截断')
        out.append(raw[offset:offset + length].decode('utf-8', 'replace'))
        offset += length
    return out


def _default_meta(target: str, multi_file: bool) -> Dict[str, Any]:
    return {
        'sdgcc': {
            'format': SDGCC_VERSION,
            'mode': MODE_MULTI if multi_file else MODE_SINGLE,
            'target': target,
            'created': datetime.now(timezone.utc)
            .replace(microsecond=0).isoformat(),
        },
    }


def _dumps_meta(meta: Dict[str, Any]) -> bytes:
    from .packaging.toml import dumps
    return dumps(meta).encode('utf-8')


def _loads_meta(raw: bytes) -> Dict[str, Any]:
    from .packaging.toml import loads
    try:
        data = loads(raw.decode('utf-8'))
    except Exception as e:  # noqa: BLE001 - 统一转换为 SdgccError
        raise SdgccError(f'META 段不是合法 TOML: {e}') from e
    if not isinstance(data, dict):
        raise SdgccError('META 段顶层必须是表')
    return data


def build(code: bytes,
          *,
          target: str = TARGET_HOST,
          multi_file: bool = True,
          meta: Optional[Dict[str, Any]] = None,
          symbols: Optional[Iterable[Tuple[int, str]]] = None,
          sources: Optional[Sequence[str]] = None,
          version: int = SDGCC_VERSION) -> bytes:
    """把 BIN v3 字节流封装为 ``.sdgcc``。

    ``code`` 必须是完整 BIN v3 镜像 (以 ``CPUSA`` 开头); 传入 ``.bin`` 原始
    字节即可, 容器不会改动其内容。
    """
    if not isinstance(code, (bytes, bytearray, memoryview)):
        raise SdgccError('code 必须是 bytes')
    code = bytes(code)
    if len(code) < 6 or code[:5] != Constants.MAGIC_NUMBER:
        raise SdgccError('CODE 必须以 BIN magic (CPUSA) 开头')
    if code[5] not in (Constants.BIN_VERSION, Constants.BIN_VERSION_V2):
        raise SdgccError(f'CODE 以不支持的 BIN 版本开头: {code[5]}')

    payload: List[Tuple[str, bytes]] = [(SECTION_CODE, code)]
    info = dict(meta or _default_meta(target, multi_file))
    info.setdefault('sdgcc', {})
    if not isinstance(info.get('sdgcc'), dict):
        raise SdgccError('meta[sdgcc] 必须是表')
    info['sdgcc'].setdefault('format', version)
    info['sdgcc']['mode'] = MODE_MULTI if multi_file else MODE_SINGLE
    info['sdgcc'].setdefault('target', target)
    payload.append((SECTION_META, _dumps_meta(info)))

    flags = FLAG_MULTI_FILE if multi_file else 0
    if symbols:
        payload.append((SECTION_SYMS, _encode_symbols(symbols)))
        flags |= FLAG_SYMBOLS
    if sources:
        payload.append((SECTION_SRCS, _encode_sources(sources)))
        flags |= FLAG_SOURCES

    table_size = HEADER_SIZE + ENTRY_SIZE * len(payload)
    offset = table_size
    table: List[bytes] = []
    body = bytearray()
    for name, data in payload:
        table.append(_ENTRY.pack(_pad_name(name), offset, len(data)))
        body += data
        offset += len(data)
    header = (_FIXED.pack(SDGCC_MAGIC, version, flags,
                          target.encode('ascii', 'replace')[:16].ljust(16, b'\x00'))
              + _COUNT.pack(len(payload)))
    return header + b''.join(table) + bytes(body)


# ==================== 解码 ====================


def is_sdgcc(data: bytes) -> bool:
    return len(data) >= 5 and data[:5] == SDGCC_MAGIC


def parse(data: bytes) -> Sdgcc:
    """解析 ``.sdgcc`` 字节流并做结构校验。"""
    if not is_sdgcc(data):
        raise SdgccError('不是 .sdgcc (magic 不匹配)')
    if len(data) < HEADER_SIZE:
        raise SdgccError('.sdgcc 头部不完整')
    magic, version, flags, raw_target = _FIXED.unpack_from(data, 0)
    if version > SDGCC_VERSION or version == 0:
        raise SdgccError(f'不支持的 .sdgcc 版本: {version}')
    count = _COUNT.unpack_from(data, HEADER_SIZE - _COUNT.size)[0]
    if count == 0 or count > 64:
        raise SdgccError(f'段表数量异常: {count}')

    table_end = HEADER_SIZE + ENTRY_SIZE * count
    if table_end > len(data):
        raise SdgccError('段表被截断')

    sections: Dict[str, bytes] = {}
    order: List[Tuple[int, int]] = []
    for index in range(count):
        raw_name, offset, size = _ENTRY.unpack_from(
            data, HEADER_SIZE + ENTRY_SIZE * index)
        name = raw_name.rstrip(b'\x00').decode('ascii', 'replace')
        if not name:
            raise SdgccError(f'第 {index} 段缺少名字')
        if name in sections:
            raise SdgccError(f'重复的段名: {name}')
        if offset < table_end or offset + size > len(data):
            raise SdgccError(f'段 {name} 越界 (offset={offset}, size={size})')
        sections[name] = data[offset:offset + size]
        order.append((offset, offset + size))
    order.sort()
    for (start, end), (next_start, _next_end) in zip(order, order[1:]):
        if next_start < end:
            raise SdgccError('段区间重叠')

    code = sections.get(SECTION_CODE)
    if code is None:
        raise SdgccError('缺少 CODE 段')
    if len(code) < 6 or code[:5] != Constants.MAGIC_NUMBER:
        raise SdgccError('CODE 段不是 BIN 字节码 (magic 不匹配)')

    meta = _loads_meta(sections[SECTION_META]) if SECTION_META in sections else {}
    target = raw_target.rstrip(b'\x00').decode('ascii', 'replace') or TARGET_HOST
    container = Sdgcc(version=version, flags=flags, target=target,
                      sections=sections, meta=meta)
    # 段表标志与 META 不一致时以段表为准, 但 META 必须能解析 (上面已校验)。
    if not container.multi_file and container.meta.get('sdgcc', {}).get(
            'mode') == MODE_MULTI:
        container.flags |= FLAG_MULTI_FILE
    return container


def load(path: str) -> Sdgcc:
    with open(path, 'rb') as f:
        return parse(f.read())


def save(path: str, code: bytes, **kwargs: Any) -> str:
    data = build(code, **kwargs)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return os.path.abspath(path)


def unwrap(data: bytes) -> Tuple[bytes, Optional[Sdgcc]]:
    """取出 BIN 字节流; ``.sdgcc`` 会解包, 旧 ``.bin`` 原样返回。

    返回 ``(code, container)``, ``container`` 在输入是裸 ``.bin`` 时为 ``None``。
    """
    if is_sdgcc(data):
        container = parse(data)
        return container.code, container
    return data, None


def read_code(path: str) -> bytes:
    """读取 ``.sdgcc`` / ``.bin`` 并返回其中的 BIN v3 字节流。"""
    with open(path, 'rb') as f:
        data = f.read()
    code, _ = unwrap(data)
    return code


# ==================== 与 CPU 对接 ====================


def save_program(cpu: 'Any', path: str, *,
                 target: str = TARGET_HOST,
                 multi_file: bool = True,
                 meta: Optional[Dict[str, Any]] = None,
                 symbols: Optional[Iterable[Tuple[int, str]]] = None,
                 sources: Optional[Sequence[str]] = None) -> str:
    """把已编译的 CPU 状态写成 ``.sdgcc`` (路径以 ``.bin`` 结尾时写裸 BIN)。"""
    from . import crom as crom_mod
    code = crom_mod.dump_bin(cpu)
    if path.lower().endswith('.bin'):
        # 显式要求旧格式: 只写 BIN v3, 便于旧工具/旧版本消费。
        crom_mod.save_bin(cpu, path)
        return os.path.abspath(path)
    return save(path, code, target=target, multi_file=multi_file, meta=meta,
                symbols=symbols, sources=sources)
