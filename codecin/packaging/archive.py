"""可复现 zip 归档、路径安全解包与 SHA-256 校验。

``.cinpkg`` (pic 包) 与 ``.cvm`` (CVM 模块) 都使用 zip 容器:

    META-INF/SHA256SUMS          # "<sha256>  <path>" 逐行
    ...                          # 其余文件

写入使用固定时间戳 (1980-01-01), 同样的输入产生逐字节相同的归档;
解包拒绝绝对路径、盘符、``..`` 与符号链接成员。
"""

import hashlib
import os
import re
import zipfile
from typing import Dict, Iterable, List, Tuple

__all__ = [
    'ArchiveError', 'is_safe_member', 'read_checksums', 'read_manifest_member',
    'safe_extract', 'sha256_bytes', 'sha256_file', 'write_zip',
]

_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_HASH_RE = re.compile(r'^[0-9a-f]{64}$')


class ArchiveError(ValueError):
    """归档损坏、路径不安全或校验失败。"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def is_safe_member(name: str) -> bool:
    """zip 成员名是否可安全解包。"""
    if not name or '\x00' in name:
        return False
    normalized = name.replace('\\', '/')
    if normalized.startswith('/'):
        return False
    if re.match(r'^[A-Za-z]:', normalized):
        return False
    parts = normalized.split('/')
    return all(part not in ('', '.', '..') for part in parts)


def _zip_info(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, 便于跨平台一致
    info.external_attr = (0o755 if executable else 0o644) << 16
    return info


def write_zip(path: str, entries: Dict[str, bytes]) -> str:
    """把 ``{成员名: 字节}`` 写为确定性 zip; 返回路径。"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    for name in entries:
        if not is_safe_member(name):
            raise ArchiveError(f'不安全的归档成员名: {name!r}')
    # 先写临时文件再原子替换, 避免中断留下半个包
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            zf.writestr(_zip_info(name), entries[name])
    os.replace(tmp, path)
    return path


def safe_extract(path: str, dest: str) -> List[str]:
    """安全解包到 ``dest``; 返回成员名列表。"""
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    out: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = info.filename
                if name.endswith('/'):
                    continue
                if not is_safe_member(name):
                    raise ArchiveError(f'归档包含不安全路径: {name!r}')
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ArchiveError(f'归档包含符号链接, 已拒绝: {name!r}')
                target = os.path.join(dest, *name.split('/'))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, 'wb') as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b''):
                        dst.write(chunk)
                out.append(name)
    except zipfile.BadZipFile as e:
        raise ArchiveError(f'不是有效的 zip 归档: {path}') from e
    return out


def read_manifest_member(path: str,
                         names: Iterable[str] = ('CVM-INF/MANIFEST.TOML',
                                                 'pic.toml')) -> str:
    """读取归档中的文本清单 (返回第一个命中的成员)。"""
    try:
        with zipfile.ZipFile(path) as zf:
            existing = set(zf.namelist())
            for name in names:
                if name in existing:
                    return zf.read(name).decode('utf-8-sig')
    except zipfile.BadZipFile as e:
        raise ArchiveError(f'不是有效的 zip 归档: {path}') from e
    raise ArchiveError(f'归档中缺少清单文件 ({", ".join(names)}): {path}')


def read_checksums(path: str,
                   member: str = 'META-INF/SHA256SUMS'
                   ) -> Dict[str, str]:
    """读取 ``SHA256SUMS`` 成员; 无该成员时返回空 dict。"""
    try:
        with zipfile.ZipFile(path) as zf:
            if member not in zf.namelist():
                return {}
            text = zf.read(member).decode('utf-8')
    except zipfile.BadZipFile as e:
        raise ArchiveError(f'不是有效的 zip 归档: {path}') from e
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0].lower(), parts[-1].lstrip('*')
        if _HASH_RE.match(digest):
            out[name.replace('\\', '/')] = digest
    return out


def verify_checksums(path: str,
                     member: str = 'META-INF/SHA256SUMS') -> Tuple[int, List[str]]:
    """校验归档内所有登记文件; 返回 (校验数, 失败列表)。"""
    expected = read_checksums(path, member)
    failures: List[str] = []
    checked = 0
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for name, digest in expected.items():
            if name not in names:
                failures.append(f'{name}: 归档中缺少该文件')
                continue
            actual = sha256_bytes(zf.read(name))
            checked += 1
            if actual != digest:
                failures.append(f'{name}: 校验和不匹配')
    return checked, failures


def checksums_text(entries: Dict[str, bytes]) -> bytes:
    """生成 ``SHA256SUMS`` 文本 (按路径排序)。"""
    lines = [f'{sha256_bytes(entries[name])}  {name}'
             for name in sorted(entries) if name != 'META-INF/SHA256SUMS']
    return ('\n'.join(lines) + '\n').encode('utf-8')


def build_archive_entries(files: Dict[str, bytes]) -> Dict[str, bytes]:
    """在文件映射上追加 ``META-INF/SHA256SUMS``。"""
    entries = dict(files)
    entries['META-INF/SHA256SUMS'] = checksums_text(entries)
    return entries
