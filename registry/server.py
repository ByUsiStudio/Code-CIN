"""package.codecin.org.cn —— pic 中央注册/分发服务器。

仅使用 Python 标准库的 HTTP 服务器（不使用 Flask），rich 负责终端输出与日志。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import stat
import sys
import time
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

from auth import Authenticator, Unauthorized, User
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from store import (
    Forbidden,
    NotFound,
    RepoMismatch,
    Store,
    StoreError,
    Version,
    VersionExists,
    format_time,
    validate_digest,
    validate_name,
    validate_version,
)
from version import __version__

MAX_METADATA_BYTES = 1 << 20
MAX_ARTIFACT_BYTES = 256 << 20
MAX_PAGE_SIZE = 200

_PUBLISH_FIELDS = {
    "name",
    "version",
    "repo",
    "tag",
    "rev",
    "source_sha256",
    "manifest_sha256",
    "archive_sha256",
    "archive_size",
    "description",
    "keywords",
    "license",
    "homepage",
    "entry",
    "modules",
}
_YANK_FIELDS = {"yanked"}
_LATEST_FIELDS = {"latest"}



class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class BodyReadError(Exception):
    """请求体读取失败（超限、分块格式错误等）。"""


@dataclass
class JsonResponse:
    status: int
    payload: dict[str, Any]


@dataclass
class ArtifactResponse:
    path: str
    sha256: str
    name: str
    version: str


@dataclass
class HtmlResponse:
    status: int
    body: str
    content_type: str = "text/html; charset=utf-8"


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: Any,
        rfile: Any,
        client_ip: str,
    ):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.rfile = rfile
        self.client_ip = client_ip

    def header(self, name: str) -> str:
        return self.headers.get(name) or ""

    def query_value(self, name: str) -> str:
        values = self.query.get(name)
        return values[0] if values else ""

    def read_body(self, limit: int) -> bytes:
        data = bytearray()
        for chunk in self.body_chunks(limit):
            data.extend(chunk)
            if len(data) > limit:
                raise BodyReadError("http: request body too large")
        return bytes(data)

    def body_chunks(self, limit: int) -> Iterator[bytes]:
        transfer_encoding = self.header("Transfer-Encoding").lower()
        if "chunked" in transfer_encoding:
            yield from self._chunked_chunks(limit)
            return
        raw_length = self.header("Content-Length")
        if not raw_length:
            return
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BodyReadError("invalid Content-Length") from exc
        if length < 0:
            raise BodyReadError("invalid Content-Length")
        if length > limit:
            raise BodyReadError("http: request body too large")
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    def _chunked_chunks(self, limit: int) -> Iterator[bytes]:
        total = 0
        while True:
            line = self.rfile.readline(1024)
            if not line:
                raise BodyReadError("unexpected EOF in chunked body")
            size_text = line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise BodyReadError("malformed chunked body") from exc
            if size == 0:
                while True:
                    trailer = self.rfile.readline(1024)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                return
            total += size
            if total > limit:
                raise BodyReadError("http: request body too large")
            remaining = size
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise BodyReadError("unexpected EOF in chunked body")
                remaining -= len(chunk)
                yield chunk
            self.rfile.read(2)


class App:
    def __init__(
        self,
        store: Store,
        authenticator: Authenticator,
        public_url: str,
        logger: logging.Logger,
        admin_emails: Any = None,
        admin_ids: Any = None,
    ):
        self.store = store
        self.auth = authenticator
        self.public_url = (public_url or "").rstrip("/")
        self.logger = logger
        self.started = time.monotonic()
        self.admin_emails = {
            email.lower()
            for email in _identity_set(
                admin_emails if admin_emails is not None
                else os.environ.get("PIC_ADMIN_EMAILS", ""))
        }
        self.admin_ids = _identity_set(
            admin_ids if admin_ids is not None
            else os.environ.get("PIC_ADMIN_IDS", ""))

    def handle_health(self, request: Request) -> JsonResponse:
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "status": "ok",
                    "version": __version__,
                    "time": format_time(datetime.now(timezone.utc)),
                    "uptime": format_duration(time.monotonic() - self.started),
                },
            },
        )

    def handle_list(self, request: Request) -> JsonResponse:
        query = request.query_value("q").strip()
        keyword = request.query_value("keyword").strip()
        sort = request.query_value("sort").strip().lower()
        order = request.query_value("order").strip().lower()
        offset = _int_query(request, "offset", 0, minimum=0)
        limit = _int_query(request, "limit", 0, minimum=0)
        if limit:
            limit = min(limit, MAX_PAGE_SIZE)
        try:
            summaries, total = self.store.search(
                query,
                limit=limit,
                offset=offset,
                sort=sort,
                order=order,
                keyword=keyword,
            )
        except StoreError as exc:
            raise ApiError(500, "store_error", str(exc)) from exc
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "packages": [summary.to_json() for summary in summaries],
                    "count": len(summaries),
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                },
            },
        )

    def handle_stats(self, request: Request) -> JsonResponse:
        try:
            data = self.store.stats()
        except StoreError as exc:
            raise ApiError(500, "store_error", str(exc)) from exc
        data["admin_configured"] = bool(self.admin_emails or self.admin_ids)
        return JsonResponse(200, {"ok": True, "data": data})

    def handle_admin_page(self, request: Request) -> HtmlResponse:
        return HtmlResponse(200, _ADMIN_HTML)

    def handle_me(self, request: Request) -> JsonResponse:
        user = self._require_user(request)
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "sub": user.sub,
                    "name": user.name,
                    "email": user.email,
                    "nickname": user.nickname,
                    "admin": self._is_admin(user),
                },
            },
        )

    def handle_get_package(self, request: Request, name: str) -> JsonResponse:
        try:
            pkg = self.store.get(name)
        except NotFound as exc:
            raise ApiError(404, "not_found", "package not found") from exc
        except StoreError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        return JsonResponse(200, {"ok": True, "data": pkg.to_json()})

    def handle_get_version(
        self, request: Request, name: str, version: str
    ) -> JsonResponse:
        try:
            pkg = self.store.get(name)
        except NotFound as exc:
            raise ApiError(404, "not_found", "package not found") from exc
        except StoreError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        entry = pkg.versions.get(version)
        if entry is None:
            raise ApiError(404, "not_found", "version not found")
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {"name": name, "repo": pkg.repo, "version": entry.to_json()},
            },
        )

    def handle_publish(
        self, request: Request, name: str, version: str
    ) -> JsonResponse:
        user = self._require_user(request)
        try:
            validate_name(name)
        except StoreError as exc:
            raise ApiError(400, "invalid_name", str(exc)) from exc
        try:
            validate_version(version)
        except StoreError as exc:
            raise ApiError(400, "invalid_version", str(exc)) from exc

        body = _decode_object(request, MAX_METADATA_BYTES, _PUBLISH_FIELDS)
        body_name = _string_field(body, "name")
        if body_name and body_name != name:
            raise ApiError(400, "name_mismatch", "body name does not match URL")
        body_version = _string_field(body, "version")
        if body_version and body_version != version:
            raise ApiError(400, "version_mismatch", "body version does not match URL")

        entry = Version(
            version=version,
            tag=_string_field(body, "tag").strip(),
            rev=_string_field(body, "rev").strip(),
            source_sha256=_string_field(body, "source_sha256").strip().lower(),
            manifest_sha256=_string_field(body, "manifest_sha256").strip().lower(),
            archive_sha256=_string_field(body, "archive_sha256").strip().lower(),
            archive_size=_int_field(body, "archive_size"),
            publisher=user.publisher(),
        )
        details = {
            "description": _string_field(body, "description"),
            "license": _string_field(body, "license"),
            "homepage": _string_field(body, "homepage"),
            "entry": _string_field(body, "entry"),
            "keywords": _string_list_field(body, "keywords"),
            "modules": _string_list_field(body, "modules"),
        }
        try:
            created = self.store.put_version(
                name, _string_field(body, "repo").strip(), entry, details
            )
        except VersionExists as exc:
            raise ApiError(
                409,
                "version_exists",
                "version already exists with different contents; bump the version",
            ) from exc
        except RepoMismatch as exc:
            raise ApiError(
                409,
                "repo_mismatch",
                "package is already bound to a different Git repository",
            ) from exc
        except StoreError as exc:
            raise ApiError(400, "publish_failed", str(exc)) from exc

        return JsonResponse(
            201 if created else 200,
            {
                "ok": True,
                "data": {
                    "name": name,
                    "version": version,
                    "created": created,
                    "publisher": user.publisher().to_json(),
                },
            },
        )

    def handle_artifact_upload(
        self, request: Request, name: str, version: str
    ) -> JsonResponse:
        user = self._require_user(request)
        try:
            pkg = self.store.get(name)
        except NotFound as exc:
            raise ApiError(404, "not_found", "publish metadata first") from exc
        except StoreError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc

        entry = pkg.versions.get(version)
        if entry is None:
            raise ApiError(404, "not_found", "version not found")
        if entry.publisher.id and entry.publisher.id != user.sub:
            raise ApiError(
                403, "forbidden", "only the publisher can upload the artifact"
            )

        expected = (
            request.query_value("sha256").strip().lower() or entry.archive_sha256
        )
        try:
            validate_digest(expected)
            digest_ok = bool(expected)
        except StoreError:
            digest_ok = False
        if not digest_ok:
            raise ApiError(
                400,
                "missing_sha256",
                "sha256 query or metadata archive_sha256 is required",
            )

        try:
            temp_path = self.store.temp_artifact(name, version)
        except OSError as exc:
            raise ApiError(500, "store_error", str(exc)) from exc

        try:
            hasher = hashlib.sha256()
            size = 0
            try:
                with open(temp_path, "wb") as handle:
                    for chunk in request.body_chunks(MAX_ARTIFACT_BYTES):
                        handle.write(chunk)
                        hasher.update(chunk)
                        size += len(chunk)
            except BodyReadError as exc:
                raise ApiError(400, "upload_failed", str(exc)) from exc
            except OSError as exc:
                raise ApiError(500, "store_error", str(exc)) from exc

            actual = hasher.hexdigest()
            if actual != expected:
                raise ApiError(
                    400,
                    "checksum_mismatch",
                    f"artifact sha256 {actual} != {expected}",
                )
            if entry.archive_size and entry.archive_size != size:
                raise ApiError(
                    400,
                    "size_mismatch",
                    f"artifact size {size} != {entry.archive_size}",
                )

            try:
                final_path = self.store.commit_artifact(
                    name, version, temp_path, actual
                )
            except OSError as exc:
                raise ApiError(500, "store_error", str(exc)) from exc

            try:
                updated = self.store.set_artifact(
                    name, version, actual, size, self.public_url
                )
            except StoreError as exc:
                _remove_quietly(final_path)
                raise ApiError(500, "store_error", str(exc)) from exc

            return JsonResponse(
                200,
                {
                    "ok": True,
                    "data": {
                        "name": name,
                        "version": version,
                        "sha256": actual,
                        "size": size,
                        "url": updated.artifact_url,
                    },
                },
            )
        finally:
            _remove_quietly(temp_path)

    def handle_artifact_download(
        self, request: Request, name: str, version: str
    ) -> ArtifactResponse:
        try:
            validate_name(name)
        except StoreError as exc:
            raise ApiError(400, "invalid_name", str(exc)) from exc
        try:
            validate_version(version)
        except StoreError as exc:
            raise ApiError(400, "invalid_version", str(exc)) from exc

        try:
            pkg = self.store.get(name)
        except NotFound as exc:
            raise ApiError(404, "not_found", "package not found") from exc
        except StoreError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc

        entry = pkg.versions.get(version)
        if entry is None:
            raise ApiError(404, "not_found", "version not found")

        path = self.store.artifact_path(name, version)
        if not os.path.isfile(path):
            raise ApiError(404, "artifact_not_found", "artifact not uploaded")
        self.store.record_download(name, version)
        return ArtifactResponse(
            path=path, sha256=entry.archive_sha256, name=name, version=version
        )

    def handle_yank(self, request: Request, name: str, version: str) -> JsonResponse:
        user = self._require_user(request)
        body = _decode_object(request, MAX_METADATA_BYTES, _YANK_FIELDS)
        value = body.get("yanked")
        if value is None:
            yanked = True
        elif isinstance(value, bool):
            yanked = value
        else:
            raise ApiError(
                400,
                "invalid_json",
                "json: cannot unmarshal value into Go struct field .yanked of type bool",
            )

        try:
            updated = self.store.yank(
                name, version, yanked, user.sub, force=self._is_admin(user)
            )
        except Forbidden as exc:
            raise ApiError(
                403, "forbidden", "only the publisher can yank this version"
            ) from exc
        except NotFound as exc:
            raise ApiError(404, "not_found", "package/version not found") from exc
        except StoreError as exc:
            raise ApiError(400, "yank_failed", str(exc)) from exc

        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {"name": name, "version": version, "yanked": updated.yanked},
            },
        )

    def handle_set_latest(
        self, request: Request, name: str, version: str
    ) -> JsonResponse:
        user = self._require_user(request)
        body = _decode_object(request, MAX_METADATA_BYTES, _LATEST_FIELDS)
        value = body.get("latest")
        if value is not None and not isinstance(value, bool):
            raise ApiError(
                400,
                "invalid_json",
                "json: cannot unmarshal value into Go struct field .latest of type bool",
            )
        try:
            if value is False:
                pkg = self.store.get(name)
                if not self._is_admin(user):
                    publishers = {entry.publisher.id
                                  for entry in pkg.versions.values()
                                  if entry.publisher.id}
                    if publishers and user.sub not in publishers:
                        raise Forbidden("forbidden")
                self.store.clear_latest(name)
                return JsonResponse(
                    200,
                    {
                        "ok": True,
                        "data": {
                            "name": name,
                            "latest": self.store.get(name).effective_latest(),
                        },
                    },
                )
            pkg = self.store.set_latest(
                name, version, user.sub, force=self._is_admin(user)
            )
        except Forbidden as exc:
            raise ApiError(
                403, "forbidden", "only the publisher or an admin can set latest"
            ) from exc
        except NotFound as exc:
            raise ApiError(404, "not_found", "package/version not found") from exc
        except StoreError as exc:
            raise ApiError(400, "latest_failed", str(exc)) from exc
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "name": name,
                    "version": version,
                    "latest": pkg.effective_latest(),
                },
            },
        )

    def handle_delete_version(
        self, request: Request, name: str, version: str
    ) -> JsonResponse:
        user = self._require_user(request)
        try:
            removed_package = self.store.delete_version(
                name, version, user.sub, force=self._is_admin(user)
            )
        except Forbidden as exc:
            raise ApiError(
                403, "forbidden", "only the publisher or an admin can delete"
            ) from exc
        except NotFound as exc:
            raise ApiError(404, "not_found", "package/version not found") from exc
        except StoreError as exc:
            raise ApiError(400, "delete_failed", str(exc)) from exc
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "name": name,
                    "version": version,
                    "deleted": True,
                    "package_removed": removed_package,
                },
            },
        )

    def handle_delete_package(
        self, request: Request, name: str
    ) -> JsonResponse:
        user = self._require_user(request)
        try:
            self.store.delete_package(name, user.sub, force=self._is_admin(user))
        except Forbidden as exc:
            raise ApiError(
                403, "forbidden", "only the publisher or an admin can delete"
            ) from exc
        except NotFound as exc:
            raise ApiError(404, "not_found", "package not found") from exc
        except StoreError as exc:
            raise ApiError(400, "delete_failed", str(exc)) from exc
        return JsonResponse(
            200,
            {"ok": True, "data": {"name": name, "deleted": True}},
        )

    def _is_admin(self, user: User) -> bool:
        if not self.admin_emails and not self.admin_ids:
            return False
        email = (user.email or "").strip().lower()
        return bool(
            (email and email in self.admin_emails)
            or (user.sub and user.sub in self.admin_ids)
        )

    def _require_user(self, request: Request) -> User:
        header = request.header("Authorization")
        if not header.lower().startswith("bearer "):
            raise ApiError(
                401,
                "unauthorized",
                "Bearer <Cdifit access token> is required",
            )
        token = header[len("Bearer ") :].strip()
        try:
            return self.auth.authenticate(token)
        except Unauthorized as exc:
            raise ApiError(
                401, "unauthorized", "invalid or expired Cdifit token"
            ) from exc
        except Exception as exc:
            raise ApiError(502, "auth_unavailable", str(exc)) from exc


def _segments(path: str) -> list[str]:
    raw = urllib.parse.urlsplit(path).path
    trimmed = raw.strip("/")
    if not trimmed:
        return []
    return [urllib.parse.unquote(part) for part in trimmed.split("/")]


def _pick(
    method: str, handler: str, allowed: tuple[str, ...], **params: str
) -> tuple[str | None, dict[str, str], tuple[str, ...]]:
    if method == allowed[0]:
        return handler, params, ()
    return None, {}, allowed


def _resolve(
    method: str, path: str
) -> tuple[str | None, dict[str, str], tuple[str, ...]]:
    lookup = "GET" if method == "HEAD" else method
    parts = _segments(path)
    if parts in ([], ["admin"]):
        return _pick(lookup, "handle_admin_page", ("GET",))
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        tail = parts[2:]
        if tail == ["health"]:
            return _pick(lookup, "handle_health", ("GET",))
        if tail == ["stats"]:
            return _pick(lookup, "handle_stats", ("GET",))
        if tail == ["admin", "stats"]:
            return _pick(lookup, "handle_stats", ("GET",))
        if tail == ["me"]:
            return _pick(lookup, "handle_me", ("GET",))
        if tail == ["packages"]:
            return _pick(lookup, "handle_list", ("GET",))
        if len(tail) == 2 and tail[0] == "packages":
            name = tail[1]
            if lookup == "DELETE":
                return "handle_delete_package", {"name": name}, ()
            return _pick(lookup, "handle_get_package", ("GET",), name=name)
        if len(tail) == 3 and tail[0] == "packages":
            name, version = tail[1], tail[2]
            if lookup == "GET":
                return "handle_get_version", {"name": name, "version": version}, ()
            if lookup == "PUT":
                return "handle_publish", {"name": name, "version": version}, ()
            if lookup == "DELETE":
                return (
                    "handle_delete_version",
                    {"name": name, "version": version},
                    (),
                )
            return None, {}, ("GET", "PUT", "DELETE")
        if len(tail) == 4 and tail[0] == "packages":
            action = tail[3]
            params = {"name": tail[1], "version": tail[2]}
            if action == "artifact":
                if lookup == "GET":
                    return "handle_artifact_download", params, ()
                if lookup == "POST":
                    return "handle_artifact_upload", params, ()
                return None, {}, ("GET", "POST")
            if action == "yank":
                if lookup == "POST":
                    return "handle_yank", params, ()
                return None, {}, ("POST",)
            if action == "latest":
                if lookup == "POST":
                    return "handle_set_latest", params, ()
                return None, {}, ("POST",)
    return None, {}, ()


def _decode_object(
    request: Request, limit: int, allowed: set[str]
) -> dict[str, Any]:
    try:
        raw = request.read_body(limit)
    except BodyReadError as exc:
        raise ApiError(400, "invalid_json", str(exc)) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise ApiError(
            400,
            "invalid_json",
            "json: cannot unmarshal value into Go struct field of type object",
        )
    for key in value:
        if key not in allowed:
            raise ApiError(400, "invalid_json", f'json: unknown field "{key}"')
    return value


def _string_field(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(
            400,
            "invalid_json",
            f"json: cannot unmarshal value into Go struct field .{field} of type string",
        )
    return value


def _int_field(body: dict[str, Any], field: str) -> int:
    value = body.get(field)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(
            400,
            "invalid_json",
            f"json: cannot unmarshal value into Go struct field .{field} of type int64",
        )
    return value


def _string_list_field(body: dict[str, Any], field: str) -> list[str]:
    value = body.get(field)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        raise ApiError(
            400,
            "invalid_json",
            f"json: cannot unmarshal value into Go struct field .{field} of type []string",
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ApiError(
                400,
                "invalid_json",
                f"json: cannot unmarshal value into Go struct field .{field} of type []string",
            )
        if item.strip():
            out.append(item.strip())
    return out


def _int_query(request: Request, name: str, default: int,
               *, minimum: int = 0) -> int:
    raw = request.query_value(name).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_query", f"{name} must be an integer") from exc
    if value < minimum:
        raise ApiError(
            400, "invalid_query", f"{name} must be >= {minimum}"
        )
    return value


def _identity_set(value: Any) -> set[str]:
    if value is None:
        return set()
    items = (re.split(r"[\s,;]+", value) if isinstance(value, str)
             else [str(item) for item in value])
    return {item.strip() for item in items if item.strip()}


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _remove_quietly(path: str) -> None:
    with suppress(OSError):
        os.unlink(path)


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    host, port = handler.client_address[0], handler.client_address[1]
    return f"{host}:{port}"


def _etag_matches(header_value: str, etag: str) -> bool:
    for candidate in header_value.split(","):
        value = candidate.strip()
        if value.startswith("W/"):
            value = value[2:].strip()
        if value == etag:
            return True
    return False


def _parse_range(header_value: str, size: int) -> tuple[int, int] | None:
    value = header_value.strip()
    if not value.lower().startswith("bytes="):
        return None
    spec = value[len("bytes=") :].strip()
    if "," in spec:
        return None
    start_text, _, end_text = spec.partition("-")
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    total = int(seconds)
    fraction = seconds - total
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    prefix = ""
    if hours:
        prefix += f"{hours}h"
    if hours or minutes:
        prefix += f"{minutes}m"
    tail = f"{secs + fraction:.3f}".rstrip("0").rstrip(".")
    return f"{prefix}{tail}s"


class RegistryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pic-registry/" + __version__
    sys_version = ""
    timeout = 120

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    def log_message(self, fmt: str, *args: Any) -> None:
        """请求日志统一在 _dispatch 中输出。"""

    def handle(self) -> None:
        """客户端提前断开（keep-alive 连接被重置）时保持安静。"""
        with suppress(ConnectionResetError, BrokenPipeError):
            super().handle()

    def _dispatch(self, method: str) -> None:
        started = time.monotonic()
        path = urllib.parse.urlsplit(self.path).path
        allowed_methods: tuple[str, ...] = ()
        status = 0
        try:
            handler_name, params, allowed_methods = _resolve(method, self.path)
            if handler_name is None:
                if allowed_methods:
                    raise ApiError(
                        405,
                        "method_not_allowed",
                        f"method {method} is not allowed",
                    )
                raise ApiError(404, "not_found", "not found")
            parsed = urllib.parse.urlsplit(self.path)
            request = Request(
                method=method,
                path=self.path,
                query=urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True
                ),
                headers=self.headers,
                rfile=self.rfile,
                client_ip=_client_ip(self),
            )
            result = getattr(self.server.app, handler_name)(request, **params)
            status = self._write_result(result)
        except ApiError as exc:
            extra = (
                {"Allow": ", ".join(allowed_methods)}
                if exc.status == 405
                else None
            )
            try:
                status = self._write_json(
                    exc.status, _error_payload(exc.code, exc.message), extra
                )
            except OSError:
                status = exc.status
        except (BrokenPipeError, ConnectionResetError):
            status = status or 499
        except Exception as exc:  # noqa: BLE001 - 兜底避免线程崩溃
            self.server.app.logger.exception("unhandled error")
            try:
                status = self._write_json(
                    500, _error_payload("internal", str(exc)), None
                )
            except OSError:
                status = 500
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self.server.app.logger.info(
            f"{method} {path} {status} {elapsed_ms:.1f}ms {_client_ip(self)}"
        )

    def _write_result(self, result: Any) -> int:
        if isinstance(result, JsonResponse):
            return self._write_json(result.status, result.payload, None)
        if isinstance(result, ArtifactResponse):
            return self._write_artifact(result)
        if isinstance(result, HtmlResponse):
            return self._write_html(result)
        raise ApiError(500, "internal", "invalid handler response")

    def _write_html(self, result: HtmlResponse) -> int:
        raw = result.body.encode("utf-8")
        self.send_response(result.status)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return result.status

    def _write_json(
        self, status: int, payload: dict[str, Any], extra: dict[str, str] | None
    ) -> int:
        raw = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return status

    def _write_artifact(self, result: ArtifactResponse) -> int:
        try:
            info = os.stat(result.path)
        except OSError as exc:
            raise ApiError(
                404, "artifact_not_found", "artifact not uploaded"
            ) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ApiError(404, "artifact_not_found", "artifact not uploaded")

        size = info.st_size
        etag = '"' + result.sha256 + '"'
        if_none_match = self.headers.get("If-None-Match")
        if if_none_match and _etag_matches(if_none_match, etag):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header(
                "Last-Modified", formatdate(info.st_mtime, usegmt=True)
            )
            self.end_headers()
            return 304

        headers: dict[str, str] = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": (
                f'attachment; filename="{result.name}-{result.version}.cinpkg"'
            ),
            "X-PIC-SHA256": result.sha256,
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Last-Modified": formatdate(info.st_mtime, usegmt=True),
        }
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header and size > 0:
            parsed = _parse_range(range_header, size)
            if parsed is not None:
                start, end = parsed
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        length = 0 if size == 0 else end - start + 1
        headers["Content-Length"] = str(length)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

        if self.command == "HEAD" or length == 0:
            return status
        with open(result.path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        return status


class RegistryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App):
        self.app = app
        super().__init__(address, RegistryHandler)


def _env(key: str, fallback: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or fallback


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>pic registry 后台管理</title>
<style>
:root{--bg:#0f1419;--panel:#171e26;--panel2:#1e2732;--line:#2b3644;--text:#e6edf3;--muted:#8b98a8;--cyan:#38bdf8;--green:#34d399;--yellow:#fbbf24;--red:#f87171;--radius:12px}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1b2a3a 0,var(--bg) 55%);color:var(--text);font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;min-height:100vh}
a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:18px 22px;border-bottom:1px solid var(--line);background:rgba(15,20,25,.72);backdrop-filter:blur(8px);position:sticky;top:0;z-index:5}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;letter-spacing:.2px}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.brand small{font-weight:400;color:var(--muted);font-size:12px}
main{max-width:1180px;margin:0 auto;padding:20px 18px 64px}
.card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:var(--radius);padding:16px}
.grid{display:grid;gap:12px}.stats{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:16px}
.stat .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.6px}.stat .v{font-size:26px;font-weight:700;margin-top:4px}.stat .v small{font-size:12px;color:var(--muted);font-weight:400}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
input,select,button{font:inherit;color:var(--text);background:#0d1218;border:1px solid var(--line);border-radius:9px;padding:8px 10px;outline:none}
input:focus,select:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(56,189,248,.14)}
button{cursor:pointer;background:#223042;border-color:#33465b}button:hover{filter:brightness(1.12)}button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:linear-gradient(135deg,#0ea5e9,#22d3ee);border-color:transparent;color:#04121b;font-weight:700}
button.danger{background:#3a1e24;border-color:#6b2a35;color:#fecaca}button.ghost{background:transparent}
.grow{flex:1;min-width:180px}.muted{color:var(--muted)}.small{font-size:12px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
tbody tr:hover{background:rgba(56,189,248,.06)}tr.clickable{cursor:pointer}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;border:1px solid var(--line);background:#0d1218;color:var(--muted)}
.pill.ok{color:var(--green);border-color:#1f5f46;background:#10231c}.pill.warn{color:var(--yellow);border-color:#5c4a13;background:#241f0d}.pill.bad{color:var(--red);border-color:#632a2a;background:#251515}
.stack{display:grid;gap:14px}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.kv{display:grid;grid-template-columns:120px 1fr;gap:6px 12px}.kv dt{color:var(--muted)}.kv dd{margin:0;word-break:break-all}
.hidden{display:none!important}.toast{position:fixed;right:18px;bottom:18px;max-width:min(420px,calc(100vw - 36px));padding:11px 14px;border-radius:10px;background:#16212c;border:1px solid var(--line);box-shadow:0 12px 36px rgba(0,0,0,.45);opacity:0;transform:translateY(8px);transition:.18s;z-index:20}
.toast.show{opacity:1;transform:none}.toast.err{border-color:#7f1d1d;background:#2a1414}.toast.ok{border-color:#14532d;background:#0f2318}
.login{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.login input{min-width:240px;flex:1}
.split{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr);gap:16px}@media(max-width:900px){.split{grid-template-columns:1fr}}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#0d1218;border:1px solid var(--line);color:var(--muted)}
.hl{color:var(--cyan)}.nowrap{white-space:nowrap}
footer{color:var(--muted);text-align:center;font-size:12px;padding:24px 0 8px}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="dot"></span><span>pic registry <small>后台管理</small></span></div>
  <div class="row">
    <span id="who" class="badge">未登录</span>
    <button class="ghost" id="refresh">刷新</button>
  </div>
</header>
<main>
  <section class="card" style="margin-bottom:16px">
    <div class="login">
      <input id="token" type="password" autocomplete="off" placeholder="Cdifit AccessToken（pic admin 可自动填入）">
      <button class="primary" id="save-token">保存令牌</button>
      <button class="ghost" id="clear-token">清除</button>
      <span class="muted small">令牌只保存在本机浏览器；读取/搜索公开，撤回、删除与设置 latest 需要发布者或管理员。</span>
    </div>
  </section>

  <section class="grid stats" id="stats"></section>

  <div class="split">
    <section class="card">
      <div class="toolbar">
        <input id="q" class="grow" placeholder="搜索包名 / 仓库 / 描述 / 关键词">
        <select id="sort">
          <option value="">相关度</option>
          <option value="name">名称</option>
          <option value="latest">最新版本</option>
          <option value="updated">更新时间</option>
          <option value="downloads">下载量</option>
          <option value="versions">版本数</option>
        </select>
        <select id="order"><option value="desc">降序</option><option value="asc">升序</option></select>
        <button class="primary" id="search">搜索</button>
      </div>
      <div id="package-count" class="muted small" style="margin-bottom:8px"></div>
      <table>
        <thead><tr><th>包</th><th>最新</th><th>版本</th><th>下载</th><th>更新时间</th></tr></thead>
        <tbody id="packages"><tr><td colspan="5" class="muted">加载中…</td></tr></tbody>
      </table>
    </section>

    <section class="card" id="detail">
      <h3 style="margin:2px 0 10px">选择左侧的包</h3>
      <p class="muted small">点击包名查看版本、发布者、摘要与制品信息，并进行版本管理。</p>
    </section>
  </div>
  <footer>package.codecin.org.cn · /api/v1 · <a href="/api/v1/health">health</a></footer>
</main>
<div id="toast" class="toast"></div>
<script>
const TOKEN_KEY = 'pic_admin_token';
let token = localStorage.getItem(TOKEN_KEY) || '';
let packages = [];
let current = null;

const $ = (id) => document.getElementById(id);
function esc(v){return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safeUrl(u){try{const x=new URL(String(u||''),location.href);return (x.protocol==='http:'||x.protocol==='https:')?x.href:'';}catch(e){return '';}}
function bytes(n){n=Number(n||0);const u=['B','KiB','MiB','GiB'];let i=0;let v=n;while(v>=1024&&i<u.length-1){v/=1024;i++;}return (i? v.toFixed(1):String(v))+' '+u[i];}
function date(v){if(!v||String(v).startsWith('0001'))return '-';const d=new Date(v);return isNaN(d)?String(v):d.toLocaleString('zh-CN',{hour12:false});}
function toast(msg, ok=true){const el=$('toast');el.textContent=msg;el.className='toast show '+(ok?'ok':'err');clearTimeout(toast._t);toast._t=setTimeout(()=>el.className='toast',3200);}

function loadToken(){token=localStorage.getItem(TOKEN_KEY)||'';$('token').value=token;updateWho();}
async function api(path, options={}){
  const headers=Object.assign({'Accept':'application/json'},options.headers||{});
  if(token)headers['Authorization']='Bearer '+token;
  if(options.body && !headers['Content-Type'])headers['Content-Type']='application/json';
  const resp=await fetch('/api/v1'+path,Object.assign({},options,{headers,cache:'no-store'}));
  let data={};try{data=await resp.json();}catch(e){}
  if(!resp.ok || data.ok===false){const err=(data.error&&data.error.message)||('HTTP '+resp.status);throw new Error(err);}
  return data.data;
}
async function updateWho(){
  if(!token){$('who').textContent='未登录（只读）';$('who').className='badge';return;}
  try{const me=await api('/me');$('who').textContent=(me.nickname||me.name||me.email||me.sub)+(me.admin?' · 管理员':'');$('who').className='badge';}
  catch(e){$('who').textContent='令牌无效或已过期';$('who').className='badge';}
}
async function loadStats(){
  try{const s=await api('/stats');
    $('stats').innerHTML=[
      ['包',s.packages],['版本',s.versions],['制品',s.artifacts],
      ['已撤回',s.yanked],['下载',s.downloads],['存储',bytes(s.storage_bytes)]
    ].map(([k,v])=>'<div class="card stat"><div class="k">'+esc(k)+'</div><div class="v">'+esc(v)+'</div></div>').join('');
  }catch(e){$('stats').innerHTML='<div class="card muted">统计加载失败：'+esc(e.message)+'</div>';}
}
async function loadPackages(){
  const q=$('q').value.trim(), sort=$('sort').value, order=$('order').value;
  const params=new URLSearchParams();if(q)params.set('q',q);if(sort)params.set('sort',sort);if(sort)params.set('order',order);params.set('limit','100');
  $('packages').innerHTML='<tr><td colspan="5" class="muted">搜索中…</td></tr>';
  try{const data=await api('/packages?'+params.toString());packages=data.packages||[];
    $('package-count').textContent='共 '+data.total+' 个包'+(data.total>packages.length?'（显示前 '+packages.length+' 个）':'');
    if(!packages.length){$('packages').innerHTML='<tr><td colspan="5" class="muted">没有匹配的包</td></tr>';return;}
    $('packages').innerHTML=packages.map(p=>{
      const active=p.latest?'<span class="pill ok">'+esc(p.latest)+'</span>':'<span class="pill warn">无可用版本</span>';
      return '<tr class="clickable" data-name="'+esc(p.name)+'"><td><b>'+esc(p.name)+'</b>'+(p.description?'<div class="muted small">'+esc(p.description)+'</div>':'')+'</td><td>'+active+'</td><td>'+esc(p.versions)+'</td><td>'+esc(p.downloads||0)+'</td><td class="nowrap">'+esc(date(p.updated_at))+'</td></tr>';
    }).join('');
    document.querySelectorAll('#packages tr.clickable').forEach(tr=>tr.addEventListener('click',()=>loadDetail(tr.dataset.name)));
  }catch(e){$('packages').innerHTML='<tr><td colspan="5" class="muted">加载失败：'+esc(e.message)+'</td></tr>';}
}
async function loadDetail(name){
  $('detail').innerHTML='<h3 style="margin:2px 0 10px">'+esc(name)+'</h3><p class="muted small">加载中…</p>';
  try{const pkg=await api('/packages/'+encodeURIComponent(name));current=pkg;renderDetail(pkg);}
  catch(e){$('detail').innerHTML='<h3 style="margin:2px 0 10px">'+esc(name)+'</h3><p class="muted">加载失败：'+esc(e.message)+'</p>';}
}
function renderDetail(p){
  const versions=Object.keys(p.versions||{}).sort().reverse();
  const repoUrl=safeUrl(p.repo);
  const rows=versions.map(v=>{
    const e=p.versions[v];const yanked=!!e.yanked;const isLatest=p.latest===v;
    return '<tr><td><b>'+esc(v)+'</b> '+(isLatest?'<span class="pill ok">latest</span> ':'')+(yanked?'<span class="pill bad">已撤回</span>':'')+'</td>'
      +'<td class="small">'+esc(e.tag||'-')+'<div class="muted">'+esc((e.rev||'').slice(0,12)||'-')+'</div></td>'
      +'<td class="small">'+esc(bytes(e.archive_size))+'<div class="muted">'+esc((e.archive_sha256||'').slice(0,12)||'无制品')+'</div></td>'
      +'<td class="small">'+esc(e.downloads||0)+'</td>'
      +'<td class="small">'+esc(date(e.published_at))+'<div class="muted">'+esc((e.publisher&&(e.publisher.email||e.publisher.nickname))||'-')+'</div></td>'
      +'<td class="nowrap"><button data-act="yank" data-v="'+esc(v)+'" data-y="'+(yanked?'0':'1')+'">'+(yanked?'恢复':'撤回')+'</button> '
      +'<button data-act="latest" data-v="'+esc(v)+'" '+(isLatest?'disabled':'')+'>设为 latest</button> '
      +'<button class="danger" data-act="delver" data-v="'+esc(v)+'">删除</button></td></tr>';
  }).join('');
  $('detail').innerHTML='<div class="row" style="justify-content:space-between;align-items:flex-start"><h3 style="margin:2px 0 10px">'+esc(p.name)+'</h3><button class="danger" id="delpkg">删除整个包</button></div>'
    +'<dl class="kv"><dt>最新</dt><dd>'+(p.latest?'<span class="pill ok">'+esc(p.latest)+'</span>':'<span class="pill warn">无</span>')+'</dd>'
    +'<dt>Git 仓库</dt><dd>'+(repoUrl?'<a href="'+esc(repoUrl)+'" target="_blank" rel="noreferrer">'+esc(p.repo)+'</a>':'-')+'</dd>'
    +'<dt>描述</dt><dd>'+esc(p.description||'-')+'</dd>'
    +'<dt>关键词</dt><dd>'+esc((p.keywords||[]).join(', ')||'-')+'</dd>'
    +'<dt>许可证</dt><dd>'+esc(p.license||'-')+'</dd>'
    +'<dt>主页</dt><dd>'+(safeUrl(p.homepage)?'<a href="'+esc(safeUrl(p.homepage))+'" target="_blank" rel="noreferrer">'+esc(p.homepage)+'</a>':esc(p.homepage||'-'))+'</dd>'
    +'<dt>入口</dt><dd>'+esc(p.entry||'-')+'</dd>'
    +'<dt>模块</dt><dd>'+esc((p.modules||[]).join(', ')||'-')+'</dd>'
    +'<dt>下载 / 版本</dt><dd>'+esc(p.downloads||0)+' / '+esc(versions.length)+'</dd>'
    +'<dt>更新时间</dt><dd>'+esc(date(p.updated_at))+'</dd></dl>'
    +'<h4 style="margin:18px 0 8px">版本</h4>'
    +'<table><thead><tr><th>版本</th><th>标签 / 提交</th><th>制品</th><th>下载</th><th>发布</th><th>操作</th></tr></thead><tbody>'+rows+'</tbody></table>';
  document.querySelectorAll('#detail button[data-act]').forEach(btn=>btn.addEventListener('click',()=>versionAction(btn.dataset.act,btn.dataset.v,btn.dataset.y==='1')));
  $('delpkg').addEventListener('click',deletePackage);
}
async function versionAction(act,version,yanked){
  if(!token){toast('先保存 Cdifit AccessToken',false);return;}
  const name=current.name;
  try{
    if(act==='yank'){
      if(!confirm((yanked?'撤回':'恢复')+' '+name+'@'+version+' ?'))return;
      await api('/packages/'+encodeURIComponent(name)+'/'+encodeURIComponent(version)+'/yank',{method:'POST',body:JSON.stringify({yanked:yanked})});
      toast((yanked?'已撤回 ':'已恢复 ')+version);
    }else if(act==='latest'){
      await api('/packages/'+encodeURIComponent(name)+'/'+encodeURIComponent(version)+'/latest',{method:'POST',body:JSON.stringify({latest:true})});
      toast('latest 已指向 '+version);
    }else if(act==='delver'){
      if(!confirm('删除 '+name+'@'+version+' 及其制品？此操作不可恢复。'))return;
      await api('/packages/'+encodeURIComponent(name)+'/'+encodeURIComponent(version),{method:'DELETE'});
      toast('已删除 '+version);
    }
    await Promise.all([loadDetail(name),loadPackages(),loadStats()]);
  }catch(e){toast(e.message,false);}
}
async function deletePackage(){
  if(!token){toast('先保存 Cdifit AccessToken',false);return;}
  const name=current.name;
  if(!confirm('删除整个包 '+name+'（全部版本与制品）？此操作不可恢复。'))return;
  try{await api('/packages/'+encodeURIComponent(name),{method:'DELETE'});toast('已删除 '+name);current=null;$('detail').innerHTML='<h3 style="margin:2px 0 10px">选择左侧的包</h3><p class="muted small">包已删除。</p>';await Promise.all([loadPackages(),loadStats()]);}
  catch(e){toast(e.message,false);}
}
$('save-token').addEventListener('click',()=>{token=$('token').value.trim();if(token)localStorage.setItem(TOKEN_KEY,token);else localStorage.removeItem(TOKEN_KEY);updateWho();toast('令牌已保存');});
$('clear-token').addEventListener('click',()=>{token='';$('token').value='';localStorage.removeItem(TOKEN_KEY);updateWho();toast('令牌已清除');});
$('search').addEventListener('click',loadPackages);
$('q').addEventListener('keydown',e=>{if(e.key==='Enter')loadPackages();});
$('sort').addEventListener('change',loadPackages);
$('order').addEventListener('change',loadPackages);
$('refresh').addEventListener('click',()=>Promise.all([loadStats(),loadPackages(),current?loadDetail(current.name):Promise.resolve()]));
(function init(){
  const raw=location.hash.slice(1);const params=new URLSearchParams(raw);const ht=params.get('token');
  if(ht){token=ht;localStorage.setItem(TOKEN_KEY,ht);history.replaceState(null,'',location.pathname);}
  $('token').value=token;updateWho();loadStats();loadPackages();
})();
</script>
</body>
</html>
"""


