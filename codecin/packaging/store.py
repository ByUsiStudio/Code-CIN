"""包安装存储与项目依赖目录。

全局存储 (默认 ``~/.pic``, 可用 ``PIC_HOME`` 覆盖)::

    index.json                       # 已安装记录 (JSON, 原子写)
    packages/<name>/<version>/       # 包内容 (pic.toml + src/...)
    packages/<name>/<version>/.pic-link   # 可编辑安装: 指向源目录
    cache/                           # Git 缓存 / 归档缓存
    registry.json                    # 分散式注册表 (名称 -> Git 仓库)

项目依赖目录 (``mkn`` / ``pic --project`` 使用)::

    <project>/pic_modules/<name>/    # 直接可被 CIN import 解析

存储只负责文件与索引, 不执行网络/编译。
"""

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .model import ManifestError, load_package_manifest, validate_name, validate_version

__all__ = ['InstalledPackage', 'PackageStore', 'default_store',
           'project_modules_dir', 'copy_package_tree', 'resolve_link']

_COPY_IGNORE = shutil.ignore_patterns(
    '.git', '.hg', '.svn', '__pycache__', '*.pyc', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'build', 'dist', 'target', 'pic_modules',
    '.pic', '.mkn', 'node_modules', '.venv', 'venv',
)


class StoreError(ManifestError):
    """包存储操作失败。"""


@dataclass
class InstalledPackage:
    name: str
    version: str
    path: str
    repo: str = ''
    rev: str = ''
    editable: bool = False
    installed_at: str = ''

    @property
    def real_path(self) -> str:
        return resolve_link(self.path)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {'path': self.path}
        if self.repo:
            data['repo'] = self.repo
        if self.rev:
            data['rev'] = self.rev
        if self.editable:
            data['editable'] = True
        if self.installed_at:
            data['installed_at'] = self.installed_at
        return data


def resolve_link(path: str) -> str:
    """可编辑安装的 ``.pic-link`` 解引用; 普通目录原样返回。"""
    link = os.path.join(path, '.pic-link')
    if os.path.isfile(link):
        try:
            with open(link, encoding='utf-8') as f:
                target = f.read().strip()
        except OSError:
            return path
        if target:
            return os.path.abspath(target)
    return path


def project_modules_dir(project_root: str) -> str:
    return os.path.join(os.path.abspath(project_root), 'pic_modules')


