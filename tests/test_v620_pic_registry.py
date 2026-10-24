"""v6.2.0: pic 中央注册/分发服务器 + 浏览器登录。

使用两个本地假服务:
* FakeCdifit  —— 只实现登录/userinfo (Cdifit 认证);
* FakeRegistry —— 实现 package.codecin.org.cn 的元数据/制品 API。
"""

import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from codecin import pic
from codecin.packaging.store import PackageStore
from codecin.pic_api import AuthState, AuthStore, CdifitClient
from codecin.pic_login import LoginServer
from codecin.pic_registry import RegistryClient

pytestmark = pytest.mark.skipif(
    __import__('shutil').which('git') is None, reason='需要 git 命令')


class _BaseServer:
    def __init__(self):
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


class FakeCdifit(_BaseServer):
    access = 'access-token'
    refresh = 'refresh-token'
    user = {'id': 'u1', 'email': 'user@example.com', 'nickname': 'Tester'}

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
                    if self.headers.get('Authorization') != (
                            'Bearer ' + outer.access):
                        self._json(401, {'code': 401, 'msg': 'unauthorized'})
                        return
                    self._json(200, {'sub': 'u1', 'name': 'Tester',
                                     'email': outer.user['email']})
                    return
                self._json(404, {'code': 404, 'msg': 'not found'})

            def do_GET(self):
                if urllib.parse.urlsplit(self.path).path != (
                        '/api/v4/session/oauth/userinfo'):
                    self._json(404, {'code': 404, 'msg': 'not found'})
                    return
                if self.headers.get('Authorization') != (
                        'Bearer ' + outer.access):
                    self._json(200, {'code': 401, 'msg': 'Login required'})
                    return
                self._json(200, {'code': 0, 'data': {
                    'sub': 'u1', 'name': 'Tester',
                    'email': outer.user['email']}})

            def do_PUT(self):
                if urllib.parse.urlsplit(self.path).path == (
                        '/api/v4/session/token/refresh'):
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

            def do_DELETE(self):
                if self.headers.get('Authorization') != (
                        'Bearer ' + outer.access):
                    self._json(401, {'code': 401, 'msg': 'unauthorized'})
                    return
                self._json(200, {'code': 0, 'data': {}})

        return Handler


