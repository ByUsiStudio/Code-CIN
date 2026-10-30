"""pic 中央注册表的文件存储层。

目录布局与原 Go 服务保持一致：

    data/packages/<name>.json                 包记录/版本列表
    data/artifacts/<name>/<version>.cinpkg    分发制品
    data/tmp/                                 上传临时文件

包记录在 v6.4.0 起额外保存展示/搜索元数据 (description/keywords/license/
modules/downloads) 与 ``latest`` 指针；搜索、后台管理和版本管理都基于这些
字段，旧记录缺少字段时按默认值读取。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cmp_to_key
from typing import Any, Iterable

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class StoreError(Exception):
    """存储层错误。"""


class NotFound(StoreError):
    """包/版本不存在。"""


class VersionExists(StoreError):
    """同版本内容不同，版本不可变。"""


class RepoMismatch(StoreError):
    """包已绑定到其它 Git 仓库。"""


class Forbidden(StoreError):
    """只有发布者/管理员可以操作。"""


class Conflict(StoreError):
    """制品摘要/大小与已记录值冲突。"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_time(value: datetime | None) -> str:
    """按 RFC3339（UTC）输出时间，与 Go time.Time 的 JSON 形式一致。"""
    if value is None:
        return "0001-01-01T00:00:00Z"
    text = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    head, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    if fraction:
        return head + "." + fraction + "Z"
    return head + "Z"


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Publisher:
    id: str = ""
    email: str = ""
    nickname: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "nickname": self.nickname}

    @classmethod
    def from_json(cls, data: Any) -> Publisher:
        if not isinstance(data, dict):
            return cls()
        return cls(
            id=str(data.get("id") or ""),
            email=str(data.get("email") or ""),
            nickname=str(data.get("nickname") or ""),
        )


@dataclass
class Version:
    version: str = ""
    tag: str = ""
    rev: str = ""
    source_sha256: str = ""
    manifest_sha256: str = ""
    archive_sha256: str = ""
    archive_size: int = 0
    artifact_url: str = ""
    published_at: datetime | None = None
    publisher: Publisher = field(default_factory=Publisher)
    yanked: bool = False
    downloads: int = 0

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "tag": self.tag,
            "rev": self.rev,
            "source_sha256": self.source_sha256,
            "manifest_sha256": self.manifest_sha256,
            "archive_sha256": self.archive_sha256,
            "archive_size": self.archive_size,
            "downloads": self.downloads,
        }
        if self.artifact_url:
            data["artifact_url"] = self.artifact_url
        data["published_at"] = format_time(self.published_at)
        data["publisher"] = self.publisher.to_json()
        data["yanked"] = self.yanked
        return data

    @classmethod
    def from_json(cls, data: Any) -> Version:
        if not isinstance(data, dict):
            return cls()
        size = data.get("archive_size")
        downloads = data.get("downloads")
        return cls(
            version=str(data.get("version") or ""),
            tag=str(data.get("tag") or ""),
            rev=str(data.get("rev") or ""),
            source_sha256=str(data.get("source_sha256") or ""),
            manifest_sha256=str(data.get("manifest_sha256") or ""),
            archive_sha256=str(data.get("archive_sha256") or ""),
            archive_size=int(size) if isinstance(size, int) else 0,
            artifact_url=str(data.get("artifact_url") or ""),
            published_at=parse_time(data.get("published_at")),
            publisher=Publisher.from_json(data.get("publisher")),
            yanked=bool(data.get("yanked")),
            downloads=int(downloads) if isinstance(downloads, int) else 0,
        )


