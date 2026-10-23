"""包/项目清单模型与语义化版本约束。

两个清单文件:

``pic.toml`` (包)
    ``[package]`` 段描述 ``name`` / ``version`` / ``entry`` / ``repo`` 等;
    ``[dependencies]`` 段描述依赖 (字符串版本或内联表)。

``mkn.toml`` (项目)
    ``[project]`` 段 (也接受 ``[package]`` 别名), ``[build]`` 段与
    ``[dependencies]`` 段。

依赖值支持两种写法::

    [dependencies]
    mathx = "1.2.0"                  # 版本约束 (通过注册表 / 本地仓库解析)

    [dependencies.mathx]
    git = "https://github.com/u/cin-mathx"
    tag = "v1.2.0"                   # 或 rev = "<commit>"
    path = "../mathx"                # 本地路径依赖 (优先级最高)

本模块只做**解析与校验**, 不访问网络, 也不复制文件。
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .toml import TomlError, load as _load_toml

__all__ = [
    'Dependency', 'ManifestError', 'MknManifest', 'PackageMeta',
    'find_project_root', 'load_mkn_manifest', 'load_package_manifest',
    'parse_version', 'satisfies', 'validate_name', 'validate_version',
]

_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
_VERSION_RE = re.compile(r'^[vV]?(\d+)\.(\d+)\.(\d+)'
                         r'(?:[-+][0-9A-Za-z.+-]+)?$')

PIC_MANIFEST = 'pic.toml'
MKN_MANIFEST = 'mkn.toml'


class ManifestError(ValueError):
    """清单缺失/字段非法/版本约束非法。"""


class VersionError(ManifestError):
    """版本号或版本约束无法解析。"""


def validate_name(name: Any, what: str = '包名') -> str:
    text = str(name or '').strip()
    if not _NAME_RE.match(text):
        raise ManifestError(
            f'{what}非法: {name!r} (需以字母开头, 仅含字母/数字/下划线/连字符, '
            f'长度 1..64)')
    return text


def validate_version(version: Any) -> str:
    text = str(version or '').strip()
    if not _VERSION_RE.match(text):
        raise ManifestError(
            f'版本号非法: {version!r} (需为 x.y.z, 可带 -预发布/+构建 后缀)')
    return text


def parse_version(version: Any) -> Tuple[int, int, int]:
    text = str(version or '').strip()
    m = _VERSION_RE.match(text)
    if not m:
        raise VersionError(f'版本号非法: {version!r} (需为 x.y.z)')
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


_LOOSE_RE = re.compile(
    r'^[vV]?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:[-+].*)?$')


def _parse_loose(version: Any) -> Tuple[Tuple[int, int, int], int]:
    """约束里的版本: 允许 ``1`` / ``1.2`` / ``1.x`` / ``1.2.x``。

    返回 ``(数值三元组, 通配级别)``; 通配级别 1 表示 ``1.x``, 2 表示
    ``1.2.x``, 0 表示精确三段。
    """
    text = str(version or '').strip()
    m = _LOOSE_RE.match(text)
    if not m:
        raise VersionError(f'版本号非法: {version!r}')
    wildcard = 0
    values: List[int] = []
    for idx, part in enumerate((m.group(1), m.group(2), m.group(3))):
        if part is None:
            values.append(0)
            continue
        if part in ('x', 'X', '*'):
            wildcard = idx
            values.append(0)
            continue
        values.append(int(part))
    return (values[0], values[1], values[2]), wildcard


def _next_major(v: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return (v[0] + 1, 0, 0)


def _next_minor(v: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return (v[0], v[1] + 1, 0)


def _next_patch(v: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return (v[0], v[1], v[2] + 1)


def _satisfies_one(version: Tuple[int, int, int], cond: str) -> bool:
    cond = cond.strip()
    if not cond or cond in ('*', 'latest', 'any'):
        return True
    if cond.startswith('^'):
        base, _ = _parse_loose(cond[1:])
        upper = _next_major(base) if base[0] > 0 else (
            _next_minor(base) if base[1] > 0 else _next_patch(base))
        return base <= version < upper
    if cond.startswith('~'):
        base, _ = _parse_loose(cond[1:])
        return base <= version < _next_minor(base)
    for op in ('>=', '<=', '==', '!=', '>', '<', '='):
        if cond.startswith(op):
            base, wildcard = _parse_loose(cond[len(op):])
            if wildcard:
                return _satisfies_one(version, cond[len(op):])
            if op == '>=':
                return version >= base
            if op == '<=':
                return version <= base
            if op == '>':
                return version > base
            if op == '<':
                return version < base
            if op == '!=':
                return version != base
            return version == base
    base, wildcard = _parse_loose(cond)
    if wildcard == 1:      # 1.x
        return (base[0], 0, 0) <= version < (base[0] + 1, 0, 0)
    if wildcard == 2:      # 1.2.x
        lower = (base[0], base[1], 0)
        return lower <= version < (base[0], base[1] + 1, 0)
    return version == base


def satisfies(version: Any, constraint: Optional[str]) -> bool:
    """版本是否满足约束。

    支持 ``*`` / 精确版本 / ``^1.2.0`` / ``~1.2.0`` / ``>=1.0,<2.0`` /
    ``1.x`` / ``1.2.x`` / ``||`` 组合。空约束视为任意版本。
    """
    ver = parse_version(version)
    text = str(constraint or '').strip()
    if not text or text in ('*', 'latest', 'any'):
        return True
    for clause in text.split('||'):
        parts = [p for p in re.split(r'[,\s]+', clause.strip()) if p]
        if parts and all(_satisfies_one(ver, p) for p in parts):
            return True
    return False


@dataclass
class Dependency:
    """一条依赖声明。"""

    name: str
    constraint: str = ''
    git: str = ''
    tag: str = ''
    rev: str = ''
    path: str = ''
    optional: bool = False

    def __post_init__(self) -> None:
        self.name = validate_name(self.name, '依赖名')

    @property
    def is_path(self) -> bool:
        return bool(self.path)

    @property
    def is_git(self) -> bool:
        return bool(self.git)

    @property
    def spec(self) -> str:
        """用于展示/安装的单行规格。"""
        if self.path:
            return f'path:{self.path}'
        if self.git:
            ref = self.tag or self.rev
            return f'git:{self.git}' + (f'@{ref}' if ref else '')
        return f'{self.name}@{self.constraint}' if self.constraint else self.name

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.constraint:
            data['version'] = self.constraint
        if self.git:
            data['git'] = self.git
        if self.tag:
            data['tag'] = self.tag
        if self.rev:
            data['rev'] = self.rev
        if self.path:
            data['path'] = self.path
        if self.optional:
            data['optional'] = True
        return data


def _dependency_from(name: str, value: Any, base_dir: str) -> Dependency:
    if isinstance(value, str):
        return Dependency(name=name, constraint=value.strip())
    if isinstance(value, bool):
        raise ManifestError(f'依赖 {name!r} 的值非法: 需要版本字符串或表')
    if not isinstance(value, dict):
        raise ManifestError(f'依赖 {name!r} 的值非法: {value!r}')
    constraint = str(value.get('version') or value.get('constraint') or '').strip()
    git = str(value.get('git') or value.get('repo') or '').strip()
    tag = str(value.get('tag') or '').strip()
    rev = str(value.get('rev') or value.get('revision') or '').strip()
    path = str(value.get('path') or '').strip()
    if path and not os.path.isabs(path):
        path = os.path.normpath(os.path.join(base_dir, path))
    optional = bool(value.get('optional') or False)
    if not (constraint or git or path):
        raise ManifestError(
            f'依赖 {name!r} 需要 version / git / path 中的至少一项')
    return Dependency(name=name, constraint=constraint, git=git, tag=tag,
                      rev=rev, path=path, optional=optional)


@dataclass
class PackageMeta:
    """``pic.toml`` 的包元数据。"""

    root: str
    manifest_path: str
    name: str
    version: str
    description: str = ''
    license: str = ''
    authors: List[str] = field(default_factory=list)
    entry: str = ''
    repo: str = ''
    tag: str = ''
    keywords: List[str] = field(default_factory=list)
    dependencies: List[Dependency] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def entry_path(self) -> Optional[str]:
        """解析后的入口文件绝对路径 (不存在时为 None)。"""
        return _resolve_entry(self.root, self.entry, self.name)

    @property
    def entry_rel(self) -> str:
        path = self.entry_path
        if path is None:
            return self.entry
        return os.path.relpath(path, self.root).replace(os.sep, '/')

    def to_dict(self) -> Dict[str, Any]:
        package: Dict[str, Any] = {
            'name': self.name,
            'version': self.version,
        }
        for key in ('description', 'license', 'entry', 'repo', 'tag'):
            value = getattr(self, key)
            if value:
                package[key] = value
        if self.authors:
            package['authors'] = list(self.authors)
        if self.keywords:
            package['keywords'] = list(self.keywords)
        data: Dict[str, Any] = {'package': package}
        if self.dependencies:
            deps: Dict[str, Any] = {}
            for dep in self.dependencies:
                if dep.path or dep.git or dep.optional or dep.constraint:
                    deps[dep.name] = dep.to_dict() if (
                        dep.path or dep.git or dep.optional) else dep.constraint
            data['dependencies'] = deps
        return data


@dataclass
class MknManifest:
    """``mkn.toml`` 的项目模型。"""

    root: str
    manifest_path: str
    name: str
    version: str
    description: str = ''
    license: str = ''
    authors: List[str] = field(default_factory=list)
    entry: str = 'src/main.cin'
    repo: str = ''
    tag: str = ''
    dependencies: List[Dependency] = field(default_factory=list)
    dev_dependencies: List[Dependency] = field(default_factory=list)
    build: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def entry_path(self) -> str:
        path = _resolve_entry(self.root, self.entry, self.name)
        if path is None:
            raise ManifestError(
                f'项目入口文件不存在: {self.entry!r} (项目根 {self.root})')
        return path

    @property
    def target_dir(self) -> str:
        return os.path.join(self.root, 'target')

    @property
    def pic_modules(self) -> str:
        return os.path.join(self.root, 'pic_modules')

    def build_option(self, key: str, default: Any = None) -> Any:
        return self.build.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        project: Dict[str, Any] = {
            'name': self.name,
            'version': self.version,
            'entry': self.entry,
        }
        for key in ('description', 'license', 'repo', 'tag'):
            value = getattr(self, key)
            if value:
                project[key] = value
        if self.authors:
            project['authors'] = list(self.authors)
        data: Dict[str, Any] = {'project': project}
        if self.build:
            data['build'] = dict(self.build)
        if self.dependencies:
            deps: Dict[str, Any] = {}
            for dep in self.dependencies:
                if dep.path or dep.git or dep.optional:
                    deps[dep.name] = dep.to_dict()
                else:
                    deps[dep.name] = dep.constraint
            data['dependencies'] = deps
        return data


def _within(root: str, path: str) -> bool:
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    try:
        return os.path.commonpath([root_abs, path_abs]) == root_abs
    except ValueError:  # 不同盘符
        return False


def _resolve_entry(root: str, entry: str, name: str) -> Optional[str]:
    candidates = []
    if entry:
        candidates.append(entry)
    candidates += [f'src/{name}.cin', f'{name}.cin', 'src/main.cin', 'main.cin']
    seen = set()
    for rel in candidates:
        rel = rel.replace('/', os.sep)
        path = os.path.normpath(os.path.join(root, rel))
        if path in seen:
            continue
        seen.add(path)
        if not _within(root, path):
            continue
        if os.path.isfile(path):
            return path
    return None


def find_project_root(start: str,
                       names: Tuple[str, ...] = (MKN_MANIFEST, PIC_MANIFEST)
                       ) -> Optional[str]:
    """从 ``start`` 向上查找包含清单文件的目录。"""
    path = os.path.abspath(start)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    while True:
        for name in names:
            if os.path.isfile(os.path.join(path, name)):
                return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _manifest_path(path_or_root: str, filename: str) -> str:
    path = os.path.abspath(path_or_root)
    if os.path.isdir(path):
        candidate = os.path.join(path, filename)
        if not os.path.isfile(candidate):
            raise ManifestError(f'找不到清单文件: {candidate}')
        return candidate
    if not os.path.isfile(path):
        raise ManifestError(f'清单文件不存在: {path}')
    return path


def _load(path: str) -> Dict[str, Any]:
    try:
        return _load_toml(path)
    except TomlError as e:
        raise ManifestError(f'{path}: {e}') from e


def _require_text(table: Dict[str, Any], key: str, path: str,
                  what: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f'{path}: {what} 缺少字符串字段 {key!r}')
    return value.strip()


def load_package_manifest(path_or_root: str,
                          require_entry: bool = True) -> PackageMeta:
    """读取 ``pic.toml``; ``path_or_root`` 可为文件或包目录。"""
    path = _manifest_path(path_or_root, PIC_MANIFEST)
    root = os.path.dirname(path)
    data = _load(path)
    table = data.get('package')
    if not isinstance(table, dict):
        raise ManifestError(f'{path}: 缺少 [package] 表')
    name = validate_name(_require_text(table, 'name', path, '[package]'))
    version = validate_version(_require_text(table, 'version', path, '[package]'))
    entry = str(table.get('entry') or '').strip()
    authors = table.get('authors') or []
    if isinstance(authors, str):
        authors = [authors]
    keywords = table.get('keywords') or []
    if isinstance(keywords, str):
        keywords = [keywords]
    meta = PackageMeta(
        root=root, manifest_path=path, name=name, version=version,
        description=str(table.get('description') or '').strip(),
        license=str(table.get('license') or '').strip(),
        authors=[str(a) for a in authors],
        entry=entry,
        repo=str(table.get('repo') or table.get('repository') or '').strip(),
        tag=str(table.get('tag') or '').strip(),
        keywords=[str(k) for k in keywords],
        raw=data,
    )
    deps_table = data.get('dependencies') or {}
    if not isinstance(deps_table, dict):
        raise ManifestError(f'{path}: [dependencies] 必须是表')
    meta.dependencies = [
        _dependency_from(name, value, root)
        for name, value in deps_table.items()
    ]
    if require_entry and meta.entry_path is None:
        raise ManifestError(
            f'{path}: 包入口文件不存在 (entry={entry!r}; 已尝试 '
            f'src/{name}.cin, {name}.cin, src/main.cin)')
    return meta


def load_mkn_manifest(path_or_root: str) -> MknManifest:
    """读取 ``mkn.toml``; ``[project]`` 与 ``[package]`` 均可。"""
    path = _manifest_path(path_or_root, MKN_MANIFEST)
    root = os.path.dirname(path)
    data = _load(path)
    table = data.get('project') or data.get('package')
    if not isinstance(table, dict):
        raise ManifestError(f'{path}: 缺少 [project] 表')
    name = validate_name(_require_text(table, 'name', path, '[project]'))
    version = validate_version(_require_text(table, 'version', path, '[project]'))
    authors = table.get('authors') or []
    if isinstance(authors, str):
        authors = [authors]
    manifest = MknManifest(
        root=root, manifest_path=path, name=name, version=version,
        description=str(table.get('description') or '').strip(),
        license=str(table.get('license') or '').strip(),
        authors=[str(a) for a in authors],
        entry=str(table.get('entry') or 'src/main.cin').strip(),
        repo=str(table.get('repo') or table.get('repository') or '').strip(),
        tag=str(table.get('tag') or '').strip(),
        build=dict(data.get('build') or {}),
        raw=data,
    )
    deps_table = data.get('dependencies') or {}
    dev_table = data.get('dev-dependencies') or {}
    if not isinstance(deps_table, dict):
        raise ManifestError(f'{path}: [dependencies] 必须是表')
    if not isinstance(dev_table, dict):
        raise ManifestError(f'{path}: [dev-dependencies] 必须是表')
    manifest.dependencies = [
        _dependency_from(name, value, root)
        for name, value in deps_table.items()
    ]
    manifest.dev_dependencies = [
        _dependency_from(name, value, root)
        for name, value in dev_table.items()
    ]
    # 入口延迟到 build 时检查: 这样 `mkn init` 之后即使手写 entry 也能先验清单
    return manifest
