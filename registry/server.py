"""package.codecin.org.cn —— pic 中央注册/分发服务器。

仅使用 Python 标准库的 HTTP 服务器（不使用 Flask），rich 负责终端输出与日志。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
    PackageSummary,
    RepoMismatch,
    Store,
    StoreError,
    Version,
    VersionExists,
    format_time,
    latest,
    validate_digest,
    validate_name,
    validate_version,
)
from version import __version__

MAX_METADATA_BYTES = 1 << 20
MAX_ARTIFACT_BYTES = 256 << 20

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
}
_YANK_FIELDS = {"yanked"}


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
    ):
        self.store = store
        self.auth = authenticator
        self.public_url = (public_url or "").rstrip("/")
        self.logger = logger
        self.started = time.monotonic()

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
        query = request.query_value("q").strip().lower()
        try:
            packages = self.store.list_packages()
        except StoreError as exc:
            raise ApiError(500, "store_error", str(exc)) from exc
        summaries: list[PackageSummary] = []
        for pkg in packages:
            if (
                query
                and query not in pkg.name.lower()
                and query not in pkg.repo.lower()
            ):
                continue
            summaries.append(
                PackageSummary(
                    name=pkg.name,
                    repo=pkg.repo,
                    latest=latest(pkg.versions),
                    versions=len(pkg.versions),
                    updated_at=pkg.updated_at,
                )
            )
        return JsonResponse(
            200,
            {
                "ok": True,
                "data": {
                    "packages": [summary.to_json() for summary in summaries],
                    "count": len(summaries),
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
        try:
            created = self.store.put_version(
                name, _string_field(body, "repo").strip(), entry
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
            updated = self.store.yank(name, version, yanked, user.sub)
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
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        tail = parts[2:]
        if tail == ["health"]:
            return _pick(lookup, "handle_health", ("GET",))
        if tail == ["packages"]:
            return _pick(lookup, "handle_list", ("GET",))
        if len(tail) == 2 and tail[0] == "packages":
            return _pick(lookup, "handle_get_package", ("GET",), name=tail[1])
        if len(tail) == 3 and tail[0] == "packages":
            name, version = tail[1], tail[2]
            if lookup == "GET":
                return "handle_get_version", {"name": name, "version": version}, ()
            if lookup == "PUT":
                return "handle_publish", {"name": name, "version": version}, ()
            return None, {}, ("GET", "PUT")
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
        raise ApiError(500, "internal", "invalid handler response")

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
