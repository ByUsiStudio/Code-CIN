"""版本设施: 单一真源的解析/比较 + 构建环境自检 (workstream C)。

版本号以前只是 `codecin/__init__.py` 里的一个字符串, 只能被打印。本模块把它变成
**可编程、可自检**的设施, 同时不复制版本号本身:

  * 真源仍然是 `codecin.__version__` (见 `tests/test_version.py`,
    `pyproject.toml` 的 dynamic version, 以及 `script/gen_native_isa.py`
    生成的 Go 侧 `engine.BuildVersion`);
  * 纯函数 (`version_tuple` / `compare` / `is_version_string`) 与副作用分离,
    便于单测; 非法输入显式抛 `VersionError` 而不是静默算错;
  * `build_info()` 采集运行环境快照 (解释器/平台/原生库/包路径), 并且
    **保证绝不抛异常**: 原生库缺失、加载失败 (OSError)、ABI 不符或自报版本
    异常时, 一律优雅降级为 `native: False` / `native_version: None`。

对外接口::

    version_info()      -> (major, minor, patch)       # 非 x.y.z 抛 VersionError
    version_tuple(text) -> (major, minor, patch)       # 纯函数
    compare(a, b)       -> -1 / 0 / 1                  # 纯函数
    build_info()        -> dict                        # 绝不抛异常
    format_build_info() -> str                         # 可读多行文本
    build_info_json()   -> str                         # 机器可读 JSON

CLI 侧: `codecin --build-info [--json]` (见 codecin/cli.py)。
"""

import json
import os
import platform
import re
import sys
from typing import Any, Callable, Dict, Optional, Tuple

from . import __version__ as _SOURCE_VERSION

__all__ = [
    'VersionError',
    'current_version',
    'is_version_string',
    'version_tuple',
    'try_version_tuple',
    'version_info',
    'compare',
    'build_info',
    'format_build_info',
    'build_info_json',
]

# x.y.z, 允许可选 'v' 前缀与语义化版本的 -预发布 / +构建 后缀。
# 后缀只用于识别, 比较时按 x.y.z 三段数值进行 (见 compare)。
_VERSION_RE = re.compile(r'^[vV]?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.\-+]*)?$')

#: bump_version.py 使用的严格形式 (不接受 v 前缀 / 后缀)。
STRICT_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')

#: 原生库自报版本里的"没有版本"占位值 (见 codecin/native.py NativeEngine.version)。
_UNKNOWN_VERSIONS = frozenset({'', 'unknown', 'none', 'null', 'n/a'})


class VersionError(ValueError):
    """版本号无法按 x.y.z 解析。"""


def current_version() -> str:
    """读取唯一真源 `codecin.__version__` (每次动态读取, 便于测试替换)。"""
    try:
        import codecin
        value = getattr(codecin, '__version__', _SOURCE_VERSION)
    except Exception:                       # pragma: no cover - 导入异常兜底
        value = _SOURCE_VERSION
    return str(value)


def is_version_string(text: Any) -> bool:
    """`text` 是否为可解析的版本号 (x.y.z, 允许 v 前缀与 -/+ 后缀)。"""
    try:
        return _VERSION_RE.match(str(text).strip()) is not None
    except Exception:                       # pragma: no cover - __str__ 抛异常
        return False