def _env(key: str, fallback: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or fallback


def _parse_addr(value: str) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port_text = rest.lstrip(":")
    elif ":" in value:
        host, _, port_text = value.rpartition(":")
    else:
        host, port_text = "", value
    return host, int(port_text)


def _configure_logging(console: Console) -> logging.Logger:
    logger = logging.getLogger("pic-registry")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = RichHandler(
        console=console,
        show_path=False,
        show_level=False,
        markup=False,
        rich_tracebacks=True,
        log_time_format="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _print_banner(
    console: Console,
    addr: str,
    data_dir: str,
    public_url: str,
    cdifit_base: str,
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("version", __version__)
    table.add_row("addr", addr)
    table.add_row("data", os.path.abspath(data_dir))
    table.add_row("public", public_url)
    table.add_row("cdifit", cdifit_base)
    console.print(
        Panel(
            table,
            title="[bold]pic-registry[/bold]",
            subtitle="package.codecin.org.cn",
            border_style="cyan",
        )
    )


def _raise_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> int:
    console = Console()
    addr = _env("PIC_SERVER_ADDR", ":18080")
    data_dir = _env("PIC_SERVER_DATA", "./data")
    public_url = _env("PIC_PUBLIC_URL", "https://package.codecin.org.cn")
    cdifit_base = _env("CDIFIT_API_BASE", "https://www.cdifit.cn")

    logger = _configure_logging(console)
    try:
        registry_store = Store(data_dir)
    except OSError as exc:
        logger.error(f"store: {exc}")
        return 1

    app = App(registry_store, Authenticator(cdifit_base), public_url, logger)
    if app.admin_emails or app.admin_ids:
        logger.info(
            "admin: %d email(s), %d id(s)",
            len(app.admin_emails),
            len(app.admin_ids),
        )
    else:
        logger.info(
            "admin: 未配置 PIC_ADMIN_EMAILS / PIC_ADMIN_IDS; 只能管理自己发布的包"
        )

    try:
        host, port = _parse_addr(addr)
        server = RegistryServer((host, port), app)
    except (ValueError, OSError) as exc:
        logger.error(f"serve: {exc}")
        return 1

    _print_banner(console, addr, data_dir, public_url, cdifit_base)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGINT, _raise_interrupt)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
