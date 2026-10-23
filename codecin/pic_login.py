"""pic 浏览器登录: 临时本地 Web 服务器。

``pic login`` 在 ``127.0.0.1`` 上启动一个**临时** HTTP 服务, 打开浏览器,
由用户在网页中输入 Cdifit 邮箱/密码 (或 2FA 验证码)。登录成功后服务立即
关闭并把令牌写入 ``$PIC_HOME/auth.json``。

安全边界:

* 只绑定 loopback (127.0.0.1), 随机端口;
* 页面使用同源请求 + 随机 CSRF token, 拒绝跨站 POST;
* 密码只存在于本次请求内存, 不写日志、不写文件;
* 响应带 ``Cache-Control: no-store`` / ``X-Frame-Options: DENY``;
* 页面布局使用相对单位与 flex (`min(92vw, 26rem)`), 不使用绝对定位。
"""

import contextlib
import html
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .pic_api import (
    DEFAULT_API_BASE,
    ApiError,
    AuthState,
    AuthStore,
    CdifitClient,
    TwoFactorRequired,
)

__all__ = ['AuthError', 'LoginServer', 'run_login_flow']


class AuthError(RuntimeError):
    """登录超时/被取消/浏览器流程失败。"""


_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f4f6fb;
  --card: #ffffff;
  --text: #182033;
  --muted: #5b6478;
  --line: #dce2ee;
  --accent: #2563eb;
  --accent-strong: #1d4ed8;
  --danger: #b42318;
  --ok: #067647;
  font-size: 16px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1420;
    --card: #171e2d;
    --text: #e8edf7;
    --muted: #9aa6bd;
    --line: #2b3548;
    --accent: #6f9bff;
    --accent-strong: #8db0ff;
    --danger: #ff8a80;
    --ok: #7ee2b0;
  }
}
* { box-sizing: border-box; }
html, body { min-height: 100%; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: radial-gradient(circle at 20% 10%, rgba(37, 99, 235, .14),
              transparent 45%), var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
               "Microsoft YaHei", sans-serif;
}
.card {
  inline-size: min(92vw, 26rem);
  padding: clamp(1.4rem, 4vw, 2.2rem);
  border: 1px solid var(--line);
  border-radius: 1rem;
  background: var(--card);
  box-shadow: 0 1.25rem 3rem rgba(17, 24, 39, .12);
}
.brand {
  display: flex;
  align-items: center;
  gap: .6rem;
  margin-block-end: .35rem;
  font-weight: 700;
  letter-spacing: .02em;
}
.dot {
  inline-size: .7rem;
  block-size: .7rem;
  border-radius: 50%;
  background: var(--accent);
}
h1 { margin: 0 0 .35rem; font-size: clamp(1.2rem, 3.4vw, 1.45rem); }
p.lead { margin: 0 0 1.25rem; color: var(--muted); font-size: .92rem;
         line-height: 1.55; }
label { display: block; margin: 0 0 .4rem; font-size: .85rem;
        color: var(--muted); }
input {
  inline-size: 100%;
  padding: .72rem .8rem;
  margin-block-end: .9rem;
  border: 1px solid var(--line);
  border-radius: .6rem;
  background: transparent;
  color: var(--text);
  font: inherit;
}
input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
button {
  inline-size: 100%;
  padding: .78rem .9rem;
  border: 0;
  border-radius: .6rem;
  background: var(--accent);
  color: #fff;
  font: inherit;
  font-weight: 650;
  cursor: pointer;
}
button:hover { background: var(--accent-strong); }
button:disabled { opacity: .55; cursor: wait; }
button.secondary {
  margin-block-start: .5rem;
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--line);
}
.status {
  min-height: 1.25rem;
  margin-block-start: .9rem;
  font-size: .85rem;
  line-height: 1.5;
  color: var(--muted);
}
.status.error { color: var(--danger); }
.status.ok { color: var(--ok); }
.hidden { display: none; }
.foot {
  margin-block-start: 1.2rem;
  padding-block-start: .9rem;
  border-block-start: 1px solid var(--line);
  font-size: .78rem;
  line-height: 1.5;
  color: var(--muted);
}
code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
"""

_SCRIPT = r"""
const csrf = document.querySelector('meta[name="pic-csrf"]').content;
const $ = (id) => document.getElementById(id);
let twoFactorSession = null;