def version_tuple(text: Any) -> Tuple[int, int, int]:
    """把版本号解析为 `(major, minor, patch)` 整数元组 (纯函数)。

    接受 `5.6.0` / `v5.6.0` / `5.6.0-rc.1+build.7` 与首尾空白;
    其余形式 (空串 / `5.6` / `1.2.3.4` / `abc`) 抛 `VersionError` (ValueError 子类),
    绝不返回 "猜测值"。
    """
    raw = str(text).strip()
    m = _VERSION_RE.match(raw)
    if m is None:
        raise VersionError(
            f"版本号必须形如 x.y.z (可带 -预发布/+构建 后缀): {raw!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def try_version_tuple(text: Any) -> Optional[Tuple[int, int, int]]:
    """`version_tuple` 的不抛异常版本: 非法输入返回 `None`。"""
    try:
        return version_tuple(text)
    except (VersionError, TypeError, ValueError):
        return None


def version_info() -> Tuple[int, int, int]:
    """当前版本 (`codecin.__version__`) 的 `(major, minor, patch)`。

    非 x.y.z 形式会抛 `VersionError`: 版本号漂移属于发布链路故障, 必须显式失败。
    需要"不抛异常"的调用方请用 `try_version_tuple(current_version())`。
    """
    return version_tuple(current_version())


def compare(a: Any, b: Any) -> int:
    """语义化数值比较 (纯函数): `a < b` 返回 -1, 相等 0, `a > b` 返回 1。

    只比较 x.y.z 三段的整数值 (如 `5.6.0-rc1` 与 `5.6.0` 视为相等),
    与 `script/bump_version.py` 的"新版本必须大于当前版本"判定一致。
    任一入参非法即抛 `VersionError`。
    """
    ta = version_tuple(a)
    tb = version_tuple(b)
    return (ta > tb) - (ta < tb)


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    """执行 `fn` 并把**任何**异常折叠为 `default` (build_info 的不抛异常基石)。"""
    try:
        return fn()
    except Exception:
        return default


def _native_details() -> Tuple[bool, Optional[str], Optional[str]]:
    """探测原生库: `(是否可用, 自报版本, 库文件路径)`。

    加载失败 (库缺失 / OSError / 缺导出符号) 全部视为不可用, 不向上抛异常。
    """
    try:
        from . import native as native_mod
        engine = native_mod.get_engine()
    except Exception:
        return False, None, None
    if engine is None:
        return False, None, None

    raw = _safe(engine.version)
    if isinstance(raw, bytes):               # pragma: no cover - c_char_p 已解码
        raw = raw.decode('utf-8', 'replace')
    version = str(raw).strip() if raw is not None else ''
    if version.lower() in _UNKNOWN_VERSIONS:
        version = ''

    lib = getattr(engine, 'lib', None)
    path = _safe(lambda: getattr(lib, '_name', None))
    return True, (version or None), (str(path) if path else None)


def build_info() -> Dict[str, Any]:
    """运行环境快照 (版本、解释器、平台、原生库、包路径)。

    **本函数绝不抛异常** —— 它是发布/排障自检入口, 收集不到的字段填 `''`/`None`,
    原生库不可用时 `native` 为 `False` (v5.9.0 起为 native-only, 需重建原生库)。
    返回值可直接 `json.dumps`。
    """
    version = _safe(current_version, '')
    if not isinstance(version, str):
        version = str(version)
    parsed = try_version_tuple(version)

    python_version = _safe(platform.python_version)
    if not python_version:
        python_version = _safe(lambda: sys.version.split()[0], '') or ''
    implementation = _safe(platform.python_implementation) or ''

    sys_platform = _safe(lambda: sys.platform, '') or ''
    system = _safe(platform.system) or ''
    machine = _safe(platform.machine) or ''

    native_ok, native_version, native_path = _safe(
        _native_details, (False, None, None))
    native_ok = bool(native_ok)
    if not isinstance(native_version, str):
        native_version = None
    if not isinstance(native_path, str):
        native_path = None

    # 自检: 原生库自报版本里是否含包版本 (Go 侧 BuildVersion 由生成器注入,
    # 正常情况下必然一致; 不一致说明旁边放了一个旧库)。
    native_matches: Optional[bool] = None
    if native_ok and native_version and parsed is not None:
        native_matches = version in native_version

    return {
        'version': version,
        'version_info': list(parsed) if parsed is not None else None,
        'python': str(python_version),
        'python_implementation': str(implementation),
        'platform': f'{sys_platform}/{machine}' if machine else str(sys_platform),
        'system': str(system),
        'machine': str(machine),
        'native': native_ok,
        'native_version': native_version,
        'native_path': native_path,
        'native_version_matches': native_matches,
        'package_path': os.path.dirname(os.path.abspath(__file__)),
        'executable': _safe(lambda: sys.executable, '') or '',
    }


def _human(value: Any, empty: str = '(未知)') -> str:
    if value is None or value == '':
        return empty
    return str(value)


def format_build_info(info: Optional[Dict[str, Any]] = None) -> str:
    """把 `build_info()` 渲染成可读的多行纯文本 (无 ANSI, 便于重定向/贴报告)。"""
    data: Dict[str, Any] = dict(build_info() if info is None else info)

    parsed = data.get('version_info')
    if isinstance(parsed, (list, tuple)) and len(parsed) == 3:
        parsed_text = '(' + ', '.join(str(p) for p in parsed) + ')'
    else:
        parsed_text = '(无法解析为 x.y.z)'

    if data.get('native'):
        native_text = '可用'
        if data.get('native_version'):
            native_text += f": {data['native_version']}"
    else:
        native_text = '不可用 (请运行 codecin/native/build.ps1 或 build.sh 重建)'

    matches = data.get('native_version_matches')
    matches_text = ('(未知)' if matches is None
                    else '一致' if matches else '不一致 (原生库可能过期)')

    lines = [
        f"Code CIN 构建信息 (build info) - {_human(data.get('version'), '?')}",
        f"  version          : {_human(data.get('version'), '?')}",
        f"  version_info     : {parsed_text}",
        f"  python           : {_human(data.get('python'))}"
        f" ({_human(data.get('python_implementation'), '?')})",
        f"  platform         : {_human(data.get('platform'))}",
        f"  system           : {_human(data.get('system'))}",
        f"  machine          : {_human(data.get('machine'))}",
        f"  native           : {native_text}",
        f"  native version   : {_human(data.get('native_version'))}",
        f"  native library   : {_human(data.get('native_path'))}",
        f"  native matches   : {matches_text}",
        f"  package path     : {_human(data.get('package_path'))}",
        f"  executable       : {_human(data.get('executable'))}",
    ]
    return '\n'.join(lines)


def build_info_json(info: Optional[Dict[str, Any]] = None, indent: int = 2) -> str:
    """`build_info()` 的 JSON 文本 (UTF-8 友好: `ensure_ascii=False`)。"""
    data = build_info() if info is None else info
    return json.dumps(data, indent=indent, ensure_ascii=False)
