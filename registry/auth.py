"""Cdifit AccessToken 校验。

Cdifit 只用于登录和 Token 校验，不存储包数据。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass

from store import Publisher
from version import __version__


class Unauthorized(Exception):
    """Token 为空、无效或已过期。"""


@dataclass
class User:
    sub: str = ""
    name: str = ""
    email: str = ""
    nickname: str = ""

    def publisher(self) -> Publisher:
        return Publisher(
            id=self.sub, email=self.email, nickname=self.nickname or self.name
        )


@dataclass
class _CacheEntry:
    expires: float
    user: User | None = None
    error: str | None = None
    unauthorized: bool = False


class Authenticator:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.ttl = 300.0
        self.negative_ttl = 30.0
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def authenticate(self, token: str) -> User:
        token = (token or "").strip()
        if not token:
            raise Unauthorized()
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and now < entry.expires:
                if entry.unauthorized:
                    raise Unauthorized()
                if entry.error is not None:
                    raise RuntimeError(entry.error)
                assert entry.user is not None
                return entry.user

        try:
            user = self._fetch(token)
        except Unauthorized:
            self._remember(key, _CacheEntry(now + self.negative_ttl, unauthorized=True))
            raise
        except RuntimeError as exc:
            self._remember(key, _CacheEntry(now + self.negative_ttl, error=str(exc)))
            raise
        self._remember(key, _CacheEntry(now + self.ttl, user=user))
        return user

    def _remember(self, key: str, entry: _CacheEntry) -> None:
        with self._lock:
            self._cache[key] = entry

    def _fetch(self, token: str) -> User:
        if not self.base_url:
            raise RuntimeError("CDIFIT_API_BASE is not configured")
        request = urllib.request.Request(
            self.base_url + "/api/v4/session/oauth/userinfo",
            method="GET",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                "User-Agent": "codecin-pic-registry/" + __version__,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1 << 20)
        except urllib.error.HTTPError as exc:
            body = ""
            with suppress(Exception):
                body = exc.read(1 << 20).decode("utf-8", "replace").strip()
            if exc.code in (401, 403):
                raise Unauthorized() from exc
            raise RuntimeError(f"cdifit userinfo: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cdifit userinfo: {exc.reason}") from exc
        except OSError as exc:
            raise RuntimeError(f"cdifit userinfo: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cdifit userinfo: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("cdifit userinfo: invalid JSON: expected object")

        # Cdifit 对无效/过期 Token 常以 HTTP 200 + code=401 返回,
        # 必须映射为 Unauthorized, 否则会被当成上游故障而报 502。
        code = payload.get("code", 0)
        if code in (401, 403):
            raise Unauthorized()
        if code not in (0, 200):
            raise RuntimeError(f"cdifit userinfo: {payload.get('msg') or code}")
        fields = payload.get("data")
        if not isinstance(fields, dict):
            fields = payload
        sub = fields.get("sub") or ""
        if not sub:
            raise RuntimeError("cdifit userinfo: missing sub")
        return User(
            sub=str(sub),
            name=str(fields.get("name") or ""),
            email=str(fields.get("email") or ""),
            nickname=str(fields.get("nickname") or ""),
        )
