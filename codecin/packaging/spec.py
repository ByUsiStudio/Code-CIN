"""pic 包/导入规格的解析。

同一个规格同时用于 ``pic install`` 与 CIN ``import``：

============================  ==================================================
写法                          含义
============================  ==================================================
``rich``                      包入口 (兼容旧写法)
``rich:*``                    包内全部公开模块 (分块导入)
``rich:table``                只导入 ``table`` 模块
``rich:table,markdown``       导入多个模块 (也可用 ``+`` 分隔)
``rich::1.1.1``               指定版本的包入口
``rich::1.1.1:*``             指定版本的全部公开模块
``rich::1.1.1:table``         指定版本的 ``table`` 模块
``rich@1.1.1``                旧式版本写法, 等价 ``rich::1.1.1``
``rich@1.1.1:table``          旧式版本 + 模块选择
============================  ==================================================

冒号是 CIN 的包选择分隔符: 包名里不允许出现 ``:``, 因此不会与现有
``import "hello/util.cin"`` 之类的路径写法冲突。版本号只接受精确版本
(``x.y.z``); 安装时仍可用 ``^1.2`` / ``~1.2`` 等约束。
"""

import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

__all__ = [
    'PackageSpec', 'SpecError', 'is_package_spec', 'parse_package_spec',
    'split_selectors',
]

_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
_VERSION_RE = re.compile(
    r'^[vV]?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?)$')

#: 明显的非包名规格: 路径 / Git / URL / 归档
_NON_PACKAGE_PREFIXES = (
    '.', '/', '\\', '~', 'path:', 'git+', 'git:', 'file:', 'http:', 'https:',
)


class SpecError(ValueError):
    """包/导入规格非法。"""


def split_selectors(selector: str) -> Tuple[str, ...]:
    """把 ``table,markdown`` / ``a+b`` 拆成模块选择器元组。"""
    text = str(selector or '').strip()
    if not text:
        return ()
    parts = [part.strip() for part in re.split(r'[,+]', text)]
    return tuple(part for part in parts if part)


def _validate_selector(selector: str) -> str:
    text = str(selector or '').strip().replace('\\', '/')
    if not text:
        return ''
    if text.startswith('/') or '..' in text.split('/'):
        raise SpecError(f'模块选择器不能是绝对路径或包含 "..": {selector!r}')
    for part in split_selectors(text):
        if not re.fullmatch(r'[A-Za-z0-9_.*?/\[\]-]+', part):
            raise SpecError(f'模块选择器非法: {part!r}')
    return text


@dataclass(frozen=True)
class PackageSpec:
    """解析后的包规格 (安装 / import 共用)。"""

    name: str
    version: str = ''
    selector: str = ''

    @property
    def modules(self) -> Tuple[str, ...]:
        return split_selectors(self.selector)

    @property
    def is_all(self) -> bool:
        """``rich:*`` / ``rich::1.1.1:*``: 导入全部公开模块。"""
        return any(module == '*' for module in self.modules)

    @property
    def has_selector(self) -> bool:
        return bool(self.selector)

    @property
    def is_latest(self) -> bool:
        return not self.version or self.version.lower() in ('latest', '*', 'any')

    @property
    def display(self) -> str:
        text = self.name
        if self.version and not self.is_latest:
            text += '::' + self.version
        if self.selector:
            text += ':' + self.selector
        return text

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.display


def is_package_spec(text: str) -> bool:
    """判断字符串是否像 ``name[:selector][::version]`` 包规格。"""
    try:
        parse_package_spec(text)
    except SpecError:
        return False
    return True


def _looks_like_path(text: str) -> bool:
    lowered = text.lower()
    if lowered.endswith(('.cinpkg', '.mkn', '.cvm', '.toml')):
        return True
    if os.path.exists(text):
        return True
    return lowered.startswith(_NON_PACKAGE_PREFIXES)


def parse_package_spec(text: str) -> PackageSpec:
    """解析 ``name`` / ``name:module`` / ``name::version:module``。"""
    raw = str(text or '').strip()
    if not raw:
        raise SpecError('空的包规格')
    if _looks_like_path(raw):
        raise SpecError(f'不是包名规格: {raw!r}')

    version = ''
    selector = ''
    name_part = raw

    # 1) 版本段: name[...]::<version>[:<selector>]
    if '::' in name_part:
        left, right = name_part.split('::', 1)
        version_part, sep, selector_part = right.partition(':')
        version = version_part.strip()
        if sep:
            selector = selector_part.strip()
        name_part = left

    # 2) 模块段: name[...]:<selector>
    if ':' in name_part:
        head, selector_part = name_part.split(':', 1)
        if selector and selector_part.strip():
            raise SpecError(
                f'包规格只能有一个模块选择器: {raw!r}')
        selector = selector_part.strip()
        name_part = head

    # 3) 旧式版本: name@<version> / name==<version>
    if '==' in name_part:
        name_part, version_part = name_part.split('==', 1)
        if version and version_part.strip():
            raise SpecError(f'包规格出现多个版本: {raw!r}')
        version = version_part.strip()
    elif '@' in name_part:
        name_part, version_part = name_part.rsplit('@', 1)
        if version and version_part.strip():
            raise SpecError(f'包规格出现多个版本: {raw!r}')
        version = version_part.strip()

    name = name_part.strip()
    if not _NAME_RE.match(name):
        raise SpecError(f'包名非法: {name!r}')

    version = version.lstrip('vV') if version else ''
    if version and version.lower() not in ('latest', '*', 'any'):
        match = _VERSION_RE.match(version)
        if match is None:
            raise SpecError(
                f'版本号非法: {version!r} (import 需要精确版本 x.y.z)')
        version = match.group(1)

    return PackageSpec(name=name, version=version,
                       selector=_validate_selector(selector))


def spec_from_parts(name: str, version: Optional[str] = None,
                    selector: Optional[str] = None) -> PackageSpec:
    """由已经在手的字段构造规格 (selector 为空表示入口)。"""
    return parse_package_spec(
        str(name)
        + (f'::{version}' if version else '')
        + (f':{selector}' if selector else ''))


def format_specs(specs: Iterable[PackageSpec]) -> str:  # pragma: no cover
    return ', '.join(spec.display for spec in specs)