class FakeRegistry(_BaseServer):
    def __init__(self):
        self.packages = {}
        self.artifacts = {}
        super().__init__()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'fake-registry/1'

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
                    'Bearer ' + FakeCdifit.access)

            def _parts(self):
                return [urllib.parse.unquote(p) for p in
                        urllib.parse.urlsplit(self.path).path.split('/')
                        if p]

            def do_GET(self):
                parts = self._parts()
                if parts == ['api', 'v1', 'packages']:
                    query = urllib.parse.parse_qs(
                        urllib.parse.urlsplit(self.path).query).get('q', [''])[0]
                    items = []
                    for name, record in sorted(outer.packages.items()):
                        if query and query.lower() not in name.lower() and \
                                query.lower() not in record['repo'].lower():
                            continue
                        versions = record['versions']
                        latest = max(versions, key=lambda v: tuple(
                            int(x) for x in v.split('-')[0].split('.')))
                        items.append({'name': name, 'repo': record['repo'],
                                      'latest': latest,
                                      'versions': len(versions)})
                    self._json(200, {'ok': True, 'data': {
                        'packages': items, 'count': len(items)}})
                    return
                if len(parts) == 4 and parts[:3] == ['api', 'v1', 'packages']:
                    name = parts[3]
                    record = outer.packages.get(name)
                    if record is None:
                        self._json(404, {'ok': False, 'error': {
                            'code': 'not_found', 'message': 'package not found'}})
                        return
                    self._json(200, {'ok': True, 'data': record})
                    return
                if len(parts) == 6 and parts[-1] == 'artifact':
                    name, version = parts[3], parts[4]
                    data = outer.artifacts.get((name, version))
                    if data is None:
                        self._json(404, {'ok': False, 'error': {
                            'code': 'artifact_not_found',
                            'message': 'artifact not uploaded'}})
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if len(parts) == 5 and parts[:3] == ['api', 'v1', 'packages']:
                    name, version = parts[3], parts[4]
                    record = outer.packages.get(name)
                    if record is None or version not in record['versions']:
                        self._json(404, {'ok': False, 'error': {
                            'code': 'not_found', 'message': 'version not found'}})
                        return
                    self._json(200, {'ok': True, 'data': {
                        'name': name, 'repo': record['repo'],
                        'version': record['versions'][version]}})
                    return
                self._json(404, {'ok': False, 'error': {
                    'code': 'not_found', 'message': 'not found'}})

            def do_PUT(self):
                if not self._auth():
                    self._json(401, {'ok': False, 'error': {
                        'code': 'unauthorized', 'message': 'unauthorized'}})
                    return
                parts = self._parts()
                if len(parts) != 5:
                    self._json(404, {'ok': False, 'error': {
                        'code': 'not_found', 'message': 'not found'}})
                    return
                name, version = parts[3], parts[4]
                body = self._body()
                record = outer.packages.setdefault(name, {
                    'schema': 'pic.package/v1', 'name': name,
                    'repo': body['repo'], 'versions': {}})
                existing = record['versions'].get(version)
                if existing is not None:
                    if existing.get('source_sha256') != body.get(
                            'source_sha256'):
                        self._json(409, {'ok': False, 'error': {
                            'code': 'version_exists',
                            'message': 'version exists'}})
                        return
                    self._json(200, {'ok': True, 'data': {
                        'name': name, 'version': version,
                        'created': False,
                        'publisher': {'id': 'u1',
                                      'email': FakeCdifit.user['email']}}})
                    return
                body['publisher'] = {'id': 'u1',
                                     'email': FakeCdifit.user['email']}
                record['versions'][version] = body
                self._json(201, {'ok': True, 'data': {
                    'name': name, 'version': version, 'created': True,
                    'publisher': body['publisher']}})

            def do_POST(self):
                if not self._auth():
                    self._json(401, {'ok': False, 'error': {
                        'code': 'unauthorized', 'message': 'unauthorized'}})
                    return
                parts = self._parts()
                if len(parts) == 6 and parts[-1] == 'artifact':
                    name, version = parts[3], parts[4]
                    length = int(self.headers.get('Content-Length') or 0)
                    data = self.rfile.read(length)
                    digest = hashlib.sha256(data).hexdigest()
                    expected = urllib.parse.parse_qs(
                        urllib.parse.urlsplit(self.path).query).get(
                            'sha256', [''])[0]
                    if expected and expected != digest:
                        self._json(400, {'ok': False, 'error': {
                            'code': 'checksum_mismatch',
                            'message': 'bad checksum'}})
                        return
                    record = outer.packages.get(name)
                    if record is None or version not in record['versions']:
                        self._json(404, {'ok': False, 'error': {
                            'code': 'not_found', 'message': 'version not found'}})
                        return
                    outer.artifacts[(name, version)] = data
                    entry = record['versions'][version]
                    entry['archive_sha256'] = digest
                    entry['archive_size'] = len(data)
                    entry['artifact_url'] = (
                        outer.base + f'/api/v1/packages/{name}/{version}/artifact')
                    self._json(200, {'ok': True, 'data': {
                        'name': name, 'version': version, 'sha256': digest,
                        'size': len(data), 'url': entry['artifact_url']}})
                    return
                if len(parts) == 6 and parts[-1] == 'yank':
                    name, version = parts[3], parts[4]
                    record = outer.packages.get(name)
                    if record is None or version not in record['versions']:
                        self._json(404, {'ok': False, 'error': {
                            'code': 'not_found', 'message': 'not found'}})
                        return
                    record['versions'][version]['yanked'] = True
                    self._json(200, {'ok': True, 'data': {
                        'name': name, 'version': version, 'yanked': True}})
                    return
                self._json(404, {'ok': False, 'error': {
                    'code': 'not_found', 'message': 'not found'}})

        return Handler