function setStatus(text, kind) {
  const el = $('status');
  el.textContent = text || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-PIC-CSRF': csrf
    },
    body: JSON.stringify(body)
  });
  let data = {};
  try { data = await response.json(); } catch (_) { data = {}; }
  return { response, data };
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('login-button');
  button.disabled = true;
  setStatus('正在连接 Cdifit…');
  try {
    const { response, data } = await post('./api/login', {
      email: $('email').value.trim(),
      password: $('password').value
    });
    if (data.need_2fa) {
      twoFactorSession = data.session;
      $('login-panel').classList.add('hidden');
      $('twofa-panel').classList.remove('hidden');
      setStatus('该账号启用了两步验证，请输入 6 位验证码。');
      $('twofa-code').focus();
      return;
    }
    if (!response.ok || !data.ok) {
      setStatus(data.error || '登录失败，请检查邮箱和密码。', 'error');
      return;
    }
    setStatus('登录成功，正在跳转…', 'ok');
    location.href = './done';
  } catch (error) {
    setStatus('网络错误: ' + error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

$('twofa-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('twofa-button');
  button.disabled = true;
  setStatus('正在验证…');
  try {
    const { response, data } = await post('./api/2fa', {
      session: twoFactorSession,
      code: $('twofa-code').value.trim()
    });
    if (!response.ok || !data.ok) {
      setStatus(data.error || '验证码不正确。', 'error');
      return;
    }
    setStatus('登录成功，正在跳转…', 'ok');
    location.href = './done';
  } catch (error) {
    setStatus('网络错误: ' + error.message, 'error');
  } finally {
    button.disabled = false;
  }
});
"""

_LOGIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pic-csrf" content="__CSRF__">
<title>pic 登录 · Code CIN</title>
<style>__STYLE__</style>
</head>
<body>
<main class="card">
  <div class="brand"><span class="dot" aria-hidden="true"></span>
    <span>Code CIN · pic</span></div>
  <h1>登录 Cdifit</h1>
  <p class="lead">首次使用 pic 需要登录。登录信息只发送到本机临时服务，
    密码不会写入磁盘。</p>

  <section id="login-panel">
    <form id="login-form" autocomplete="on">
      <label for="email">邮箱</label>
      <input id="email" name="email" type="email" autocomplete="username"
             required placeholder="you@example.com">
      <label for="password">密码</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required placeholder="密码">
      <button id="login-button" type="submit">登录</button>
    </form>
  </section>

  <section id="twofa-panel" class="hidden">
    <form id="twofa-form" autocomplete="one-time-code">
      <label for="twofa-code">两步验证码</label>
      <input id="twofa-code" name="code" type="text" inputmode="numeric"
             pattern="[0-9]*" maxlength="8" required placeholder="123456">
      <button id="twofa-button" type="submit">验证并登录</button>
    </form>
  </section>

  <div id="status" class="status" role="status" aria-live="polite"></div>
  <div class="foot">临时服务地址仅在本机可用，登录完成后自动关闭。<br>
    遇到问题请回到终端查看 <code>pic login</code> 的提示。</div>
</main>
<script>__SCRIPT__</script>
</body>
</html>
"""

_DONE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pic 登录成功</title>
<style>__STYLE__</style>
</head>
<body>
<main class="card">
  <div class="brand"><span class="dot" aria-hidden="true"></span>
    <span>Code CIN · pic</span></div>
  <h1>登录成功</h1>
  <p class="lead">已登录为 <strong>__USER__</strong>。可以关闭此页面，
    回到终端继续使用 pic。</p>
  <div class="status ok">临时登录服务即将自动关闭。</div>
</main>
</body>
</html>
"""


def _render_login(csrf: str) -> str:
    return (_LOGIN_HTML
            .replace('__STYLE__', _STYLE)
            .replace('__SCRIPT__', _SCRIPT)
            .replace('__CSRF__', html.escape(csrf, quote=True)))


def _render_done(user: str) -> str:
    return (_DONE_HTML
            .replace('__STYLE__', _STYLE)
            .replace('__USER__', html.escape(user or 'Cdifit 用户')))


class _LoginHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, owner: 'LoginServer'):
        super().__init__(address, handler)
        self.owner = owner


class _Handler(BaseHTTPRequestHandler):
    server_version = 'pic-login/1'

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return  # 不向终端打印访问日志 (避免泄露 query/state)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        self._send_bytes(status, json.dumps(data, ensure_ascii=False).encode(),
                         'application/json; charset=utf-8')

    def _send_html(self, status: int, text: str) -> None:
        self._send_bytes(status, text.encode('utf-8'),
                         'text/html; charset=utf-8')

    def _origin_ok(self) -> bool:
        origin = self.headers.get('Origin')
        if not origin:
            return True
        return origin == self.server.owner.origin

    def _csrf_ok(self) -> bool:
        return self.headers.get('X-PIC-CSRF') == self.server.owner.csrf

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        owner = self.server.owner
        if path in ('/', '/index.html', '/login'):
            self._send_html(200, _render_login(owner.csrf))
        elif path in ('/done', '/success'):
            user = owner.auth.display_name if owner.auth else ''
            self._send_html(200, _render_done(user))
        elif path == '/api/status':
            if owner.auth:
                self._send_json(200, {'authenticated': True,
                                      'user': owner.auth.user})
            else:
                self._send_json(200, {'authenticated': False})
        else:
            self._send_json(404, {'ok': False, 'error': 'not found'})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        owner = self.server.owner
        if not self._origin_ok():
            self._send_json(403, {'ok': False, 'error': 'invalid origin'})
            return
        if not self._csrf_ok():
            self._send_json(403, {'ok': False, 'error': 'invalid csrf token'})
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {'ok': False, 'error': 'invalid json'})
            return
        if not isinstance(body, dict):
            self._send_json(400, {'ok': False, 'error': 'invalid body'})
            return

        try:
            if path == '/api/login':
                email = str(body.get('email') or '').strip()
                password = str(body.get('password') or '')
                if not email or not password:
                    self._send_json(400, {'ok': False,
                                          'error': '邮箱和密码不能为空'})
                    return
                state = owner.client.login(email, password)
                owner.auth = state
                owner.event.set()
                self._send_json(200, {'ok': True, 'user': state.user})
                return
            if path == '/api/2fa':
                session = str(body.get('session') or '')
                code = str(body.get('code') or '').strip()
                if not session or not code:
                    self._send_json(400, {'ok': False,
                                          'error': '验证码不能为空'})
                    return
                state = owner.client.finish_2fa(session, code)
                owner.auth = state
                owner.event.set()
                self._send_json(200, {'ok': True, 'user': state.user})
                return
        except TwoFactorRequired as e:
            self._send_json(200, {'ok': False, 'need_2fa': True,
                                  'session': e.session})
            return
        except ApiError as e:
            self._send_json(200, {'ok': False, 'error': str(e)})
            return
        except Exception as e:  # noqa: BLE001 - 页面只显示可读错误
            self._send_json(500, {'ok': False, 'error': str(e)})
            return
        self._send_json(404, {'ok': False, 'error': 'not found'})


class LoginServer:
    """临时登录 Web 服务 (只监听 127.0.0.1)。"""

    def __init__(self, client: CdifitClient, timeout: float = 300.0):
        self.client = client
        self.timeout = timeout
        self.csrf = secrets.token_urlsafe(32)
        self.event = threading.Event()
        self.auth: Optional[AuthState] = None
        self.httpd: Optional[_LoginHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        if not self.httpd:
            raise RuntimeError('login server not started')
        return int(self.httpd.server_address[1])

    @property
    def origin(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    @property
    def url(self) -> str:
        return self.origin + '/'

    def start(self) -> str:
        self.httpd = _LoginHTTPServer(('127.0.0.1', 0), _Handler, self)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={'poll_interval': 0.1},
                                       daemon=True)
        self.thread.start()
        return self.url

    def wait(self, timeout: Optional[float] = None) -> AuthState:
        if not self.event.wait(self.timeout if timeout is None else timeout):
            raise AuthError(
                f'登录超时 ({self.timeout:.0f}s)。请重新运行 `pic login`。')
        if self.auth is None:
            raise AuthError('登录流程结束但没有拿到令牌')
        return self.auth

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None


def _print_url(url: str) -> None:
    print('请在浏览器中完成登录:')
    print(f'  {url}')
    print('如果浏览器没有自动打开, 请手动复制上面的地址。')


def run_login_flow(api_base: str = DEFAULT_API_BASE, *,
                   store: Optional[AuthStore] = None,
                   timeout: float = 300.0,
                   open_browser: bool = True) -> AuthState:
    """启动临时 Web 服务并等待浏览器登录。"""
    store = store or AuthStore()
    client = CdifitClient(api_base, store=store)
    server = LoginServer(client, timeout=timeout)
    url = server.start()
    try:
        _print_url(url)
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        state = server.wait(timeout)
        # 给浏览器留出加载 /done 的时间, 随后关闭临时服务
        time.sleep(0.4)
        return state
    finally:
        server.stop()
