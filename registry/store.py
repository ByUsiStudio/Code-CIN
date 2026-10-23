"""pic 中央注册表的文件存储层。

目录布局与原 Go 服务保持一致：

    data/packages/<name>.json                 包记录/版本列表
    data/artifacts/<name>/<version>.cinpkg    分发制品
    data/tmp/                                 上传临时文件
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cmp_to_key
from typing import Any

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
    """只有发布者可以操作该版本。"""


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

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "tag": self.tag,
            "rev": self.rev,
            "source_sha256": self.source_sha256,
            "manifest_sha256": self.manifest_sha256,
            "archive_sha256": self.archive_sha256,
            "archive_size": self.archive_size,
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
        )


@dataclass
class Package:
    name: str
    repo: str = ""
    schema: str = "pic.package/v1"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    versions: dict[str, Version] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "repo": self.repo,
            "created_at": format_time(self.created_at),
            "updated_at": format_time(self.updated_at),
            "versions": {
                key: self.versions[key].to_json() for key in sorted(self.versions)
            },
        }

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

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo": self.repo,
            "latest": self.latest,
            "versions": self.versions,
            "updated_at": format_time(self.updated_at),
        }


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

    def put_version(self, name: str, repo: str, version: Version) -> bool:
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

    def yank(self, name: str, version: str, yanked: bool, user_id: str) -> Version:
        with self._lock:
            pkg = self.get(name)
            entry = pkg.versions.get(version)
            if entry is None:
                raise NotFound("not found")
            if entry.publisher.id and entry.publisher.id != user_id:
                raise Forbidden("forbidden")
            entry.yanked = yanked
            pkg.updated_at = utc_now()
            self._write_package(pkg)
            return entry

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


def version_less(left: str, right: str) -> bool:
    left_parts, right_parts = version_parts(left), version_parts(right)
    for index in range(3):
        if left_parts[index] != right_parts[index]:
            return left_parts[index] < right_parts[index]
    return left < right


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
