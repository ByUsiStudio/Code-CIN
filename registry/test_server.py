"""pic 注册服务器的端到端测试（pytest）。

覆盖：发布元数据、上传/下载制品、幂等发布、版本冲突、yank、搜索与参数校验。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auth
import pytest
import server
import store

TEST_TOKEN = "test-token"


class _AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/v4/session/oauth/userinfo":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != "Bearer " + TEST_TOKEN:
            # 与真实 Cdifit 一致: 无效 Token 返回 HTTP 200 + code=401
            body = b'{"code":401,"msg":"Login required"}'
        else:
            body = (
                b'{"code":0,"data":{"sub":"u1","email":"user@example.com",'
                b'"name":"Tester"}}'
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


@pytest.fixture()
def registry(tmp_path):
    auth_server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    auth_thread = threading.Thread(target=auth_server.serve_forever, daemon=True)
    auth_thread.start()

    logger = logging.getLogger("pic-registry-test")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    registry_store = store.Store(str(tmp_path))
    app = server.App(
        registry_store,
        auth.Authenticator(f"http://127.0.0.1:{auth_server.server_port}"),
        "https://package.codecin.org.cn",
        logger,
    )
    httpd = server.RegistryServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        auth_server.shutdown()
        auth_server.server_close()


def call(
    httpd,
    method: str,
    path: str,
    body: bytes | None = None,
    token: str | None = TEST_TOKEN,
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
):
    url = f"http://127.0.0.1:{httpd.server_port}{path}"
    request = urllib.request.Request(url, data=body, method=method)
    if token is not None:
        request.add_header("Authorization", "Bearer " + token)
    if body is not None and content_type:
        request.add_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def publish(httpd, name: str, version: str, meta: dict):
    return call(
        httpd,
        "PUT",
        f"/api/v1/packages/{name}/{version}",
        json.dumps(meta).encode("utf-8"),
        content_type="application/json",
    )


def test_publish_artifact_and_yank(registry) -> None:
    app, httpd = registry
    archive = b"fake-cinpkg"
    archive_sha = hashlib.sha256(archive).hexdigest()
    meta = {
        "repo": "https://github.com/u/hello",
        "tag": "v1.0.0",
        "rev": "abc123",
        "source_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "archive_sha256": archive_sha,
        "archive_size": len(archive),
    }

    status, _, body = publish(httpd, "hello", "1.0.0", meta)
    assert status == 201, body

    status, _, body = call(
        httpd,
        "POST",
        f"/api/v1/packages/hello/1.0.0/artifact?sha256={archive_sha}",
        archive,
        content_type="application/octet-stream",
    )
    assert status == 200, body
    uploaded = json.loads(body)
    assert uploaded["data"]["url"].endswith("/api/v1/packages/hello/1.0.0/artifact")

    status, _, body = call(httpd, "GET", "/api/v1/packages/hello")
    assert status == 200, body
    pkg = json.loads(body)["data"]
    entry = pkg["versions"]["1.0.0"]
    assert entry["archive_sha256"] == archive_sha
    assert entry["publisher"]["id"] == "u1"

    status, headers, got = call(
        httpd, "GET", "/api/v1/packages/hello/1.0.0/artifact"
    )
    assert status == 200
    assert got == archive
    assert headers.get("X-PIC-SHA256") == archive_sha

    status, headers, got = call(
        httpd,
        "GET",
        "/api/v1/packages/hello/1.0.0/artifact",
        headers={"Range": "bytes=0-3"},
    )
    assert status == 206
    assert got == archive[:4]
    assert headers.get("Content-Range") == f"bytes 0-3/{len(archive)}"

    # 相同内容重复发布是幂等的
    status, _, body = publish(httpd, "hello", "1.0.0", meta)
    assert status == 200, body

    # 不同内容必须拒绝
    meta["archive_sha256"] = "c" * 64
    status, _, body = publish(httpd, "hello", "1.0.0", meta)
    assert status == 409, body

    status, _, body = call(
        httpd,
        "POST",
        "/api/v1/packages/hello/1.0.0/yank",
        b'{"yanked":true}',
        content_type="application/json",
    )
    assert status == 200, body

    record = os.path.join(app.store.root, "packages", "hello.json")
    with open(record, encoding="utf-8") as handle:
        assert '"yanked": true' in handle.read()


def test_list_validation_and_auth(registry) -> None:
    _, httpd = registry
    meta = {
        "repo": "https://github.com/u/hello",
        "tag": "v1.0.0",
        "source_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "archive_sha256": "d" * 64,
        "archive_size": 3,
    }
    status, _, body = publish(httpd, "hello", "1.0.0", meta)
    assert status == 201, body

    status, _, body = call(httpd, "GET", "/api/v1/packages?q=hello")
    assert status == 200, body
    listing = json.loads(body)["data"]
    assert listing["count"] == 1
    assert listing["packages"][0]["latest"] == "1.0.0"

    status, _, _ = publish(httpd, "bad%20name", "1.0.0", meta)
    assert status == 400

    status, _, _ = call(
        httpd,
        "PUT",
        "/api/v1/packages/hello/2.0.0",
        json.dumps(meta).encode("utf-8"),
        token=None,
        content_type="application/json",
    )
    assert status == 401

    status, _, _ = call(
        httpd,
        "PUT",
        "/api/v1/packages/hello/2.0.0",
        json.dumps(meta).encode("utf-8"),
        token="bad-token",
        content_type="application/json",
    )
    assert status == 401

    unknown = dict(meta, unexpected=True)
    status, _, _ = publish(httpd, "hello", "2.0.0", unknown)
    assert status == 400

    status, _, body = call(
        httpd, "GET", "/api/v1/packages/hello/1.0.0/artifact"
    )
    assert status == 404
    assert json.loads(body)["error"]["code"] == "artifact_not_found"

    status, _, _ = call(httpd, "DELETE", "/api/v1/packages/hello")
    assert status == 405

    status, _, _ = call(httpd, "GET", "/api/v1/packages/missing")
    assert status == 404
