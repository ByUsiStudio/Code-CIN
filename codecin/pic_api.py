"""Cdifit 中央服务 API 客户端 (pic v6.2.0+)。

pic 的中央注册表不复用 Git 服务器: 包记录 (标识名、Git 获取地址、版本列表、
摘要/安全校验信息) 存放在 Cdifit 的文件 API 中。本模块只负责:

* ``POST /api/v4/session/token`` 密码登录 (含 2FA);
* ``PUT /api/v4/session/token/refresh`` 刷新令牌;
* ``DELETE /api/v4/session/token`` 退出;
* ``POST /api/v4/session/oauth/userinfo`` 用户信息;
* ``/api/v4/file*`` 文件读写 (注册表 JSON);
* ``POST /api/v4/file/url`` 临时下载链接。

API 文档: https://docs.cdifit.com/zh/api/session
"""

import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .packaging.model import ManifestError

DEFAULT_API_BASE = 'https://www.cdifit.cn'
API_PREFIX = '/api/v4'
__all__ = [
    'ApiError', 'AuthState', 'AuthStore', 'CdifitClient', 'TwoFactorRequired',
    'DEFAULT_API_BASE', 'auth_path',
]


class ApiError(RuntimeError):
    """Cdifit API 返回非 0 code 或 HTTP 错误。"""

    def __init__(self, message: str, *, status: int = 0, code: int = 0,
                 data: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data


class TwoFactorRequired(ApiError):
    """密码登录返回 code=203, 需要 2FA。"""

    def __init__(self, session: str):
        super().__init__('需要两步验证', code=203, data=session)
        self.session = session


@dataclass
class AuthState:
    api_base: str = DEFAULT_API_BASE
    access_token: str = ''
    refresh_token: str = ''
    access_expires: Optional[str] = None
    refresh_expires: Optional[str] = None
    user: Dict[str, Any] = field(default_factory=dict)

    @property
    def email(self) -> str:
        return str(self.user.get('email') or '')

    @property
    def display_name(self) -> str:
        return str(self.user.get('nickname') or self.user.get('name')
                   or self.email or self.user.get('id') or 'unknown')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'api_base': self.api_base,
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'access_expires': self.access_expires,
            'refresh_expires': self.refresh_expires,
            'user': self.user,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AuthState':
        return cls(
            api_base=str(data.get('api_base') or DEFAULT_API_BASE),
            access_token=str(data.get('access_token') or ''),
            refresh_token=str(data.get('refresh_token') or ''),
            access_expires=data.get('access_expires'),
            refresh_expires=data.get('refresh_expires'),
            user=dict(data.get('user') or {}),
        )


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _expiring(value: Any, skew: int = 60) -> bool:
    parsed = _parse_time(value)
    if parsed is None:
        return False
    return parsed.timestamp() - skew <= time.time()


def _pic_home() -> str:
    return os.path.abspath(os.environ.get('PIC_HOME')
                           or os.path.join(os.path.expanduser('~'), '.pic'))


def auth_path() -> str:
    return os.path.join(_pic_home(), 'auth.json')


class AuthStore:
    """``$PIC_HOME/auth.json`` 的读写 (权限尽量设为 0600)。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or auth_path()

    def load(self) -> Optional[AuthState]:
        if not os.path.isfile(self.path):
            return None
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            state = AuthState.from_dict(data)
            return state if state.access_token else None
        except (OSError, ValueError, TypeError):
            return None

    def save(self, state: AuthState) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = json.dumps(state.to_dict(), ensure_ascii=False,
                             indent=2) + '\n'
        fd, tmp = tempfile.mkstemp(prefix='.auth-', suffix='.json',
                                   dir=os.path.dirname(self.path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(payload)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


class CdifitClient:
    """轻量 HTTP 客户端 (标准库, 无第三方依赖)。"""

    def __init__(self, api_base: str = DEFAULT_API_BASE,
                 auth: Optional[AuthState] = None,
                 store: Optional[AuthStore] = None,
                 timeout: int = 25):
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip('/')
        self.auth = auth
        self.store = store
        self.timeout = timeout

    # ---------------- 底层请求 ----------------

    def _url(self, path: str) -> str:
        if path.startswith('http://') or path.startswith('https://'):
            return path
        return self.api_base + (path if path.startswith('/') else '/' + path)

    def _send(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              token: Optional[str] = None) -> Any:
        headers = {'Accept': 'application/json',
                   'User-Agent': f'pic/{_version()}'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self._url(path), data=data,
                                         headers=headers, method=method)
        status = 0
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
        except urllib.error.URLError as e:
            raise ApiError(f'无法连接 Cdifit API: {e.reason}') from e
        except OSError as e:
            raise ApiError(f'无法连接 Cdifit API: {e}') from e

        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except (ValueError, UnicodeDecodeError):
            text = raw.decode('utf-8', 'replace')[:300]
            raise ApiError(
                f'Cdifit API 返回非 JSON (HTTP {status}): {text}',
                status=status) from None

        if not isinstance(payload, dict):
            return payload
        code = payload.get('code', 0)
        if code == 203:
            raise TwoFactorRequired(str(payload.get('data') or ''))
        if code not in (0, None):
            raise ApiError(str(payload.get('msg') or 'Cdifit API 请求失败'),
                           status=status, code=int(code or 0),
                           data=payload.get('data'))
        return payload.get('data', payload)

    def _token(self) -> Optional[str]:
        return self.auth.access_token if self.auth else None

    def _refresh_if_needed(self) -> None:
        if not self.auth:
            return
        if self.auth.access_expires and _expiring(self.auth.access_expires):
            self.refresh()

    def ensure_token(self) -> None:
        """确保 access token 存在且未过期 (必要时刷新)。"""
        if not self.auth or not self.auth.access_token:
            raise ApiError('未登录: 请先运行 `pic login`', status=401)
        self._refresh_if_needed()

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None,
                 auth: bool = True, retry: bool = True) -> Any:
        if auth:
            if not self.auth or not self.auth.access_token:
                raise ApiError('未登录: 请先运行 `pic login`', status=401)
            self._refresh_if_needed()
        try:
            return self._send(method, path, body, self._token() if auth else None)
        except ApiError as e:
            if auth and e.status in (401, 403) and retry and self.auth \
                    and self.auth.refresh_token:
                self.refresh()
                return self._send(method, path, body, self._token())
            raise

    # ---------------- 会话 ----------------

    def login(self, email: str, password: str,
              captcha: Optional[str] = None,
              ticket: Optional[str] = None) -> AuthState:
        body: Dict[str, Any] = {'email': email, 'password': password}
        if captcha:
            body['captcha'] = captcha
        if ticket:
            body['ticket'] = ticket
        payload = self._send('POST', API_PREFIX + '/session/token', body)
        data = payload if isinstance(payload, dict) else {}
        token = data.get('token') or {}
        user = data.get('user') or {}
        if not token.get('access_token'):
            raise ApiError('登录响应缺少 access_token', data=data)
        self.auth = AuthState(
            api_base=self.api_base,
            access_token=str(token.get('access_token')),
            refresh_token=str(token.get('refresh_token') or ''),
            access_expires=token.get('access_expires'),
            refresh_expires=token.get('refresh_expires'),
            user=dict(user),
        )
        if self.store:
            self.store.save(self.auth)
        return self.auth

    def finish_2fa(self, session: str, code: str) -> AuthState:
        payload = self._send('POST', API_PREFIX + '/session/token/2fa',
                             {'session': session, 'code': code})
        data = payload if isinstance(payload, dict) else {}
        token = data.get('token') or {}
        user = data.get('user') or {}
        if not token.get('access_token'):
            raise ApiError('2FA 响应缺少 access_token', data=data)
        self.auth = AuthState(
            api_base=self.api_base,
            access_token=str(token.get('access_token')),
            refresh_token=str(token.get('refresh_token') or ''),
            access_expires=token.get('access_expires'),
            refresh_expires=token.get('refresh_expires'),
            user=dict(user) or (self.auth.user if self.auth else {}),
        )
        if self.store:
            self.store.save(self.auth)
        return self.auth

    def refresh(self) -> AuthState:
        if not self.auth or not self.auth.refresh_token:
            raise ApiError('没有可用的 refresh_token, 请重新登录', status=401)
        payload = self._send(
            'PUT', API_PREFIX + '/session/token/refresh',
            {'refresh_token': self.auth.refresh_token},
            token=self.auth.refresh_token)
        data = payload if isinstance(payload, dict) else {}
        if not data.get('access_token'):
            raise ApiError('刷新令牌响应缺少 access_token', data=data)
        self.auth.access_token = str(data['access_token'])
        if data.get('refresh_token'):
            self.auth.refresh_token = str(data['refresh_token'])
        self.auth.access_expires = data.get('access_expires') \
            or self.auth.access_expires
        self.auth.refresh_expires = data.get('refresh_expires') \
            or self.auth.refresh_expires
        if self.store:
            self.store.save(self.auth)
        return self.auth

    def logout(self) -> None:
        if self.auth and self.auth.access_token:
            try:
                self._request('DELETE', API_PREFIX + '/session/token')
            except ApiError:
                pass
        self.auth = None
        if self.store:
            self.store.clear()

    def userinfo(self) -> Dict[str, Any]:
        payload = self._request('POST',
                                API_PREFIX + '/session/oauth/userinfo', {})
        if isinstance(payload, dict) and payload.get('sub'):
            if self.auth:
                self.auth.user.update(payload)
                if self.store:
                    self.store.save(self.auth)
            return payload
        return self.auth.user if self.auth else {}

    # ---------------- 文件 ----------------

    def file_info(self, uri: str) -> Optional[Dict[str, Any]]:
        query = urllib.parse.urlencode({'uri': uri})
        try:
            payload = self._request('GET', API_PREFIX + '/file/info?' + query)
        except ApiError as e:
            if e.status == 404 or e.code in (404, 40401, 40402):
                return None
            raise
        return payload if isinstance(payload, dict) else None

    def file_exists(self, uri: str) -> bool:
        return self.file_info(uri) is not None

    def create_folder(self, uri: str) -> None:
        if self.file_exists(uri):
            return
        try:
            self._request('PUT', API_PREFIX + '/file',
                          {'uri': uri, 'type': 1})
        except ApiError:
            # 并发创建/已存在时 API 可能返回非 0, 以最终存在为准
            if not self.file_exists(uri):
                raise

    def create_file(self, uri: str, content: str,
                    overwrite: bool = True) -> Dict[str, Any]:
        payload = self._request('POST', API_PREFIX + '/file/create',
                                {'uri': uri, 'content': content,
                                 'overwrite': overwrite})
        return payload if isinstance(payload, dict) else {}

    def read_text(self, uri: str) -> Optional[str]:
        info = self.file_info(uri)
        if info is None:
            return None
        payload = self._request('POST', API_PREFIX + '/file/url',
                                {'uris': [uri], 'download': True})
        urls = (payload or {}).get('urls') if isinstance(payload, dict) else None
        if not urls:
            raise ApiError(f'无法获取下载链接: {uri}', data=payload)
        url = urls[0].get('url') if isinstance(urls[0], dict) else urls[0]
        if not url:
            raise ApiError(f'下载链接为空: {uri}')
        request = urllib.request.Request(str(url),
                                         headers={'User-Agent': f'pic/{_version()}'})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return resp.read().decode('utf-8')
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f'下载文件失败: {uri}: {e}') from e

    def list_files(self, uri: str, page: int = 0,
                   page_size: int = 200) -> List[Dict[str, Any]]:
        query = urllib.parse.urlencode({'uri': uri, 'page': page,
                                        'page_size': page_size})
        payload = self._request('GET', API_PREFIX + '/file?' + query)
        if not isinstance(payload, dict):
            return []
        files = payload.get('files')
        return files if isinstance(files, list) else []

    def delete_file(self, uri: str, skip_soft_delete: bool = True) -> None:
        self._request('DELETE', API_PREFIX + '/file',
                      {'uris': [uri], 'skip_soft_delete': skip_soft_delete})

    # ---------------- 便捷 JSON ----------------

    def read_json(self, uri: str) -> Optional[Dict[str, Any]]:
        text = self.read_text(uri)
        if text is None:
            return None
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ApiError(f'注册表文件不是合法 JSON: {uri}') from e
        if not isinstance(data, dict):
            raise ApiError(f'注册表文件顶层必须是对象: {uri}')
        return data

    def write_json(self, uri: str, data: Dict[str, Any],
                   overwrite: bool = True) -> None:
        self.create_file(uri, json.dumps(data, ensure_ascii=False, indent=2),
                         overwrite=overwrite)


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:  # pragma: no cover
        return '0'


def basic_auth_header(email: str, password: str) -> str:
    token = base64.b64encode(f'{email}:{password}'.encode()).decode()
    return 'Basic ' + token


def load_auth(api_base: Optional[str] = None) -> Optional[AuthState]:
    state = AuthStore().load()
    if state is None:
        return None
    if api_base and state.api_base.rstrip('/') != api_base.rstrip('/'):
        return None
    return state


def client_from_store(api_base: Optional[str] = None,
                      store: Optional[AuthStore] = None) -> Optional[CdifitClient]:
    store = store or AuthStore()
    state = store.load()
    if state is None:
        return None
    if api_base and state.api_base.rstrip('/') != api_base.rstrip('/'):
        return None
    return CdifitClient(api_base or state.api_base, auth=state, store=store)
