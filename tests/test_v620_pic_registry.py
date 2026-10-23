"""v6.2.0: pic 中央注册表 + 浏览器登录 (本地假 Cdifit 服务)。"""

import json
import os
import re
import subprocess
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from codecin import pic
from codecin.packaging.store import PackageStore
from codecin.pic_api import ApiError, AuthState, AuthStore, CdifitClient
from codecin.pic_login import LoginServer
from codecin.pic_registry import Registry

pytestmark = pytest.mark.skipif(
    __import__('shutil').which('git') is None, reason='需要 git 命令')


class FakeCdifit:
    """只实现 pic 用到的 Cdifit 端点。"""

    def __init__(self):
        self.files = {}
        self.folders = {'cloudreve://my'}
        self.access = 'access-token'
        self.refresh = 'refresh-token'
        self.user = {'id': 'u1', 'email': 'user@example.com',
                     'nickname': 'Tester'}
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), self._handler())
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    @property
    def base(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'fake-cdifit/1'

            def log_message(self, *args):
                return

            def _json(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b'{}'
                return json.loads(raw.decode() or '{}')

            def _auth(self):
                return self.headers.get('Authorization') == (
                    'Bearer ' + outer.access)

            def _uri(self):
                query = urllib.parse.urlsplit(self.path).query
                return urllib.parse.parse_qs(query).get('uri', [''])[0]

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == '/download':
                    uri = self._uri()
                    data = outer.files.get(uri)
                    if data is None:
                        self._json(404, {'code': 404, 'msg': 'not found'})
                        return
                    raw = data.encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if path == '/api/v4/file/info':
                    uri = self._uri()
                    if uri in outer.files:
                        self._json(200, {'code': 0, 'data': {
                            'type': 0, 'id': 'id', 'name':
                            uri.rsplit('/', 1)[-1], 'path': uri,
                            'size': len(outer.files[uri])}})
                    elif uri in outer.folders:
                        self._json(200, {'code': 0, 'data': {
                            'type': 1, 'id': 'dir', 'path': uri}})
                    else:
                        self._json(404, {'code': 404, 'msg': 'not found'})
                    return
                if path == '/api/v4/file':
                    if not self._auth():
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    uri = self._uri()
                    if uri not in outer.folders:
                        self._json(404, {'code': 404, 'msg': 'no folder'})
                        return
                    prefix = uri.rstrip('/') + '/'
                    files = []
                    for name in sorted(outer.files):
                        if name.startswith(prefix) and '/' not in name[
                                len(prefix):]:
                            files.append({'type': 0, 'id': name,
                                          'name': name[len(prefix):],
                                          'path': name,
                                          'size': len(outer.files[name])})
                    self._json(200, {'code': 0, 'data': {'files': files,
                                                         'pagination': {}}})
                    return
                self._json(404, {'code': 404, 'msg': 'not found'})

            def do_PUT(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == '/api/v4/file':
                    if not self._auth():
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    body = self._body()
                    outer.folders.add(body['uri'])
                    self._json(200, {'code': 0, 'data': {'type': 1}})
                    return
                if path == '/api/v4/session/token/refresh':
                    body = self._body()
                    if body.get('refresh_token') != outer.refresh:
                        self._json(401, {'code': 401, 'msg': 'bad refresh'})
                        return
                    self._json(200, {'code': 0, 'data': {
                        'access_token': outer.access,
                        'refresh_token': outer.refresh,
                        'access_expires': '2099-01-01T00:00:00+00:00',
                        'refresh_expires': '2099-01-01T00:00:00+00:00'}})
                    return
                self._json(404, {'code': 404, 'msg': 'not found'})

            def do_POST(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == '/api/v4/session/token':
                    body = self._body()
                    if body.get('email') == outer.user['email'] \
                            and body.get('password') == 'secret':
                        self._json(200, {'code': 0, 'data': {
                            'user': outer.user,
                            'token': {
                                'access_token': outer.access,
                                'refresh_token': outer.refresh,
                                'access_expires':
                                    '2099-01-01T00:00:00+00:00',
                                'refresh_expires':
                                    '2099-01-01T00:00:00+00:00'}}})
                    else:
                        self._json(200, {'code': 40001,
                                         'msg': 'invalid credentials'})
                    return
                if path == '/api/v4/session/oauth/userinfo':
                    if not self._auth():
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    self._json(200, {'sub': 'u1', 'name': 'Tester',
                                     'email': outer.user['email']})
                    return
                if path == '/api/v4/file/create':
                    if not self._auth():
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    body = self._body()
                    outer.files[body['uri']] = body['content']
                    self._json(200, {'code': 0, 'data': {'path': body['uri']}})
                    return
                if path == '/api/v4/file/url':
                    if not self._auth():
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    body = self._body()
                    uris = body.get('uris') or []
                    urls = [{'url': outer.base + '/download?uri=' +
                             urllib.parse.quote(uri, safe='')}
                            for uri in uris]
                    self._json(200, {'code': 0, 'data': {'urls': urls}})
                    return
                self._json(404, {'code': 404, 'msg': 'not found'})

            def do_DELETE(self):
                if not self._auth():
                    self._json(401, {'code': 401, 'msg': 'unauthorized'})
                    return
                body = self._body()
                for uri in body.get('uris') or []:
                    outer.files.pop(uri, None)
                self._json(200, {'code': 0, 'data': {}})

        return Handler


@pytest.fixture()
def fake_api():
    server = FakeCdifit().start()
    try:
        yield server
    finally:
        server.stop()


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True,
                   capture_output=True, text=True, encoding='utf-8')


def _make_repo(path, version='1.0.0'):
    assert pic.main(['init', 'hello', '--path', path, '--version', version]) == 0
    with open(os.path.join(path, 'src', 'hello.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function hello() -> int { return 42 }\n')
    _git(path, 'init', '--quiet')
    _git(path, 'config', 'user.email', 'test@example.com')
    _git(path, 'config', 'user.name', 'Test')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-m', 'init', '--quiet')
    _git(path, 'tag', '-a', f'v{version}', '-m', f'v{version}')
    return path


def _auth(monkeypatch, fake_api, workdir):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    monkeypatch.setenv('PIC_API_BASE', fake_api.base)
    monkeypatch.delenv('PIC_OFFLINE', raising=False)
    assert pic.main(['login', '--email', 'user@example.com',
                     '--password', 'secret']) == 0
    return AuthStore().load()


def test_login_and_whoami(fake_api, workdir, monkeypatch, capsys):
    state = _auth(monkeypatch, fake_api, workdir)
    assert state is not None and state.access_token == fake_api.access
    assert pic.main(['whoami']) == 0
    out = capsys.readouterr().out
    assert 'user@example.com' in out
    assert pic.main(['logout']) == 0
    assert AuthStore().load() is None


def test_publish_and_install_through_central_registry(
        fake_api, workdir, monkeypatch):
    _auth(monkeypatch, fake_api, workdir)
    repo = _make_repo(os.path.join(workdir, 'hello'))
    assert pic.main(['publish', '--manifest', repo, '--repo', repo,
                     '--no-tag']) == 0
    registry = Registry(CdifitClient(fake_api.base,
                                     auth=AuthStore().load(),
                                     store=AuthStore()))
    record = registry.get_package('hello')
    assert record is not None
    assert record['repo'] == repo
    assert '1.0.0' in record['versions']
    assert record['versions']['1.0.0']['source_sha256']

    assert pic.main(['install', 'hello']) == 0
    pkg = PackageStore().get('hello')
    assert pkg is not None and pkg.version == '1.0.0'
    assert pkg.rev


def test_tampered_registry_is_rejected(fake_api, workdir, monkeypatch):
    _auth(monkeypatch, fake_api, workdir)
    repo = _make_repo(os.path.join(workdir, 'hello'))
    assert pic.main(['publish', '--manifest', repo, '--repo', repo,
                     '--no-tag']) == 0
    client = CdifitClient(fake_api.base, auth=AuthStore().load(),
                          store=AuthStore())
    registry = Registry(client)
    record = registry.get_package('hello')
    record['versions']['1.0.0']['source_sha256'] = '0' * 64
    client.write_json(registry.package_uri('hello'), record)

    assert pic.main(['install', 'hello', '--force']) == 1
    assert PackageStore().get('hello') is None


def test_browser_login_server(fake_api, workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    client = CdifitClient(fake_api.base, store=AuthStore())
    server = LoginServer(client, timeout=10)
    url = server.start()
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            page = response.read().decode('utf-8')
            assert 'inline-size: min(92vw, 26rem)' in page
            assert 'display: flex' in page
            csrf = re.search(r'name="pic-csrf" content="([^"]+)"', page).group(1)
        body = json.dumps({'email': 'user@example.com',
                           'password': 'secret'}).encode()
        request = urllib.request.Request(
            url + 'api/login', data=body,
            headers={'Content-Type': 'application/json',
                     'X-PIC-CSRF': csrf}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        assert payload['ok'] is True
        state = server.wait(5)
        assert state.email == 'user@example.com'
    finally:
        server.stop()
