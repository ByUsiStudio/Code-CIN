"""``pic`` —— Code CIN 包管理器 (pip 风格, Cdifit 中央注册表)。

核心约定 (v6.2.0+):

* **中央注册表**: 包记录 (标识名、Git 获取地址、版本列表、源码/清单摘要、
  发布者) 存放在 Cdifit 文件 API (``cloudreve://my/pic/v1``); 首次使用必须
  通过浏览器登录 Cdifit;
* **Git 作为内容源**: 包内容仍在用户自己的 Git 仓库; 中心只存元数据与安全摘要,
  安装时按 Git 标签/提交拉取并校验摘要;
* **安装即可直接 import**: 包安装到 ``$PIC_HOME/packages/<name>/<version>``
  或项目 ``pic_modules/<name>``; CIN 的 ``import "name"`` 会自动解析;
* **依赖递归安装** / **可编辑安装** 与旧版一致;
* ``PIC_OFFLINE=1`` 时退化为纯本地注册表 (离线开发/测试)。

``pic`` 本身不执行被安装包的代码, 也不编译; 编译/运行交给 ``mkn`` / ``cvm``。
"""

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import __version__
from .packaging import archive, vcs
from .packaging.model import (
    Dependency,
    ManifestError,
    PackageMeta,
    load_package_manifest,
    satisfies,
    validate_name,
    validate_version,
)
from .packaging.store import (
    InstalledPackage,
    PackageStore,
    copy_package_tree,
    default_store,
    project_modules_dir,
    resolve_link,
)
from .packaging.toml import dumps as toml_dumps
from .packaging.toml import load as toml_load
from .pic_api import (
    DEFAULT_API_BASE,
    ApiError,
    AuthStore,
    CdifitClient,
    TwoFactorRequired,
    client_from_store,
)
from .pic_login import AuthError, run_login_flow
from .pic_registry import (
    DEFAULT_NAMESPACE,
    Registry,
    RegistryError,
    iter_package_files,
    manifest_digest,
    source_digest,
)

__all__ = ['main', 'install_spec', 'install_path', 'install_git',
           'install_named', 'build_package', 'write_project_lock']

_META_FILE = '.pic-meta.json'
_IGNORE = shutil.ignore_patterns(
    '.git', '.hg', '.svn', '__pycache__', '*.pyc', '.pytest_cache',
    '.ruff_cache', 'dist', 'build', 'target', 'pic_modules', '.pic', '.mkn',
    'node_modules', '.venv', 'venv', '*.cinpkg')

#: 进程内缓存的已登录客户端 (一次 CLI 调用只弹一次浏览器)
_CLIENT: Optional[CdifitClient] = None


class PicError(RuntimeError):
    """pic 命令的可预期失败 (不打印 traceback)。"""


# ==================== 配置 / 登录 ====================


@dataclass
class PicConfig:
    api_base: str = DEFAULT_API_BASE
    namespace: str = DEFAULT_NAMESPACE
    offline: bool = False


def _pic_home() -> str:
    return os.path.abspath(os.environ.get('PIC_HOME')
                           or os.path.join(os.path.expanduser('~'), '.pic'))


def _read_config() -> Dict[str, Any]:
    path = os.path.join(_pic_home(), 'config.toml')
    if not os.path.isfile(path):
        return {}
    try:
        return toml_load(path)
    except Exception:  # noqa: BLE001 - 配置损坏不应让所有命令崩溃
        return {}


def load_config() -> PicConfig:
    data = _read_config()
    registry = data.get('registry') or {}
    login = data.get('login') or {}
    env_offline = os.environ.get('PIC_OFFLINE', '').lower() in (
        '1', 'true', 'yes', 'on')
    return PicConfig(
        api_base=str(os.environ.get('PIC_API_BASE')
                     or registry.get('api_base') or DEFAULT_API_BASE).rstrip('/'),
        namespace=str(os.environ.get('PIC_REGISTRY_URI')
                      or registry.get('namespace') or DEFAULT_NAMESPACE).rstrip('/'),
        offline=env_offline or bool(registry.get('offline') or
                                    login.get('offline') or False),
    )


def _reset_client() -> None:
    global _CLIENT
    _CLIENT = None


def _version_key(version: str) -> Tuple[int, int, int]:
    try:
        return tuple(int(part) for part in version.split('-')[0].split('.'))
    except ValueError:
        return (0, 0, 0)


def get_client(*, interactive: bool = True,
               open_browser: bool = True,
               force_login: bool = False) -> CdifitClient:
    """返回已登录客户端; 未登录时按需启动浏览器登录。"""
    global _CLIENT
    config = load_config()
    if config.offline:
        raise PicError('PIC_OFFLINE=1: 中央注册表已禁用')
    if _CLIENT is not None and not force_login:
        return _CLIENT
    store = AuthStore()
    state = store.load()
    if state is not None and state.api_base.rstrip('/') == config.api_base:
        client = CdifitClient(config.api_base, auth=state, store=store)
        try:
            client.ensure_token()
            _CLIENT = client
            return client
        except ApiError:
            # access/refresh 都失效 -> 重新登录
            store.clear()
    if not interactive:
        raise PicError('未登录 Cdifit; 请先运行 `pic login`')
    state = run_login_flow(config.api_base, store=store,
                           open_browser=open_browser)
    client = CdifitClient(config.api_base, auth=state, store=store)
    _CLIENT = client
    return client