@dataclass
class Package:
    name: str
    repo: str = ""
    schema: str = "pic.package/v1"
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    license: str = ""
    homepage: str = ""
    entry: str = ""
    modules: list[str] = field(default_factory=list)
    downloads: int = 0
    latest: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    versions: dict[str, Version] = field(default_factory=dict)

    def effective_latest(self) -> str:
        """显式 latest 指针优先，否则取最高的未撤回版本。"""
        if self.latest and self.latest in self.versions:
            entry = self.versions[self.latest]
            if not entry.yanked:
                return self.latest
        active = {key: value for key, value in self.versions.items()
                  if not value.yanked}
        return latest(active)

    def active_versions(self) -> list[str]:
        return sorted(
            (key for key, value in self.versions.items() if not value.yanked),
            key=cmp_to_key(version_less),
            reverse=True,
        )

    def yanked_versions(self) -> list[str]:
        return sorted(key for key, value in self.versions.items()
                      if value.yanked)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "name": self.name,
            "repo": self.repo,
            "latest": self.effective_latest(),
            "downloads": self.downloads,
            "created_at": format_time(self.created_at),
            "updated_at": format_time(self.updated_at),
            "versions": {
                key: self.versions[key].to_json()
                for key in sorted(self.versions)
            },
        }
        if self.description:
            data["description"] = self.description
        if self.keywords:
            data["keywords"] = list(self.keywords)
        if self.license:
            data["license"] = self.license
        if self.homepage:
            data["homepage"] = self.homepage
        if self.entry:
            data["entry"] = self.entry
        if self.modules:
            data["modules"] = list(self.modules)
        if self.latest:
            data["latest_pointer"] = self.latest
        return data

    @classmethod
    def from_json(cls, data: Any) -> Package:
        if not isinstance(data, dict):
            raise StoreError("invalid package record")
        raw_versions = data.get("versions")
        versions: dict[str, Version] = {}
        if isinstance(raw_versions, dict):
            for key, value in raw_versions.items():
                versions[str(key)] = Version.from_json(value)
        return cls(
            name=str(data.get("name") or ""),
            repo=str(data.get("repo") or ""),
            schema=str(data.get("schema") or "pic.package/v1"),
            description=str(data.get("description") or ""),
            keywords=_string_list(data.get("keywords")),
            license=str(data.get("license") or ""),
            homepage=str(data.get("homepage") or ""),
            entry=str(data.get("entry") or ""),
            modules=_string_list(data.get("modules")),
            downloads=_int_value(data.get("downloads")),
            latest=str(data.get("latest_pointer") or ""),
            created_at=parse_time(data.get("created_at")),
            updated_at=parse_time(data.get("updated_at")),
            versions=versions,
        )


@dataclass
class PackageSummary:
    name: str
    repo: str
    latest: str
    versions: int
    updated_at: datetime | None
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    downloads: int = 0
    yanked: int = 0

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "repo": self.repo,
            "latest": self.latest,
            "versions": self.versions,
            "updated_at": format_time(self.updated_at),
            "downloads": self.downloads,
            "yanked": self.yanked,
        }
        if self.description:
            data["description"] = self.description
        if self.keywords:
            data["keywords"] = list(self.keywords)
        return data


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    return int(value) if isinstance(value, int) else 0


def validate_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise StoreError(f"invalid package name {json.dumps(name, ensure_ascii=False)}")


def validate_version(version: str) -> None:
    if not VERSION_RE.match(version or ""):
        raise StoreError(
            f"invalid version {json.dumps(version, ensure_ascii=False)}"
        )


def validate_digest(digest: str) -> None:
    if digest and not SHA_RE.match(digest):
        raise StoreError(f"invalid sha256 {json.dumps(digest, ensure_ascii=False)}")


