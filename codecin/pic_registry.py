"""pic 中央注册/分发服务器客户端。

服务器: ``https://package.codecin.org.cn`` (可配置)
认证:   Cdifit ``https://www.cdifit.cn`` 的 AccessToken (Bearer)

客户端只把 **元数据 + .cinpkg 制品** 上传到 package.codecin.org.cn;
Cdifit 仅用于登录/身份校验, 不再用于包存储。
"""

import contextlib
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .packaging.model import PackageMeta, satisfies, validate_name
from .packaging.toml import dumps as toml_dumps

__all__ = [
    'DEFAULT_REGISTRY_URL', 'DIGEST_ALGORITHM', 'RegistryClient',
    'RegistryError', 'iter_package_files', 'manifest_digest', 'source_digest',
]

DEFAULT_REGISTRY_URL = 'https://package.codecin.org.cn'
DIGEST_ALGORITHM = 'sha256'

_IGNORED_DIRS = frozenset({
    '.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.ruff_cache',
    'dist', 'build', 'target', 'pic_modules', '.pic', '.mkn',
    'node_modules', '.venv', 'venv', 'META-INF',
})


class RegistryError(RuntimeError):
    """中央服务器读写/校验失败。"""

    def __init__(self, message: str, *, status: int = 0,
                 code: str = '', data: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data


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


def source_digest(root: str) -> str:
    """源码树摘要: 路径 + 长度 + 内容 (换行归一) 的 SHA-256。"""
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
        h.update(normalized)
    return h.hexdigest()


def manifest_digest(meta: PackageMeta) -> str:
    """规范化清单摘要 (忽略 repo/tag 等发布位置字段)。"""
    data = meta.to_dict()
    package = data.get('package')
    if isinstance(package, dict):
        package.pop('repo', None)
        package.pop('tag', None)
    text = toml_dumps(data)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _version_key(version: str) -> Tuple[int, int, int]:
    try:
        return tuple(int(part) for part in version.split('-')[0].split('.'))
    except ValueError:
        return (0, 0, 0)


def _highest(versions: Iterable[str]) -> str:
    return max(versions, key=_version_key)


class RegistryClient:
    """package.codecin.org.cn API 客户端。"""

    def __init__(self, base_url: str = DEFAULT_REGISTRY_URL,
                 token: Optional[str] = None, timeout: int = 30,
                 user_agent: str = 'pic/6.2.0'):
        self.base_url = (base_url or DEFAULT_REGISTRY_URL).rstrip('/')
        self.token = token
        self.timeout = timeout
        self.user_agent = user_agent

    @property
    def api(self) -> str:
        return self.base_url + '/api/v1'

    def _url(self, path: str) -> str:
        return self.api + path if path.startswith('/') else self.api + '/' + path

    def _headers(self, auth: bool, content_type: str = '') -> Dict[str, str]:
        headers = {'Accept': 'application/json',
                   'User-Agent': self.user_agent}
        if content_type:
            headers['Content-Type'] = content_type
        if auth and self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        return headers

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None, *,
                 auth: bool = False, raw: bool = False,
                 content_type: str = 'application/json') -> Any:
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            self._url(path), data=data, method=method,
            headers=self._headers(auth, content_type if data is not None else ''))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            payload = e.read()
            status = e.code
        except urllib.error.URLError as e:
            raise RegistryError(
                f'无法连接中央注册服务器 {self.base_url}: {e.reason}') from e
        except OSError as e:
            raise RegistryError(
                f'无法连接中央注册服务器 {self.base_url}: {e}') from e

        if raw:
            if status >= 400:
                raise RegistryError(
                    f'中央服务器返回 HTTP {status}', status=status)
            return payload
        try:
            envelope = json.loads(payload.decode('utf-8')) if payload else {}
        except (ValueError, UnicodeDecodeError):
            text = payload.decode('utf-8', 'replace')[:300]
            raise RegistryError(
                f'中央服务器返回非 JSON (HTTP {status}): {text}',
                status=status) from None
        if not isinstance(envelope, dict):
            raise RegistryError(f'中央服务器响应格式错误 (HTTP {status})',
                                status=status)
        if status >= 400 or not envelope.get('ok'):
            error = envelope.get('error') or {}
            message = error.get('message') if isinstance(error, dict) else None
            code = error.get('code') if isinstance(error, dict) else ''
            raise RegistryError(
                message or f'中央服务器请求失败 (HTTP {status})',
                status=status, code=str(code or ''), data=envelope.get('data'))
        return envelope.get('data', {})

    # ---------------- 读 ----------------

    def health(self) -> Dict[str, Any]:
        return self._request('GET', '/health')

    def list_packages(self, query: str = '') -> List[Dict[str, Any]]:
        path = '/packages'
        if query:
            path += '?' + urllib.parse.urlencode({'q': query})
        data = self._request('GET', path)
        packages = data.get('packages') if isinstance(data, dict) else None
        return packages if isinstance(packages, list) else []

    def get_package(self, name: str) -> Optional[Dict[str, Any]]:
        validate_name(name)
        try:
            data = self._request('GET', f'/packages/{name}')
        except RegistryError as e:
            if e.status == 404:
                return None
            raise
        return data if isinstance(data, dict) else None

    def get_version(self, name: str, version: str) -> Optional[Dict[str, Any]]:
        validate_name(name)
        try:
            data = self._request('GET', f'/packages/{name}/{version}')
        except RegistryError as e:
            if e.status == 404:
                return None
            raise
        if not isinstance(data, dict):
            return None
        entry = data.get('version')
        return entry if isinstance(entry, dict) else None

    def resolve(self, name: str, constraint: str = ''
                ) -> Tuple[str, str, Dict[str, Any]]:
        record = self.get_package(name)
        if record is None:
            raise RegistryError(f'中央注册服务器未找到包: {name}')
        versions = record.get('versions') or {}
        candidates = []
        for version, entry in versions.items():
            if not isinstance(entry, dict) or entry.get('yanked'):
                continue
            try:
                if satisfies(version, constraint or '*'):
                    candidates.append((version, entry))
            except Exception:  # noqa: BLE001 - 跳过非法历史记录
                continue
        if not candidates:
            available = ', '.join(sorted(versions)) or '(无)'
            raise RegistryError(
                f'包 {name} 没有满足约束 {constraint or "*"} 的版本; '
                f'可用: {available}')
        version, entry = max(candidates, key=lambda item: _version_key(item[0]))
        repo = str(record.get('repo') or entry.get('repo') or '')
        if not repo:
            raise RegistryError(f'包 {name} 的记录缺少 Git 仓库地址')
        return repo, version, entry

    # ---------------- 写 ----------------

    def publish(self, meta: PackageMeta, *, repo: str, tag: str, rev: str,
                archive_path: str) -> Dict[str, Any]:
        """提交元数据并上传 .cinpkg 制品。"""
        if not repo:
            raise RegistryError('发布需要 Git 仓库地址')
        if not os.path.isfile(archive_path):
            raise RegistryError(f'包归档不存在: {archive_path}')
        source = source_digest(meta.root)
        manifest = manifest_digest(meta)
        archive_sha = _file_sha256(archive_path)
        size = os.path.getsize(archive_path)
        metadata = {
            'name': meta.name,
            'version': meta.version,
            'repo': repo,
            'tag': tag,
            'rev': rev,
            'source_sha256': source,
            'manifest_sha256': manifest,
            'archive_sha256': archive_sha,
            'archive_size': size,
        }
        published = self._request(
            'PUT', f'/packages/{meta.name}/{meta.version}', metadata,
            auth=True)
        artifact = self._upload_artifact(
            meta.name, meta.version, archive_path, archive_sha)
        result = dict(published) if isinstance(published, dict) else {}
        result['artifact'] = artifact
        result['source_sha256'] = source
        result['manifest_sha256'] = manifest
        result['archive_sha256'] = archive_sha
        result['archive_size'] = size
        return result

    def _upload_artifact(self, name: str, version: str, path: str,
                         digest: str) -> Dict[str, Any]:
        with open(path, 'rb') as f:
            data = f.read()
        query = urllib.parse.urlencode({'sha256': digest})
        request = urllib.request.Request(
            self._url(f'/packages/{name}/{version}/artifact?' + query),
            data=data, method='POST',
            headers=self._headers(True, 'application/octet-stream'))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            payload = e.read()
            status = e.code
        except urllib.error.URLError as e:
            raise RegistryError(
                f'上传制品失败 (网络): {e.reason}') from e
        try:
            envelope = json.loads(payload.decode('utf-8')) if payload else {}
        except (ValueError, UnicodeDecodeError):
            raise RegistryError(
                f'上传制品返回非 JSON (HTTP {status})', status=status) from None
        if status >= 400 or not envelope.get('ok'):
            error = envelope.get('error') or {}
            message = error.get('message') if isinstance(error, dict) else None
            raise RegistryError(message or f'上传制品失败 (HTTP {status})',
                                status=status,
                                code=str((error or {}).get('code') or ''))
        return envelope.get('data') or {}

    def download_artifact(self, name: str, version: str, dest: str,
                          expected_sha256: str = '') -> Tuple[str, str]:
        """下载制品到 ``dest``; 返回 (路径, 实际 sha256)。"""
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        request = urllib.request.Request(
            self._url(f'/packages/{name}/{version}/artifact'),
            headers={'User-Agent': self.user_agent})
        h = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp, \
                    open(dest, 'wb') as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
        except urllib.error.HTTPError as e:
            raise RegistryError(
                f'下载制品失败 (HTTP {e.code}): {name}@{version}',
                status=e.code) from e
        except urllib.error.URLError as e:
            raise RegistryError(f'下载制品失败: {e.reason}') from e
        actual = h.hexdigest()
        if expected_sha256 and actual != expected_sha256:
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise RegistryError(
                f'制品 SHA-256 不匹配: 期望 {expected_sha256[:16]}…, '
                f'实际 {actual[:16]}…')
        return dest, actual

    def yank(self, name: str, version: str, yanked: bool = True) -> Dict[str, Any]:
        data = self._request('POST', f'/packages/{name}/{version}/yank',
                             {'yanked': yanked}, auth=True)
        return data if isinstance(data, dict) else {}

    # ---------------- 校验 ----------------

    @staticmethod
    def verify_install(meta: PackageMeta, entry: Dict[str, Any], *,
                       rev: str = '') -> None:
        expected_source = str(entry.get('source_sha256') or '')
        expected_manifest = str(entry.get('manifest_sha256') or '')
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
    def version_rows(record: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        rows = []
        for version in sorted(record.get('versions') or {},
                              key=_version_key, reverse=True):
            entry = record['versions'][version]
            rows.append((version, str(entry.get('tag') or ''),
                         'yanked' if entry.get('yanked') else ''))
        return rows