def get_registry(*, interactive: bool = True,
                 open_browser: bool = True) -> Optional[Registry]:
    config = load_config()
    if config.offline:
        return None
    client = get_client(interactive=interactive, open_browser=open_browser)
    return Registry(client, config.namespace)


# ==================== 包文件与归档 ====================


def _strip_suffix(text: str, suffix: str) -> str:
    return text[:-len(suffix)] if text.endswith(suffix) else text


def _iter_package_files(root: str) -> Iterable[Tuple[str, str]]:
    return iter_package_files(root)


def build_package(root: str, out: Optional[str] = None) -> Tuple[str, PackageMeta]:
    """把包目录打成 ``.cinpkg`` (含 ``META-INF/SHA256SUMS``)。"""
    meta = _load_any_manifest(root)
    root = meta.root
    if out is None:
        out = os.path.join(root, 'dist',
                           f'{meta.name}-{meta.version}.cinpkg')
    files: Dict[str, bytes] = {}
    for rel, path in _iter_package_files(root):
        with open(path, 'rb') as f:
            files[rel] = f.read()
    # 清单以规范化形式写入, 避免发布后的包与内存中的 name/version 漂移
    files['pic.toml'] = toml_dumps(meta.to_dict()).encode('utf-8')
    entries = archive.build_archive_entries(files)
    archive.write_zip(out, entries)
    return os.path.abspath(out), meta


# ==================== 安装 ====================


def _meta_path(path: str) -> str:
    return os.path.join(path, _META_FILE)