def copy_package_tree(src: str, dest: str, editable: bool = False) -> str:
    """把包目录复制/链接到 ``dest``; 返回 ``dest``。"""
    src = os.path.abspath(src)
    dest = os.path.abspath(dest)
    if not os.path.isdir(src):
        raise StoreError(f'包源目录不存在: {src}')
    if os.path.exists(dest):
        shutil.rmtree(dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    if editable:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, '.pic-link'), 'w', encoding='utf-8') as f:
            f.write(src + '\n')
        return dest
    shutil.copytree(src, dest, ignore=_COPY_IGNORE, dirs_exist_ok=False)
    return dest


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PackageStore:
    """全局包存储 (带 JSON 索引与损坏自愈)。"""

    def __init__(self, root: Optional[str] = None):
        if root is None:
            root = os.environ.get('PIC_HOME') or os.path.join(
                os.path.expanduser('~'), '.pic')
        self.root = os.path.abspath(root)
        self.packages_dir = os.path.join(self.root, 'packages')
        self.index_path = os.path.join(self.root, 'index.json')
        self.registry_path = os.path.join(self.root, 'registry.json')
        self.cache_dir = os.path.join(self.root, 'cache')
        self.index: Dict[str, Any] = {}
        self._load()

    # ---------------- 索引 ----------------

    def _empty_index(self) -> Dict[str, Any]:
        return {'format': 1, 'packages': {}}

    def _load(self) -> None:
        self.index = self._empty_index()
        if os.path.isfile(self.index_path):
            try:
                with open(self.index_path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get('packages'), dict):
                    self.index = data
                    return
            except (OSError, ValueError):
                pass
        self.scan()
        self.save()

    def save(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        payload = json.dumps(self.index, ensure_ascii=False, indent=2,
                             sort_keys=True) + '\n'
        fd, tmp = tempfile.mkstemp(prefix='.index-', suffix='.json',
                                   dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(payload)
            os.replace(tmp, self.index_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def scan(self) -> None:
        """从磁盘重建索引 (只读目录结构, 不需要 pic.toml)。"""
        packages: Dict[str, Any] = {}
        if os.path.isdir(self.packages_dir):
            for name in sorted(os.listdir(self.packages_dir)):
                name_dir = os.path.join(self.packages_dir, name)
                if not os.path.isdir(name_dir):
                    continue
                versions: Dict[str, Any] = {}
                for version in sorted(os.listdir(name_dir)):
                    path = os.path.join(name_dir, version)
                    if not os.path.isdir(path):
                        continue
                    try:
                        validate_name(name)
                        validate_version(version)
                    except ManifestError:
                        continue
                    versions[version] = InstalledPackage(
                        name=name, version=version, path=path).to_dict()
                if versions:
                    packages[name] = {
                        'current': self._highest(list(versions)),
                        'versions': versions,
                    }
        self.index = {'format': 1, 'packages': packages}

    @staticmethod
    def _highest(versions: Iterable[str]) -> str:
        def key(v: str):
            try:
                return tuple(int(p) for p in v.split('-')[0].split('.'))
            except ValueError:
                return (0, 0, 0)
        return max(versions, key=key)

    # ---------------- 查询 ----------------

    def _records(self, name: Optional[str] = None) -> Dict[str, Any]:
        packages = self.index.get('packages', {})
        if name is None:
            return packages
        entry = packages.get(name)
        return entry if isinstance(entry, dict) else {}

    def versions(self, name: str) -> List[str]:
        entry = self._records(name)
        versions = entry.get('versions')
        return sorted(versions) if isinstance(versions, dict) else []

    def get(self, name: str, version: Optional[str] = None
            ) -> Optional[InstalledPackage]:
        entry = self._records(name)
        versions = entry.get('versions')
        if not isinstance(versions, dict) or not versions:
            return None
        if version is None:
            version = entry.get('current') or self._highest(list(versions))
        record = versions.get(version)
        if record is None:
            return None
        path = record.get('path') or os.path.join(
            self.packages_dir, name, version)
        if not os.path.isabs(path):
            path = os.path.join(self.root, path)
        return InstalledPackage(
            name=name, version=version, path=os.path.abspath(path),
            repo=str(record.get('repo') or ''),
            rev=str(record.get('rev') or ''),
            editable=bool(record.get('editable')),
            installed_at=str(record.get('installed_at') or ''),
        )

    def all(self) -> List[InstalledPackage]:
        out: List[InstalledPackage] = []
        for name in sorted(self._records()):
            for version in self.versions(name):
                pkg = self.get(name, version)
                if pkg is not None:
                    out.append(pkg)
        return out

    def package_entry(self, name: str,
                      version: Optional[str] = None) -> Optional[str]:
        pkg = self.get(name, version)
        if pkg is None or not os.path.isdir(pkg.real_path):
            return None
        try:
            return load_package_manifest(pkg.real_path,
                                         require_entry=False).entry_path
        except ManifestError:
            for candidate in (f'src/{name}.cin', f'{name}.cin',
                              'src/main.cin', 'main.cin'):
                path = os.path.join(pkg.real_path, candidate)
                if os.path.isfile(path):
                    return path
        return None

    # ---------------- 写入 ----------------

    def install_dir(self, name: str, version: str) -> str:
        validate_name(name)
        validate_version(version)
        return os.path.join(self.packages_dir, name, version)

    def add(self, src_root: str, name: str, version: str, *,
            repo: str = '', rev: str = '', editable: bool = False,
            force: bool = False, make_current: bool = True
            ) -> InstalledPackage:
        validate_name(name)
        validate_version(version)
        dest = self.install_dir(name, version)
        if os.path.exists(dest) and not force:
            pkg = self.get(name, version)
            if pkg is not None:
                return pkg
            raise StoreError(
                f'{name}=={version} 已存在 ({dest}); 使用 --force 覆盖')
        copy_package_tree(src_root, dest, editable=editable)
        record = InstalledPackage(
            name=name, version=version, path=dest, repo=repo, rev=rev,
            editable=editable, installed_at=_now())
        package = self.index.setdefault('packages', {}).setdefault(
            name, {'versions': {}})
        versions = package.setdefault('versions', {})
        versions[version] = record.to_dict()
        if make_current or not package.get('current'):
            package['current'] = version
        self.save()
        return record

    def set_current(self, name: str, version: str) -> None:
        package = self.index.get('packages', {}).get(name)
        if not package or version not in package.get('versions', {}):
            raise StoreError(f'未安装: {name}=={version}')
        package['current'] = version
        self.save()

    def remove(self, name: str, version: Optional[str] = None) -> bool:
        package = self.index.get('packages', {}).get(name)
        if not package:
            return False
        versions = package.get('versions', {})
        targets = [version] if version else list(versions)
        removed = False
        for ver in list(targets):
            if ver not in versions:
                continue
            path = self.install_dir(name, ver)
            shutil.rmtree(path, ignore_errors=True)
            versions.pop(ver, None)
            removed = True
        if not versions:
            self.index['packages'].pop(name, None)
        elif package.get('current') not in versions:
            package['current'] = self._highest(versions)
        if removed:
            self.save()
        return removed

    # ---------------- 注册表 (名称 -> Git 仓库) ----------------

    def load_registry(self) -> Dict[str, Any]:
        if not os.path.isfile(self.registry_path):
            return {'format': 1, 'packages': {}}
        try:
            with open(self.registry_path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('packages'), dict):
                return data
        except (OSError, ValueError):
            pass
        return {'format': 1, 'packages': {}}

    def save_registry(self, data: Dict[str, Any]) -> None:
        os.makedirs(self.root, exist_ok=True)
        with open(self.registry_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')

    def register(self, name: str, repo: str, version: Optional[str] = None,
                 tag: Optional[str] = None) -> Dict[str, Any]:
        validate_name(name, '注册名')
        data = self.load_registry()
        entry = data.setdefault('packages', {}).setdefault(
            name, {'repo': repo, 'versions': {}})
        entry['repo'] = repo
        if version:
            entry.setdefault('versions', {})[version] = {
                'tag': tag or f'v{version}',
            }
        self.save_registry(data)
        return entry

    def lookup(self, name: str) -> Optional[Dict[str, Any]]:
        entry = self.load_registry().get('packages', {}).get(name)
        return entry if isinstance(entry, dict) else None

    # ---------------- 杂项 ----------------

    def cache_path(self, *parts: str) -> str:
        path = os.path.join(self.cache_dir, *parts)
        os.makedirs(path, exist_ok=True)
        return path


def default_store() -> PackageStore:
    return PackageStore()


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
