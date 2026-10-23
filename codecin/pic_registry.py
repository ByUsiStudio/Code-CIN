"""pic 中央注册表 (Cdifit 文件 API)。

注册表存放在用户 Cdifit 网盘的 ``cloudreve://my/pic/v1`` 下::

    packages/<name>.json      # 标识名、Git 获取地址、版本列表、安全摘要
    index.json                # 名称索引 (便于搜索)

每个版本记录包含:

* ``tag`` / ``rev``: 从哪个 Git 标签/提交安装;
* ``source_sha256``: 对包源码树 (排除 .git/dist/target/pic_modules) 的确定性摘要;
* ``manifest_sha256``: 规范化 ``pic.toml`` 的摘要;
* ``archive_sha256``: 发布时 ``.cinpkg`` 的摘要 (若生成);
* ``publisher`` / ``published_at``: 发布者与时间。

安装时重新计算源码/清单摘要并与注册表比对, 不匹配则拒绝安装。这提供了
"获取地址 + 版本列表 + 内容校验" 的完整链路, 且不要求中心服务器存储二进制。
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .packaging.model import PackageMeta, satisfies, validate_name
from .packaging.toml import dumps as toml_dumps
from .pic_api import ApiError, CdifitClient

__all__ = [
    'DEFAULT_NAMESPACE', 'REGISTRY_SCHEMA', 'Registry', 'RegistryError',
    'iter_package_files', 'manifest_digest', 'source_digest',
]

DEFAULT_NAMESPACE = 'cloudreve://my/pic/v1'
REGISTRY_SCHEMA = 'pic.registry/v1'
INDEX_SCHEMA = 'pic.index/v1'

_IGNORED_DIRS = frozenset({
    '.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.ruff_cache',
    'dist', 'build', 'target', 'pic_modules', '.pic', '.mkn',
    'node_modules', '.venv', 'venv',
})


class RegistryError(RuntimeError):
    """注册表读写/校验失败。"""


def iter_package_files(root: str) -> Iterable[Tuple[str, str]]:
    """遍历包内文件 (与 ``pic build`` 使用同一排除规则)。"""
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(('.pyc', '.cinpkg', '.mkn', '.cvm')):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            yield rel, path


def _digest(parts: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()


def source_digest(root: str) -> str:
    """源码树摘要: 路径 + 长度 + 内容的 SHA-256, 与平台无关。"""
    h = hashlib.sha256()
    for rel, path in iter_package_files(root):
        if rel in ('pic.toml', '.pic-meta.json', '.mkn-meta.json'):
            continue
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as e:
            raise RegistryError(f'读取源码文件失败: {path}: {e}') from e
        normalized = data.replace(b'\r\n', b'\n')
        h.update(rel.encode('utf-8'))
        h.update(b'\0')
        h.update(str(len(normalized)).encode('ascii'))
        h.update(b'\0')
        # 统一换行, 避免 Windows core.autocrlf 导致同一提交摘要不同
        h.update(normalized)
    return h.hexdigest()


def manifest_digest(meta: PackageMeta) -> str:
    """规范化清单摘要 (不依赖 TOML 排版/注释)。

    ``repo`` / ``tag`` 属于发布位置元数据, 会随发布流程变化 (Git 提交里可能
    尚未回写), 因此不参与内容摘要; 名称/版本/入口/依赖仍被覆盖。
    """
    data = meta.to_dict()
    package = data.get('package')
    if isinstance(package, dict):
        package.pop('repo', None)
        package.pop('tag', None)
    text = toml_dumps(data)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _version_key(version: str) -> Tuple[int, int, int]:
    try:
        return tuple(int(part) for part in version.split('-')[0].split('.'))
    except ValueError:
        return (0, 0, 0)


def _highest(versions: Iterable[str]) -> str:
    return max(versions, key=_version_key)


class Registry:
    """Cdifit 上的 pic 中央注册表。"""

    def __init__(self, client: CdifitClient,
                 namespace: str = DEFAULT_NAMESPACE):
        if not namespace.startswith('cloudreve://'):
            raise RegistryError(
                f'注册表 namespace 必须是 cloudreve:// URI: {namespace!r}')
        self.client = client
        self.namespace = namespace.rstrip('/')

    # ---------------- URI ----------------

    @property
    def packages_uri(self) -> str:
        return self.namespace + '/packages'

    @property
    def index_uri(self) -> str:
        return self.namespace + '/index.json'

    def package_uri(self, name: str) -> str:
        return f'{self.packages_uri}/{validate_name(name)}.json'

    # ---------------- 布局 ----------------

    def ensure_layout(self) -> None:
        self.client.create_folder(self.namespace)
        self.client.create_folder(self.packages_uri)

    # ---------------- 读取 ----------------

    def get_package(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            data = self.client.read_json(self.package_uri(name))
        except ApiError as e:
            raise RegistryError(f'读取包记录失败 {name}: {e}') from e
        if data is None:
            return None
        if data.get('schema') != REGISTRY_SCHEMA:
            raise RegistryError(
                f'包记录 schema 不兼容: {data.get("schema")!r} '
                f'(需要 {REGISTRY_SCHEMA})')
        if data.get('name') != validate_name(name):
            raise RegistryError(
                f'包记录名称不匹配: 期望 {name!r}, 实际 {data.get("name")!r}')
        if not isinstance(data.get('versions'), dict):
            raise RegistryError(f'包记录缺少 versions: {name}')
        return data

    def read_index(self) -> Dict[str, Any]:
        data = self.client.read_json(self.index_uri)
        if data is None:
            return {'schema': INDEX_SCHEMA, 'updated_at': _now(),
                    'packages': {}}
        if data.get('schema') != INDEX_SCHEMA:
            raise RegistryError(
                f'索引 schema 不兼容: {data.get("schema")!r}')
        if not isinstance(data.get('packages'), dict):
            data['packages'] = {}
        return data

    def list_packages(self) -> List[Dict[str, Any]]:
        try:
            files = self.client.list_files(self.packages_uri)
        except ApiError as e:
            if e.status == 404 or e.code in (404, 40401, 40402):
                return []
            raise RegistryError(f'读取包列表失败: {e}') from e
        out: List[Dict[str, Any]] = []
        for item in files:
            name = str(item.get('name') or '')
            if not name.endswith('.json'):
                continue
            name = name[:-5]
            try:
                record = self.get_package(name)
            except RegistryError:
                continue
            if record:
                out.append(record)
        return out

    # ---------------- 写入 ----------------

    def _publisher(self) -> Dict[str, Any]:
        user = self.client.auth.user if self.client.auth else {}
        return {
            'id': user.get('id') or '',
            'email': user.get('email') or '',
            'nickname': user.get('nickname') or user.get('name') or '',
        }

    def publish(self, meta: PackageMeta, *, repo: str, tag: str,
                rev: str = '', archive_path: Optional[str] = None,
                source_hash: Optional[str] = None,
                manifest_hash: Optional[str] = None) -> Dict[str, Any]:
        """写入/合并一个版本记录, 并更新名称索引。"""
        if not repo:
            raise RegistryError('发布需要 Git 仓库地址')
        self.ensure_layout()
        record = self.get_package(meta.name) or {
            'schema': REGISTRY_SCHEMA,
            'name': meta.name,
            'repo': repo,
            'created_at': _now(),
            'versions': {},
        }
        if record.get('repo') and record['repo'] != repo:
            # 同名包换仓库属于破坏性变更, 要求显式处理
            raise RegistryError(
                f'包 {meta.name} 已登记为 {record["repo"]}, 与本次 {repo} 不同; '
                f'请先在 Cdifit 中删除旧记录或使用其他标识名')
        record['repo'] = repo
        record['updated_at'] = _now()
        versions = record.setdefault('versions', {})
        previous = versions.get(meta.version) or {}
        digest_source = source_hash or source_digest(meta.root)
        digest_manifest = manifest_hash or manifest_digest(meta)
        entry = {
            'tag': tag,
            'rev': rev or previous.get('rev', ''),
            'source_sha256': digest_source,
            'manifest_sha256': digest_manifest,
            'published_at': _now(),
            'publisher': self._publisher(),
            'yanked': bool(previous.get('yanked', False)),
            'security': {
                'algorithm': 'sha256',
                'source_digest': digest_source,
                'manifest_digest': digest_manifest,
            },
        }
        if archive_path and os.path.isfile(archive_path):
            entry['archive_sha256'] = _file_sha256(archive_path)
            entry['archive_name'] = os.path.basename(archive_path)
            entry['archive_size'] = os.path.getsize(archive_path)
        versions[meta.version] = entry
        self.client.write_json(self.package_uri(meta.name), record)
        self._update_index(meta.name, repo, _highest(versions))
        return record

    def _update_index(self, name: str, repo: str, latest: str) -> None:
        try:
            index = self.read_index()
        except RegistryError:
            index = {'schema': INDEX_SCHEMA, 'updated_at': _now(),
                     'packages': {}}
        index['updated_at'] = _now()
        index.setdefault('packages', {})[name] = {
            'repo': repo,
            'latest': latest,
            'updated_at': _now(),
        }
        self.client.write_json(self.index_uri, index)

    def yank(self, name: str, version: str, yanked: bool = True) -> Dict[str, Any]:
        record = self.get_package(name)
        if record is None:
            raise RegistryError(f'未找到包: {name}')
        entry = record.get('versions', {}).get(version)
        if entry is None:
            raise RegistryError(f'未找到版本: {name}@{version}')
        entry['yanked'] = bool(yanked)
        record['updated_at'] = _now()
        self.client.write_json(self.package_uri(name), record)
        return record

    # ---------------- 解析/校验 ----------------

    def resolve(self, name: str, constraint: str = ''
                ) -> Tuple[str, str, Dict[str, Any]]:
        """按名称+约束解析到 (repo, version, entry)。"""
        record = self.get_package(name)
        if record is None:
            raise RegistryError(f'中央注册表未找到包: {name}')
        versions = record.get('versions') or {}
        candidates = []
        for version, entry in versions.items():
            if not isinstance(entry, dict) or entry.get('yanked'):
                continue
            try:
                if satisfies(version, constraint or '*'):
                    candidates.append((version, entry))
            except Exception:  # noqa: BLE001 - 非法版本记录跳过
                continue
        if not candidates:
            raise RegistryError(
                f'包 {name} 没有满足约束 {constraint or "*"} 的版本; '
                f'可用: {", ".join(sorted(versions)) or "(无)"}')
        version, entry = max(candidates, key=lambda item: _version_key(item[0]))
        repo = str(record.get('repo') or entry.get('repo') or '')
        if not repo:
            raise RegistryError(f'包 {name} 的记录缺少 Git 仓库地址')
        return repo, version, entry

    @staticmethod
    def verify_install(meta: PackageMeta, entry: Dict[str, Any], *,
                       rev: str = '') -> None:
        """安装后校验源码/清单摘要与 Git 提交。"""
        expected_source = str(entry.get('source_sha256')
                              or (entry.get('security') or {}).get(
                                  'source_digest') or '')
        expected_manifest = str(entry.get('manifest_sha256')
                                or (entry.get('security') or {}).get(
                                    'manifest_digest') or '')
        if expected_source:
            actual = source_digest(meta.root)
            if actual != expected_source:
                raise RegistryError(
                    f'安全校验失败: {meta.name} {meta.version} 源码摘要不匹配 '
                    f'(期望 {expected_source[:16]}…, 实际 {actual[:16]}…)')
        if expected_manifest:
            actual = manifest_digest(meta)
            if actual != expected_manifest:
                raise RegistryError(
                    f'安全校验失败: {meta.name} {meta.version} 清单摘要不匹配')
        expected_rev = str(entry.get('rev') or '')
        if rev and expected_rev and rev != expected_rev:
            raise RegistryError(
                f'安全校验失败: {meta.name} {meta.version} Git 提交不匹配 '
                f'(期望 {expected_rev[:12]}…, 实际 {rev[:12]}…)')

    @staticmethod
    def entry_summary(record: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        rows = []
        for version in sorted(record.get('versions') or {},
                              key=_version_key, reverse=True):
            entry = record['versions'][version]
            rows.append((version, str(entry.get('tag') or ''),
                         'yanked' if entry.get('yanked') else ''))
        return rows


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def registry_from_env(client: Optional[CdifitClient] = None,
                      namespace: Optional[str] = None) -> Optional[Registry]:
    if client is None:
        from .pic_api import client_from_store
        client = client_from_store()
    if client is None:
        return None
    return Registry(client, namespace or os.environ.get(
        'PIC_REGISTRY_URI', DEFAULT_NAMESPACE))