def _read_meta(path: str) -> Dict[str, Any]:
    try:
        with open(_meta_path(path), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_meta(path: str, repo: str = '', rev: str = '',
                editable: bool = False, source: str = '') -> None:
    data = {'repo': repo, 'rev': rev, 'editable': editable,
            'source': source, 'installed_by': f'pic {__version__}'}
    with open(_meta_path(path), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def _read_installed_manifest(path: str) -> Optional[PackageMeta]:
    try:
        return load_package_manifest(resolve_link(path), require_entry=False)
    except (ManifestError, OSError):
        return None


def _load_any_manifest(root: str) -> PackageMeta:
    """读取 pic.toml; 也接受 mkn 项目 (自动生成等效包元数据)。"""
    pic_path = os.path.join(root, 'pic.toml')
    if os.path.isfile(pic_path):
        return load_package_manifest(root)
    mkn_path = os.path.join(root, 'mkn.toml')
    if os.path.isfile(mkn_path):
        from .packaging.model import load_mkn_manifest
        project = load_mkn_manifest(mkn_path)
        return PackageMeta(
            root=project.root, manifest_path=project.manifest_path,
            name=project.name, version=project.version,
            description=project.description, license=project.license,
            authors=list(project.authors), entry=project.entry,
            repo=project.repo, tag=project.tag,
            dependencies=list(project.dependencies), raw=project.raw)
    raise ManifestError(
        f'{root}: 既没有 pic.toml 也没有 mkn.toml, 无法作为包安装')


def _project_existing(target_root: str, name: str) -> Optional[PackageMeta]:
    path = os.path.join(target_root, name)
    if not os.path.isdir(path):
        return None
    return _read_installed_manifest(path)


def _check_expected(meta: PackageMeta, name: Optional[str],
                    version: Optional[str]) -> None:
    if name and meta.name != name:
        raise PicError(
            f'包名不匹配: 请求 {name!r}, 仓库清单为 {meta.name!r}')
    if version and meta.version != version:
        raise PicError(
            f'版本不匹配: 请求 {version!r}, 仓库清单为 {meta.version!r}')


def _install_into(meta: PackageMeta, store: PackageStore, *,
                  target_root: Optional[str], editable: bool, force: bool,
                  repo: str = '', rev: str = '', source: str = ''
                  ) -> InstalledPackage:
    if target_root:
        target_root = os.path.abspath(target_root)
        os.makedirs(target_root, exist_ok=True)
        dest = os.path.join(target_root, meta.name)
        existing = _read_installed_manifest(dest)
        if existing is not None and existing.version == meta.version \
                and not force:
            return InstalledPackage(
                name=meta.name, version=meta.version, path=dest,
                repo=repo, rev=rev, editable=editable)
        copy_package_tree(meta.root, dest, editable=editable)
        if not editable:
            _write_meta(dest, repo=repo, rev=rev, source=source or meta.root)
            if not os.path.isfile(os.path.join(dest, 'pic.toml')):
                with open(os.path.join(dest, 'pic.toml'), 'w',
                          encoding='utf-8', newline='\n') as f:
                    f.write(toml_dumps(meta.to_dict()))
        return InstalledPackage(name=meta.name, version=meta.version,
                                path=dest, repo=repo, rev=rev,
                                editable=editable)

    pkg = store.add(meta.root, meta.name, meta.version, repo=repo, rev=rev,
                    editable=editable, force=force)
    if not editable:
        _write_meta(pkg.path, repo=repo, rev=rev,
                    source=source or meta.root)
        if not os.path.isfile(os.path.join(pkg.path, 'pic.toml')):
            with open(os.path.join(pkg.path, 'pic.toml'), 'w',
                      encoding='utf-8', newline='\n') as f:
                f.write(toml_dumps(meta.to_dict()))
    return pkg


def _dependency_specs(meta: PackageMeta) -> List[Dependency]:
    return list(meta.dependencies)


def install_path(path: str, store: PackageStore, *,
                 target_root: Optional[str] = None, editable: bool = False,
                 force: bool = False, no_deps: bool = False,
                 expected_name: Optional[str] = None,
                 expected_version: Optional[str] = None,
                 repo: str = '', rev: str = '',
                 _seen: Optional[set] = None) -> InstalledPackage:
    """安装本地目录 / ``pic.toml`` / ``.cinpkg``。"""
    seen = _seen if _seen is not None else set()
    path = os.path.abspath(path)
    tmp_dir: Optional[str] = None
    try:
        if os.path.isfile(path) and path.lower().endswith(('.cinpkg', '.mkn')):
            # 不用 tempfile.mkdtemp: 部分受限环境会给 mkdtemp 目录加不可写 ACL
            tmp_dir = os.path.join(
                store.cache_dir, f'extract-{os.getpid()}-{uuid.uuid4().hex[:8]}')
            os.makedirs(tmp_dir, exist_ok=True)
            archive.safe_extract(path, tmp_dir)
            root = tmp_dir
        elif os.path.isfile(path) and os.path.basename(path) == 'pic.toml':
            root = os.path.dirname(path)
        elif os.path.isdir(path):
            root = path
        else:
            raise PicError(f'不是可安装的路径或 .cinpkg: {path}')

        meta = _load_any_manifest(root)
        _check_expected(meta, expected_name, expected_version)
        key = f'{meta.name}=={meta.version}'
        if key in seen:
            existing = store.get(meta.name, meta.version)
            if existing is not None:
                return existing
        seen.add(key)

        installed = _install_into(
            meta, store, target_root=target_root, editable=editable,
            force=force, repo=repo, rev=rev, source=root)

        if not no_deps:
            for dep in _dependency_specs(meta):
                _install_dependency(dep, store, target_root=target_root,
                                    force=force, seen=seen)
        return installed
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _cache_key(url: str, ref: Optional[str]) -> str:
    import hashlib
    digest = hashlib.sha256(f'{url}\0{ref or ""}'.encode()).hexdigest()[:24]
    return digest


def install_git(url: str, store: PackageStore, *,
                ref: Optional[str] = None, target_root: Optional[str] = None,
                editable: bool = False, force: bool = False,
                no_deps: bool = False, expected_name: Optional[str] = None,
                expected_version: Optional[str] = None,
                _seen: Optional[set] = None) -> InstalledPackage:
    """从 Git 仓库安装 (浅克隆到缓存后复制)。"""
    if not vcs.git_available():
        raise PicError('未找到 git 命令; 从 Git 安装需要先安装 Git')
    seen = _seen if _seen is not None else set()
    url, inline_ref = vcs.split_git_spec(url)
    ref = ref or inline_ref
    cache = os.path.join(store.cache_dir, 'git', _cache_key(url, ref))
    if force or not os.path.isdir(cache):
        shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        vcs.clone(url, cache, ref=ref)
    meta = _load_any_manifest(cache)
    _check_expected(meta, expected_name, expected_version)
    rev = ''
    with contextlib.suppress(vcs.VcsError):
        rev = vcs.current_rev(cache)
    key = f'{meta.name}=={meta.version}'
    if key in seen:
        existing = store.get(meta.name, meta.version)
        if existing is not None:
            return existing
    seen.add(key)
    installed = _install_into(
        meta, store, target_root=target_root, editable=editable, force=force,
        repo=url, rev=rev, source=cache)
    if not no_deps:
        for dep in _dependency_specs(meta):
            _install_dependency(dep, store, target_root=target_root,
                                force=force, seen=seen)
    return installed


def _tag_version(tag: str) -> Optional[str]:
    text = tag.strip()
    if text.lower().startswith('v'):
        text = text[1:]
    return text if re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?', text) else None


def _choose_tag(url: str, constraint: str) -> Optional[str]:
    """从远端标签中选一个满足约束的最高版本标签。"""
    try:
        tags = vcs.list_tags(url=url)
    except vcs.VcsError:
        return None
    candidates: List[Tuple[Tuple[int, int, int], str]] = []
    for tag in tags:
        version = _tag_version(tag)
        if version and satisfies(version, constraint):
            candidates.append((tuple(int(x) for x in version.split('-')[0].split('.')),
                               tag))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def install_named(name: str, constraint: str, store: PackageStore, *,
                  target_root: Optional[str] = None, editable: bool = False,
                  force: bool = False, no_deps: bool = False,
                  _seen: Optional[set] = None) -> InstalledPackage:
    """按名称 + 版本约束安装 (注册表 -> Git 仓库)。"""
    validate_name(name)
    # 1) 已安装且满足约束: 直接复用
    if target_root:
        existing = _project_existing(target_root, name)
        if existing is not None and satisfies(existing.version, constraint) \
                and not force:
            return InstalledPackage(name=name, version=existing.version,
                                    path=os.path.join(target_root, name))
    else:
        for version in store.versions(name):
            if satisfies(version, constraint):
                pkg = store.get(name, version)
                if pkg is not None:
                    return pkg

    # 2) 中央注册表 (在线) / 本地注册表 (PIC_OFFLINE)
    config = load_config()
    registry = None if config.offline else get_registry(interactive=True)
    if registry is not None:
        installed = None
        try:
            repo, version, registered = registry.resolve(name, constraint)
            ref = str(registered.get('tag')
                      or registered.get('rev') or '') or None
            installed = install_git(
                repo, store, ref=ref, target_root=target_root,
                editable=editable, force=force, no_deps=no_deps,
                expected_name=name, _seen=_seen)
            meta = _read_installed_manifest(installed.real_path)
            if meta is None:
                raise RegistryError(
                    f'安装后无法读取包清单: {installed.path}')
            Registry.verify_install(meta, registered, rev=installed.rev)
            return installed
        except RegistryError as e:
            if installed is not None:
                if target_root:
                    shutil.rmtree(os.path.join(target_root, name),
                                  ignore_errors=True)
                else:
                    store.remove(name, installed.version)
            raise PicError(str(e)) from e

    entry = store.lookup(name)
    if not entry or not entry.get('repo'):
        raise PicError(
            f'未找到包 {name!r} 的仓库。\n'
            f'  在线: 请确认包已通过 `pic publish` 登记到 Cdifit 中央注册表;\n'
            f'  离线: 可用 `pic install git+<url>` 直接安装, 或在 pic.toml 中声明 '
            f'`[dependencies.{name}] git = "..."`;\n'
            f'  也可用 `pic registry add <url> --name {name}` 登记本地名称。')
    repo = str(entry['repo'])
    ref = _choose_tag(repo, constraint) if constraint else None
    installed = install_git(repo, store, ref=ref, target_root=target_root,
                            editable=editable, force=force,
                            no_deps=no_deps, expected_name=name,
                            _seen=_seen)
    if constraint and not satisfies(installed.version, constraint):
        if target_root:
            shutil.rmtree(os.path.join(target_root, name), ignore_errors=True)
        else:
            store.remove(name, installed.version)
        raise PicError(
            f'{name} 的最新可选版本 {installed.version} 不满足约束 '
            f'{constraint!r}; 请检查仓库标签或放宽版本范围')
    return installed


def _install_dependency(dep: Dependency, store: PackageStore, *,
                        target_root: Optional[str], force: bool,
                        seen: set) -> InstalledPackage:
    if dep.path:
        return install_path(dep.path, store, target_root=target_root,
                            force=force, expected_name=dep.name, _seen=seen)
    if dep.git:
        return install_git(dep.git, store, ref=dep.tag or dep.rev or None,
                           target_root=target_root, force=force,
                           expected_name=dep.name, _seen=seen)
    return install_named(dep.name, dep.constraint, store,
                         target_root=target_root, force=force, _seen=seen)


def _split_name_version(spec: str) -> Tuple[str, str]:
    text = spec.strip()
    if '==' in text:
        name, version = text.split('==', 1)
        return name.strip(), version.strip()
    if '@' in text and not text.startswith('git@'):
        name, version = text.rsplit('@', 1)
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', name):
            return name, version.strip()
    return text, ''


def install_spec(spec: str, store: PackageStore, *,
                 target_root: Optional[str] = None, editable: bool = False,
                 force: bool = False, no_deps: bool = False,
                 name: Optional[str] = None, version: Optional[str] = None,
                 ref: Optional[str] = None,
                 _seen: Optional[set] = None) -> InstalledPackage:
    """安装任意规格: 本地路径 / .cinpkg / git+URL / name@version。"""
    seen = _seen if _seen is not None else set()
    text = str(spec or '').strip()
    if not text:
        raise PicError('空的安装规格')

    if text.startswith('path:'):
        return install_path(text[len('path:'):], store,
                            target_root=target_root, editable=editable,
                            force=force, no_deps=no_deps,
                            expected_name=name, _seen=seen)
    if os.path.exists(text):
        return install_path(text, store, target_root=target_root,
                            editable=editable, force=force, no_deps=no_deps,
                            expected_name=name, _seen=seen)
    if vcs.is_git_spec(text):
        return install_git(text, store, ref=ref, target_root=target_root,
                           editable=editable, force=force, no_deps=no_deps,
                           expected_name=name, expected_version=version,
                           _seen=seen)
    pkg_name, pkg_version = _split_name_version(text)
    if name:
        pkg_name = name
    if version:
        pkg_version = version
    return install_named(pkg_name, pkg_version, store,
                         target_root=target_root, editable=editable,
                         force=force, no_deps=no_deps, _seen=seen)


# ==================== 项目锁文件 ====================


def _scan_project_packages(target_root: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(target_root):
        return out
    for name in sorted(os.listdir(target_root)):
        path = os.path.join(target_root, name)
        if not os.path.isdir(path):
            continue
        meta = _read_installed_manifest(path)
        if meta is None:
            continue
        info = _read_meta(path)
        out.append({
            'name': meta.name,
            'version': meta.version,
            'path': os.path.relpath(resolve_link(path), target_root).replace(
                os.sep, '/'),
            'repo': info.get('repo', ''),
            'rev': info.get('rev', ''),
            'editable': bool(info.get('editable', False)),
        })
    return out


def write_project_lock(project_root: str,
                       target_root: Optional[str] = None) -> Optional[str]:
    """把 ``pic_modules`` 的解析结果写入 ``pic.lock``。"""
    project_root = os.path.abspath(project_root)
    target_root = target_root or project_modules_dir(project_root)
    packages = _scan_project_packages(target_root)
    if not packages:
        return None
    data = {'lock-version': 1, 'package': packages}
    path = os.path.join(project_root, 'pic.lock')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(toml_dumps(data))
    return path


# ==================== 命令实现 ====================


def _guard_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


def cmd_init(args) -> int:
    root = os.path.abspath(args.path or os.getcwd())
    name = args.name or os.path.basename(root.rstrip('\\/')) or 'my-package'
    name = re.sub(r'[^A-Za-z0-9_-]', '-', name)
    if not re.match(r'^[A-Za-z]', name):
        name = 'pkg-' + name
    validate_name(name)
    os.makedirs(root, exist_ok=True)
    manifest = os.path.join(root, 'pic.toml')
    if os.path.isfile(manifest) and not args.force:
        raise PicError(f'{manifest} 已存在 (使用 --force 覆盖)')
    entry_rel = args.entry or f'src/{name}.cin'
    entry_path = os.path.join(root, entry_rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(entry_path), exist_ok=True)
    package = {
        'package': {
            'name': name,
            'version': args.version,
            'description': args.description or f'{name} package for Code CIN',
            'license': args.license,
            'entry': entry_rel.replace(os.sep, '/'),
            'authors': [],
        },
    }
    if args.repo:
        package['package']['repo'] = args.repo
    with open(manifest, 'w', encoding='utf-8', newline='\n') as f:
        f.write(toml_dumps(package))
    if not os.path.isfile(entry_path):
        fn = _guard_name(name)
        with open(entry_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(
                f'// {name} - Code CIN 包入口\n'
                f'// 其他项目可 `import "{name}"` 直接使用本文件中的函数。\n\n'
                f'function {fn}_hello() -> string {{\n'
                f'    return "hello from {name}"\n'
                f'}}\n')
    readme = os.path.join(root, 'README.md')
    if not os.path.isfile(readme):
        with open(readme, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'# {name}\n\nCode CIN package `{name}`.\n\n'
                    f'```bash\npic install git+<repo-url>\n```\n')
    gitignore = os.path.join(root, '.gitignore')
    if not os.path.isfile(gitignore):
        with open(gitignore, 'w', encoding='utf-8', newline='\n') as f:
            f.write('dist/\n*.cinpkg\npic_modules/\n__pycache__/\n')
    print(f'Created package {name} {args.version} at {root}')
    print(f'  manifest: {manifest}')
    print(f'  entry   : {entry_path}')
    return 0


def cmd_build(args) -> int:
    root = _manifest_root(args.manifest)
    out, meta = build_package(root, args.output)
    print(f'Built {meta.name} {meta.version}: {out}')
    return 0


def _manifest_root(path: Optional[str]) -> str:
    if path:
        path = os.path.abspath(path)
        return os.path.dirname(path) if os.path.isfile(path) else path
    return os.getcwd()


def cmd_publish(args) -> int:
    root = _manifest_root(args.manifest)
    manifest_path = os.path.join(root, 'pic.toml')
    meta = _load_any_manifest(root)
    root = meta.root
    if args.name and args.name != meta.name:
        validate_name(args.name)
        meta.name = args.name
    if args.version:
        validate_version(args.version)
        meta.version = args.version
    if args.repo:
        meta.repo = args.repo
    if not meta.repo:
        raise PicError(
            '发布需要 Git 仓库地址: 使用 `--repo <git-url>` 或在 pic.toml '
            '的 [package].repo 中填写')
    if not vcs.git_available():
        raise PicError('未找到 git 命令; 发布需要 Git')

    config = load_config()
    registry = None if config.offline else get_registry(interactive=True)

    if vcs.is_repo(root):
        if not args.allow_dirty and not vcs.is_clean(root):
            raise PicError(
                'Git 工作区不干净; 先提交改动, 或使用 --allow-dirty 强制发布')
    elif args.push:
        raise PicError('--push 需要在 Git 仓库内运行 (未找到 .git)')

    # 回写规范化清单 (name/version/repo/tag)。注意: 这一步会修改工作区,
    # 因此 --push 要求用户先把清单改动提交, 保证标签指向的提交包含发布元数据。
    rewrite = bool(args.repo or args.name or args.version or args.tag)
    if rewrite:
        if args.tag:
            meta.tag = args.tag
        with open(manifest_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(toml_dumps(meta.to_dict()))
        if args.push and vcs.is_repo(root) and not args.allow_dirty \
                and not vcs.is_clean(root):
            raise PicError(
                '发布元数据已写入 pic.toml, 但工作区随之变脏; '
                '请先提交该文件, 再重新运行 --push')

    out, meta = build_package(root, args.output)
    tag = args.tag or f'v{meta.version}'
    pushed = False
    if vcs.is_repo(root) and not args.no_tag:
        created = vcs.create_tag(root, tag,
                                 message=f'{meta.name} {meta.version}')
        if created:
            print(f'Tagged {tag}')
        else:
            print(f'Tag {tag} already exists')
        if args.push:
            remote = args.remote or 'origin'
            try:
                vcs.push_tag(root, tag, remote=remote)
                pushed = True
            except vcs.VcsError as e:
                raise PicError(f'推送标签失败: {e}') from e

    store = default_store()
    store.register(meta.name, meta.repo, meta.version, tag)

    if registry is not None:
        rev = ''
        if vcs.is_repo(root):
            with contextlib.suppress(vcs.VcsError):
                rev = vcs.current_rev(root)
        registry.publish(meta, repo=meta.repo, tag=tag, rev=rev,
                         archive_path=out)
        print(f'  central: {config.namespace}/packages/{meta.name}.json')

    print(f'Published {meta.name} {meta.version}: {out}')
    print(f'  repo : {meta.repo}')
    print(f'  tag  : {tag}' + (' (pushed)' if pushed else ''))
    print(f'  sha256: {archive.sha256_file(out)}')

    if args.index:
        index_dir = os.path.abspath(args.index)
        os.makedirs(index_dir, exist_ok=True)
        entry = {
            'package': {
                'name': meta.name,
                'repo': meta.repo,
                'latest': meta.version,
            },
            'versions': {
                meta.version: {
                    'tag': tag,
                    'file': os.path.basename(out),
                    'sha256': archive.sha256_file(out),
                },
            },
        }
        indexPath = os.path.join(index_dir, f'{meta.name}.toml')
        with open(indexPath, 'w', encoding='utf-8', newline='\n') as f:
            f.write(toml_dumps(entry))
        print(f'  index: {indexPath}')
    return 0


def _project_root() -> str:
    from .packaging.model import find_project_root
    return find_project_root(os.getcwd()) or os.getcwd()


def _target_root(args, store: PackageStore) -> Optional[str]:
    if getattr(args, 'project', False):
        return project_modules_dir(_project_root())
    return None


def cmd_install(args) -> int:
    store = default_store()
    target_root = _target_root(args, store)
    installed: List[InstalledPackage] = []

    specs = list(args.specs)
    if not specs:
        manifest_path = os.path.join(os.getcwd(), 'pic.toml')
        if not os.path.isfile(manifest_path):
            raise PicError('未指定包, 且当前目录没有 pic.toml')
        meta = load_package_manifest(os.getcwd())
        seen: set = set()
        for dep in meta.dependencies:
            installed.append(_install_dependency(
                dep, store, target_root=target_root, force=args.force,
                seen=seen))
    else:
        for spec in specs:
            installed.append(install_spec(
                spec, store, target_root=target_root, editable=args.editable,
                force=args.force, no_deps=args.no_deps, name=args.name,
                version=args.version, ref=args.ref))

    if target_root:
        lock = write_project_lock(_project_root(), target_root)
        if lock:
            print(f'Lockfile: {lock}')
    for pkg in installed:
        print(f'Installed {pkg.name} {pkg.version} -> {pkg.path}')
    return 0


def cmd_uninstall(args) -> int:
    store = default_store()
    if args.project:
        target = os.path.join(project_modules_dir(_project_root()), args.name)
        if not os.path.isdir(target):
            raise PicError(f'项目未安装: {args.name}')
        shutil.rmtree(target)
        write_project_lock(_project_root())
        print(f'Removed {args.name} from pic_modules')
        return 0
    if not store.remove(args.name, args.version):
        raise PicError(f'未安装: {args.name}'
                       + (f'=={args.version}' if args.version else ''))
    print(f'Removed {args.name}'
          + (f' {args.version}' if args.version else ''))
    return 0


def cmd_list(args) -> int:
    store = default_store()
    if args.project:
        target = project_modules_dir(_project_root())
        packages = _scan_project_packages(target)
        if args.json:
            print(json.dumps(packages, ensure_ascii=False, indent=2))
            return 0
        if not packages:
            print('No packages installed in this project.')
            return 0
        print('NAME                 VERSION     SOURCE')
        for pkg in packages:
            print(f'{pkg["name"]:<20} {pkg["version"]:<11} '
                  f'{pkg.get("repo") or pkg.get("path", "")}')
        return 0
    packages = store.all()
    if args.json:
        data = []
        for p in packages:
            item = p.to_dict()
            item['name'] = p.name
            item['version'] = p.version
            data.append(item)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not packages:
        print('No packages installed.')
        return 0
    print('NAME                 VERSION     SOURCE')
    for pkg in packages:
        source = pkg.repo or ('editable:' + pkg.real_path if pkg.editable
                              else pkg.path)
        print(f'{pkg.name:<20} {pkg.version:<11} {source}')
    return 0


def cmd_show(args) -> int:
    store = default_store()
    pkg = store.get(args.name, args.version)
    if pkg is None and args.project:
        path = os.path.join(project_modules_dir(_project_root()), args.name)
        meta = _read_installed_manifest(path)
        if meta:
            pkg = InstalledPackage(meta.name, meta.version, path)
    if pkg is None:
        raise PicError(f'未安装: {args.name}')
    meta = _read_installed_manifest(pkg.real_path)
    data = {
        'name': pkg.name,
        'version': pkg.version,
        'path': pkg.real_path,
        'repo': pkg.repo or _read_meta(pkg.real_path).get('repo', ''),
        'rev': pkg.rev or _read_meta(pkg.real_path).get('rev', ''),
        'editable': pkg.editable,
        'entry': (meta.entry_rel if meta else ''),
        'dependencies': ([d.spec for d in meta.dependencies] if meta else []),
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for key, value in data.items():
            if isinstance(value, list):
                value = ', '.join(value) if value else '-'
            print(f'{key:<12}: {value}')
    return 0


def cmd_freeze(args) -> int:
    store = default_store()
    if args.project:
        lock = write_project_lock(_project_root())
        if lock is None:
            print('No packages installed in this project.')
            return 0
        print(lock)
        return 0
    lines = ['# pic freeze (global)', '']
    for pkg in store.all():
        lines.append(f'{pkg.name}=={pkg.version}'
                     + (f'  # {pkg.repo}' if pkg.repo else ''))
    text = '\n'.join(lines) + '\n'
    if args.output:
        with open(args.output, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print(args.output)
    else:
        sys.stdout.write(text)
    return 0


def cmd_search(args) -> int:
    config = load_config()
    query = (args.query or '').lower()
    rows = []
    if not config.offline:
        registry = get_registry(interactive=True)
        for record in registry.list_packages():
            name = str(record.get('name') or '')
            repo = str(record.get('repo') or '')
            if query and query not in name.lower() and query not in repo.lower():
                continue
            versions = list((record.get('versions') or {}).keys())
            latest = max(versions, key=_version_key) if versions else '-'
            rows.append((name, repo, latest))
    else:
        data = default_store().load_registry().get('packages', {})
        for name, entry in sorted(data.items()):
            if query and query not in name.lower() and query not in str(
                    entry.get('repo', '')).lower():
                continue
            versions = list((entry.get('versions') or {}).keys())
            rows.append((name, entry.get('repo', ''),
                         versions[-1] if versions else '-'))
    if not rows:
        print('No matching packages in registry.')
        return 0
    print('NAME                 LATEST      REPO')
    for name, repo, latest in sorted(rows):
        print(f'{name:<20} {latest:<11} {repo}')
    return 0


def cmd_registry(args) -> int:
    store = default_store()
    if args.action == 'add':
        raw_name = args.name
        url = args.url
        if url is None:
            url = raw_name
            raw_name = args.option_name
        if not url:
            raise PicError('registry add 需要 Git 仓库地址: '
                           'pic registry add <name> <git-url>')
        name = raw_name or args.option_name
        if not name:
            name = os.path.basename(_strip_suffix(url.rstrip('/'), '.git'))
        store.register(name, url)
        print(f'Registered {name} -> {url} (local fallback)')
        return 0
    if args.action == 'remove':
        data = store.load_registry()
        if data.get('packages', {}).pop(args.name, None) is None:
            raise PicError(f'本地注册表没有 {args.name}')
        store.save_registry(data)
        print(f'Unregistered {args.name}')
        return 0

    config = load_config()
    if not config.offline:
        registry = get_registry(interactive=True)
        records = registry.list_packages()
        if not records:
            print('Central registry is empty.')
            return 0
        print('NAME                 LATEST      REPO')
        for record in sorted(records, key=lambda item: item.get('name', '')):
            versions = list((record.get('versions') or {}).keys())
            latest = max(versions, key=_version_key) if versions else '-'
            print(f'{record.get("name", ""):<20} {latest:<11} '
                  f'{record.get("repo", "")}')
        return 0

    data = store.load_registry().get('packages', {})
    if not data:
        print('Local registry is empty.')
        return 0
    print('NAME                 REPO')
    for name, entry in sorted(data.items()):
        print(f'{name:<20} {entry.get("repo", "")}')
    return 0


def cmd_login(args) -> int:
    config = load_config()
    if args.api_base:
        config.api_base = args.api_base.rstrip('/')
    store = AuthStore()
    existing = store.load()
    if existing is not None and not args.force \
            and existing.api_base.rstrip('/') == config.api_base:
        client = CdifitClient(config.api_base, auth=existing, store=store)
        try:
            user = client.userinfo()
            print(f'Already logged in as {existing.display_name} '
                  f'<{existing.email}>')
            return 0
        except ApiError:
            store.clear()

    if args.email:
        email = args.email
        password = args.password
        if not password:
            import getpass
            password = getpass.getpass('Cdifit 密码: ')
        client = CdifitClient(config.api_base, store=store)
        try:
            state = client.login(email, password)
        except TwoFactorRequired as e:
            if not args.code:
                raise PicError(
                    '该账号启用了两步验证; 请追加 `--code <验证码>` 重新运行, '
                    '或使用默认浏览器登录流程') from e
            state = client.finish_2fa(e.session, args.code)
    else:
        state = run_login_flow(config.api_base, store=store,
                               timeout=args.timeout,
                               open_browser=not args.no_browser)
    _reset_client()
    print(f'Logged in as {state.display_name} <{state.email}>')
    print(f'  api: {state.api_base}')
    print(f'  auth: {store.path}')
    return 0


def cmd_logout(args) -> int:
    store = AuthStore()
    client = client_from_store()
    if client is not None:
        client.logout()
    else:
        store.clear()
    _reset_client()
    print('Logged out.')
    return 0


def cmd_whoami(args) -> int:
    client = get_client(interactive=True)
    user = client.userinfo()
    if args.json:
        print(json.dumps(user, ensure_ascii=False, indent=2))
        return 0
    auth = client.auth
    print(f'name : {auth.display_name if auth else ""}')
    print(f'email: {auth.email if auth else ""}')
    print(f'id   : {user.get("sub") or (auth.user.get("id") if auth else "")}')
    print(f'api  : {client.api_base}')
    return 0


def cmd_doctor(args) -> int:
    store = default_store()
    config = load_config()
    auth = AuthStore().load()
    print(f'pic {__version__}')
    print(f'home       : {store.root}')
    print(f'git        : {shutil.which("git") or "NOT FOUND"}')
    print(f'packages   : {len(store.all())}')
    print(f'api base   : {config.api_base}')
    print(f'namespace  : {config.namespace}')
    print(f'offline    : {config.offline}')
    print(f'login      : ' + (
        f'{auth.display_name} <{auth.email}>' if auth else 'not logged in'))
    print(f'local reg  : {len(store.load_registry().get("packages", {}))}')
    return 0


# ==================== argparse ====================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='pic',
        description='Code CIN 包管理器 (Cdifit 中央注册表 + Git 内容源, '
                    '安装后可直接 import)')
    parser.add_argument('--version', action='version',
                        version=f'pic {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('login', help='登录 Cdifit (默认打开浏览器)')
    p.add_argument('--email', help='直接使用邮箱登录 (不打开浏览器)')
    p.add_argument('--password', help='密码 (建议省略, 改用交互式输入)')
    p.add_argument('--code', help='两步验证码 (与 --email/--password 同用)')
    p.add_argument('--api-base', help='覆盖 Cdifit API 地址')
    p.add_argument('--no-browser', action='store_true',
                   help='不自动打开浏览器, 只打印临时登录地址')
    p.add_argument('--timeout', type=float, default=300.0,
                   help='浏览器登录超时秒数 (默认 300)')
    p.add_argument('--force', action='store_true', help='强制重新登录')
    p.set_defaults(func=cmd_login)

    p = sub.add_parser('logout', help='退出 Cdifit 登录')
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser('whoami', help='显示当前登录用户')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser('init', help='创建 pic.toml 与包入口')
    p.add_argument('name', nargs='?', help='包名 (默认目录名)')
    p.add_argument('--path', help='包目录 (默认当前目录)')
    p.add_argument('--version', default='0.1.0', help='初始版本 (默认 0.1.0)')
    p.add_argument('--entry', help='入口文件 (默认 src/<name>.cin)')
    p.add_argument('--description', default='')
    p.add_argument('--license', default='MIT')
    p.add_argument('--repo', help='Git 仓库地址')
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('build', help='生成 .cinpkg 包归档')
    p.add_argument('--manifest', help='pic.toml 或包目录')
    p.add_argument('-o', '--output', help='输出 .cinpkg 路径')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('publish', help='发布到中央注册表 (Cdifit)')
    p.add_argument('--manifest', help='pic.toml 或包目录')
    p.add_argument('--repo', help='Git 仓库地址')
    p.add_argument('--name', help='包标识名')
    p.add_argument('--version', help='发布版本')
    p.add_argument('--tag', help='Git 标签 (默认 v<version>)')
    p.add_argument('--no-tag', action='store_true', help='不创建 Git 标签')
    p.add_argument('--push', action='store_true', help='推送标签到远端')
    p.add_argument('--remote', default='origin')
    p.add_argument('--allow-dirty', action='store_true')
    p.add_argument('--index', help='额外写入本地分散索引目录')
    p.add_argument('-o', '--output', help='包归档输出路径')
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser('install', help='安装包')
    p.add_argument('specs', nargs='*',
                   help='包规格: name / name@version / path / git+URL / .cinpkg')
    p.add_argument('-e', '--editable', action='store_true',
                   help='可编辑安装 (仅本地路径)')
    p.add_argument('--project', action='store_true',
                   help='安装到当前项目 pic_modules/ (可直接 import)')
    p.add_argument('--name', help='覆盖期望包名')
    p.add_argument('--version', help='覆盖期望版本')
    p.add_argument('--ref', help='Git 引用 (标签/分支/提交)')
    p.add_argument('--force', action='store_true')
    p.add_argument('--no-deps', action='store_true', help='不安装依赖')
    p.set_defaults(func=cmd_install)

    p = sub.add_parser('uninstall', help='卸载包')
    p.add_argument('name')
    p.add_argument('--version')
    p.add_argument('--project', action='store_true')
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser('list', help='列出已安装包')
    p.add_argument('--project', action='store_true')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('show', help='查看包详情')
    p.add_argument('name')
    p.add_argument('--version')
    p.add_argument('--project', action='store_true')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_show)

    p = sub.add_parser('freeze', help='输出锁定信息')
    p.add_argument('--project', action='store_true')
    p.add_argument('-o', '--output')
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser('search', help='搜索中央注册表 (离线时搜索本地)')
    p.add_argument('query', nargs='?')
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('registry', help='本地回退注册表 / 中央列表')
    p.add_argument('action', choices=['add', 'remove', 'list'])
    p.add_argument('name', nargs='?', help='add: 包名 (或只给 URL); remove: 包名')
    p.add_argument('url', nargs='?', help='add: Git 仓库地址')
    p.add_argument('--name', dest='option_name', help='add: 包标识名')
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser('doctor', help='检查环境')
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .toolkit import configure_stdio
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        # 首次使用门禁: 除登录/退出/诊断外, 没有本地 auth.json 时先走浏览器
        # 登录。PIC_OFFLINE=1 或 config.toml 的 offline=true 可绕过 (离线开发)。
        config = load_config()
        if not config.offline and args.command not in (
                'login', 'logout', 'doctor'):
            if AuthStore().load() is None:
                get_client(interactive=True)
        return int(args.func(args) or 0)
    except (PicError, ManifestError, archive.ArchiveError,
            vcs.VcsError, ApiError, AuthError, RegistryError, OSError) as e:
        sys.stderr.write(f'pic: error: {e}\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