@pytest.fixture()
def fake_services():
    auth = FakeCdifit().start()
    registry = FakeRegistry().start()
    try:
        yield auth, registry
    finally:
        auth.stop()
        registry.stop()


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


def _auth(monkeypatch, auth, registry, workdir):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    monkeypatch.setenv('PIC_API_BASE', auth.base)
    monkeypatch.setenv('PIC_REGISTRY_URL', registry.base)
    monkeypatch.delenv('PIC_OFFLINE', raising=False)
    assert pic.main(['login', '--email', 'user@example.com',
                     '--password', 'secret']) == 0
    return AuthStore().load()


def test_login_and_whoami(fake_services, workdir, monkeypatch, capsys):
    auth, registry = fake_services
    state = _auth(monkeypatch, auth, registry, workdir)
    assert state is not None and state.access_token == auth.access
    assert pic.main(['whoami']) == 0
    out = capsys.readouterr().out
    assert 'user@example.com' in out
    assert pic.main(['logout']) == 0
    assert AuthStore().load() is None


def test_publish_and_install_through_central_registry(
        fake_services, workdir, monkeypatch, capsys):
    auth, registry = fake_services
    _auth(monkeypatch, auth, registry, workdir)
    repo = _make_repo(os.path.join(workdir, 'hello'))
    assert pic.main(['publish', '--manifest', repo, '--repo', repo,
                     '--no-tag']) == 0
    out = capsys.readouterr().out
    assert '[1/8]' in out and '[8/8]' in out
    assert '[7/8]' in out
    assert 'SHA-256' in out
    client = RegistryClient(registry.base, token=auth.access)
    record = client.get_package('hello')
    assert record is not None
    assert record['repo'] == repo
    assert '1.0.0' in record['versions']
    assert record['versions']['1.0.0']['source_sha256']
    assert record['versions']['1.0.0']['artifact_url']

    assert pic.main(['install', 'hello']) == 0
    pkg = PackageStore().get('hello')
    assert pkg is not None and pkg.version == '1.0.0'
    assert pic.main(['search', 'hello']) == 0
    assert pic.main(['registry', 'list']) == 0


def test_tampered_registry_is_rejected(fake_services, workdir, monkeypatch):
    auth, registry = fake_services
    _auth(monkeypatch, auth, registry, workdir)
    repo = _make_repo(os.path.join(workdir, 'hello'))
    assert pic.main(['publish', '--manifest', repo, '--repo', repo,
                     '--no-tag']) == 0
    registry.packages['hello']['versions']['1.0.0']['source_sha256'] = '0' * 64
    assert pic.main(['install', 'hello', '--force']) == 1
    assert PackageStore().get('hello') is None


def test_browser_login_server(fake_services, workdir, monkeypatch):
    auth, _registry = fake_services
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    client = CdifitClient(auth.base, store=AuthStore())
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
        # 缺少 CSRF 的跨站 POST 必须被拒绝
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(
                url + 'api/login', data=body,
                headers={'Content-Type': 'application/json'}, method='POST'),
                timeout=5)
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


def test_first_use_gate_runs_login(workdir, monkeypatch):
    home = os.path.join(workdir, 'home')
    monkeypatch.setenv('PIC_HOME', home)
    monkeypatch.setenv('PIC_API_BASE', 'https://example.invalid')
    monkeypatch.setenv('PIC_REGISTRY_URL', 'https://example.invalid')
    monkeypatch.delenv('PIC_OFFLINE', raising=False)

    def fake_login(api_base, **kwargs):
        state = AuthState(
            api_base=api_base,
            access_token='token',
            refresh_token='refresh',
            access_expires='2099-01-01T00:00:00+00:00',
            user={'id': 'u1', 'email': 'gate@example.com',
                  'nickname': 'Gate'},
        )
        AuthStore().save(state)
        return state

    monkeypatch.setattr(pic, 'run_login_flow', fake_login)
    target = os.path.join(workdir, 'first')
    assert pic.main(['init', 'first', '--path', target]) == 0
    assert os.path.isfile(os.path.join(target, 'pic.toml'))
    assert AuthStore().load() is not None