def _clean_metadata(value: Any, field_name: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise StoreError(f"{field_name} is too long")
    return text


def _clean_keywords(value: Any) -> list[str]:
    keywords = _string_list(value)
    if len(keywords) > 32:
        raise StoreError("too many keywords")
    for keyword in keywords:
        if len(keyword) > 40:
            raise StoreError("keyword is too long")
    return keywords


class Store:
    def __init__(self, root: str):
        self.root = root
        self._lock = threading.Lock()
        for directory in (self.packages_dir, self.artifacts_dir, self.tmp_dir):
            os.makedirs(directory, exist_ok=True)

    @property
    def packages_dir(self) -> str:
        return os.path.join(self.root, "packages")

    @property
    def artifacts_dir(self) -> str:
        return os.path.join(self.root, "artifacts")

    @property
    def tmp_dir(self) -> str:
        return os.path.join(self.root, "tmp")

    def package_path(self, name: str) -> str:
        return os.path.join(self.packages_dir, name + ".json")

    def artifact_path(self, name: str, version: str) -> str:
        return os.path.join(self.artifacts_dir, name, version + ".cinpkg")

    def get(self, name: str) -> Package:
        validate_name(name)
        try:
            with open(self.package_path(name), "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            raise NotFound("not found") from None
        except OSError as exc:
            raise StoreError(str(exc)) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError(f"decode {name}: {exc}") from exc
        return Package.from_json(data)

    def list_packages(self) -> list[Package]:
        try:
            entries = sorted(os.listdir(self.packages_dir))
        except OSError as exc:
            raise StoreError(str(exc)) from exc
        packages: list[Package] = []
        for entry in entries:
            if not entry.endswith(".json"):
                continue
            if not os.path.isfile(os.path.join(self.packages_dir, entry)):
                continue
            try:
                packages.append(self.get(entry[: -len(".json")]))
            except StoreError:
                continue
        return sorted(packages, key=lambda pkg: pkg.name)

    def put_version(
        self,
        name: str,
        repo: str,
        version: Version,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """写入版本元数据；返回是否为新建的包（幂等重复提交返回 False）。"""
        validate_name(name)
        validate_version(version.version)
        if not repo.strip():
            raise StoreError("repo is required")
        validate_digest(version.source_sha256)
        if not version.source_sha256:
            raise StoreError("source_sha256 is required")
        validate_digest(version.manifest_sha256)
        if not version.manifest_sha256:
            raise StoreError("manifest_sha256 is required")
        validate_digest(version.archive_sha256)

        meta = details or {}
        description = _clean_metadata(meta.get("description"), "description")
        license_name = _clean_metadata(meta.get("license"), "license", 200)
        homepage = _clean_metadata(meta.get("homepage"), "homepage", 500)
        entry = _clean_metadata(meta.get("entry"), "entry", 500)
        keywords = _clean_keywords(meta.get("keywords"))
        modules = _string_list(meta.get("modules"))[:200]

        with self._lock:
            try:
                pkg = self.get(name)
            except NotFound:
                pkg = Package(
                    schema="pic.package/v1",
                    name=name,
                    repo=repo,
                    created_at=utc_now(),
                    versions={},
                )
                created = True
            else:
                created = False

            if pkg.repo and pkg.repo != repo:
                raise RepoMismatch("package repository mismatch")

            if description:
                pkg.description = description
            if license_name:
                pkg.license = license_name
            if homepage:
                pkg.homepage = homepage
            if entry:
                pkg.entry = entry
            if keywords:
                pkg.keywords = keywords
            if modules:
                pkg.modules = modules

            existing = pkg.versions.get(version.version)
            if existing is not None:
                same_source = existing.source_sha256 == version.source_sha256
                same_archive = existing.archive_sha256 == version.archive_sha256
                if not (same_source and same_archive):
                    raise VersionExists(
                        "version already exists with different contents"
                    )
                if version.tag:
                    existing.tag = version.tag
                if version.rev:
                    existing.rev = version.rev
                pkg.updated_at = utc_now()
                self._write_package(pkg)
                return False

            version.version = version.version.strip()
            version.published_at = utc_now()
            if not version.publisher.id:
                raise StoreError("publisher is required")
            pkg.versions[version.version] = version
            pkg.updated_at = version.published_at
            self._write_package(pkg)
            return created

    def set_artifact(
        self, name: str, version: str, digest: str, size: int, public_url: str
    ) -> Version:
        validate_digest(digest)
        with self._lock:
            pkg = self.get(name)
            entry = pkg.versions.get(version)
            if entry is None:
                raise NotFound("not found")
            if entry.archive_sha256 and entry.archive_sha256 != digest:
                raise Conflict("conflict")
            if entry.archive_size and entry.archive_size != size:
                raise Conflict("conflict")
            entry.archive_sha256 = digest
            entry.archive_size = size
            entry.artifact_url = (
                public_url.rstrip("/")
                + "/api/v1/packages/"
                + name
                + "/"
                + version
                + "/artifact"
            )
            pkg.updated_at = utc_now()
            self._write_package(pkg)
            return entry

    def yank(
        self,
        name: str,
        version: str,
        yanked: bool,
        user_id: str,
        *,
        force: bool = False,
    ) -> Version:
        with self._lock:
            pkg = self.get(name)
            entry = pkg.versions.get(version)
            if entry is None:
                raise NotFound("not found")
            if not force and entry.publisher.id and entry.publisher.id != user_id:
                raise Forbidden("forbidden")
            entry.yanked = yanked
            pkg.updated_at = utc_now()
            self._write_package(pkg)
            return entry

    def set_latest(
        self, name: str, version: str, user_id: str, *, force: bool = False
    ) -> Package:
        with self._lock:
            pkg = self.get(name)
            entry = pkg.versions.get(version)
            if entry is None:
                raise NotFound("not found")
            if not force and entry.publisher.id \
                    and entry.publisher.id != user_id:
                raise Forbidden("forbidden")
            pkg.latest = version
            pkg.updated_at = utc_now()
            self._write_package(pkg)
            return pkg

    def clear_latest(self, name: str) -> None:
        with self._lock:
            pkg = self.get(name)
            pkg.latest = ""
            pkg.updated_at = utc_now()
            self._write_package(pkg)

    def delete_version(
        self, name: str, version: str, user_id: str, *, force: bool = False
    ) -> bool:
        """删除单个版本与制品；返回是否删除了包目录。"""
        with self._lock:
            pkg = self.get(name)
            entry = pkg.versions.get(version)
            if entry is None:
                raise NotFound("not found")
            if not force and entry.publisher.id \
                    and entry.publisher.id != user_id:
                raise Forbidden("forbidden")
            path = self.artifact_path(name, version)
            with suppress(OSError):
                os.unlink(path)
            pkg.versions.pop(version, None)
            if pkg.latest == version:
                pkg.latest = ""
            if not pkg.versions:
                with suppress(OSError):
                    os.unlink(self.package_path(name))
                with suppress(OSError):
                    shutil.rmtree(os.path.join(self.artifacts_dir, name))
                return True
            pkg.updated_at = utc_now()
            self._write_package(pkg)
            return False

    def delete_package(
        self, name: str, user_id: str, *, force: bool = False
    ) -> None:
        with self._lock:
            pkg = self.get(name)
            if not force:
                publishers = {
                    entry.publisher.id
                    for entry in pkg.versions.values()
                    if entry.publisher.id
                }
                if publishers and publishers != {user_id}:
                    raise Forbidden("forbidden")
            with suppress(OSError):
                os.unlink(self.package_path(name))
            with suppress(OSError):
                shutil.rmtree(os.path.join(self.artifacts_dir, name))

    def record_download(self, name: str, version: str) -> None:
        """制品下载计数 (失败不影响下载)。"""
        with suppress(OSError, StoreError):
            with self._lock:
                pkg = self.get(name)
                entry = pkg.versions.get(version)
                if entry is None:
                    return
                entry.downloads += 1
                pkg.downloads += 1
                self._write_package(pkg)

    def summary(self, pkg: Package) -> PackageSummary:
        return PackageSummary(
            name=pkg.name,
            repo=pkg.repo,
            latest=pkg.effective_latest(),
            versions=len(pkg.versions),
            updated_at=pkg.updated_at,
            description=pkg.description,
            keywords=list(pkg.keywords),
            downloads=pkg.downloads,
            yanked=len(pkg.yanked_versions()),
        )

    def search(
        self,
        query: str = "",
        *,
        limit: int = 0,
        offset: int = 0,
        sort: str = "",
        order: str = "",
        keyword: str = "",
    ) -> tuple[list[PackageSummary], int]:
        """搜索/列表；返回 (当前页, 总数)。"""
        text = (query or "").strip().lower()
        keyword_text = (keyword or "").strip().lower()
        packages = self.list_packages()
        scored: list[tuple[int, Package]] = []
        for pkg in packages:
            if keyword_text and keyword_text not in {
                item.lower() for item in pkg.keywords
            }:
                continue
            score = _match_score(pkg, text)
            if score < 0:
                continue
            scored.append((score, pkg))

        if sort in ("name", "latest", "updated", "downloads", "versions"):
            reverse = order != "asc"
            if sort == "name":
                reverse = order == "desc"
                scored.sort(key=lambda item: item[1].name.lower(),
                            reverse=reverse)
            elif sort == "downloads":
                scored.sort(
                    key=lambda item: (item[1].downloads, item[1].name.lower()),
                    reverse=reverse)
            elif sort == "versions":
                scored.sort(
                    key=lambda item: (len(item[1].versions), item[1].name.lower()),
                    reverse=reverse)
            elif sort == "latest":
                scored.sort(
                    key=lambda item: (
                        version_key(item[1].effective_latest()),
                        item[1].name.lower(),
                    ),
                    reverse=reverse)
            else:
                updated = datetime.min.replace(tzinfo=timezone.utc)
                scored.sort(
                    key=lambda item: (item[1].updated_at or updated),
                    reverse=reverse)
        else:
            # 默认: 有查询词时按相关度, 否则按名称
            if text:
                scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
            else:
                scored.sort(key=lambda item: item[1].name.lower())

        total = len(scored)
        start = max(0, offset)
        if limit and limit > 0:
            page = scored[start:start + limit]
        else:
            page = scored[start:]
        return [self.summary(pkg) for _score, pkg in page], total

    def stats(self) -> dict[str, Any]:
        packages = self.list_packages()
        versions = sum(len(pkg.versions) for pkg in packages)
        yanked = sum(len(pkg.yanked_versions()) for pkg in packages)
        artifacts = 0
        storage = 0
        if os.path.isdir(self.artifacts_dir):
            for root, _dirs, files in os.walk(self.artifacts_dir):
                for filename in files:
                    if not filename.endswith(".cinpkg"):
                        continue
                    path = os.path.join(root, filename)
                    with suppress(OSError):
                        storage += os.path.getsize(path)
                        artifacts += 1
        return {
            "packages": len(packages),
            "versions": versions,
            "yanked": yanked,
            "artifacts": artifacts,
            "storage_bytes": storage,
            "downloads": sum(pkg.downloads for pkg in packages),
            "updated_at": format_time(
                max((pkg.updated_at for pkg in packages
                     if pkg.updated_at is not None),
                    default=None)),
        }

    def temp_artifact(self, name: str, version: str) -> str:
        directory = os.path.join(self.tmp_dir, name)
        os.makedirs(directory, exist_ok=True)
        fd, path = tempfile.mkstemp(
            dir=directory, prefix=version + "-", suffix=".part"
        )
        os.close(fd)
        return path

    def commit_artifact(
        self, name: str, version: str, temp_path: str, digest: str
    ) -> str:
        final = self.artifact_path(name, version)
        os.makedirs(os.path.dirname(final), exist_ok=True)
        os.replace(temp_path, final)
        return final

    def _write_package(self, pkg: Package) -> None:
        data = json.dumps(pkg.to_json(), indent=2, ensure_ascii=False) + "\n"
        atomic_write(self.package_path(pkg.name), data.encode("utf-8"))


def _match_score(pkg: Package, query: str) -> int:
    """搜索相关度: 名称精确 > 前缀 > 包含 > 仓库 > 描述/关键词。"""
    if not query:
        return 0
    name = pkg.name.lower()
    repo = pkg.repo.lower()
    description = pkg.description.lower()
    keywords = " ".join(item.lower() for item in pkg.keywords)
    if name == query:
        return 100
    if name.startswith(query):
        return 80
    if query in name:
        return 60
    if query in keywords:
        return 45
    if query in description:
        return 30
    if query in repo:
        return 20
    return -1


def atomic_write(path: str, data: bytes, mode: int = 0o644) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def latest(versions: dict[str, Version]) -> str:
    if not versions:
        return ""
    return max(versions, key=cmp_to_key(version_less))


def version_key(version: str) -> tuple[int, int, int]:
    return version_parts(version)


def version_less(left: str, right: str) -> int:
    """比较两个版本; 供 ``cmp_to_key`` / 排序使用。"""
    left_parts, right_parts = version_parts(left), version_parts(right)
    for index in range(3):
        if left_parts[index] != right_parts[index]:
            return -1 if left_parts[index] < right_parts[index] else 1
    if left == right:
        return 0
    return -1 if left < right else 1


def version_parts(version: str) -> tuple[int, int, int]:
    base = version.split("-", 1)[0]
    base = base.split("+", 1)[0]
    parts = base.split(".")
    out: list[int] = []
    for index in range(3):
        try:
            out.append(int(parts[index]))
        except (IndexError, ValueError):
            out.append(0)
    return out[0], out[1], out[2]
