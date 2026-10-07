import hashlib
import json
import re
import sqlite3
import time

import pytest

from sustech_dashboard.entry import create_app, backend, http_server

HOST = 'dashboard.test'
PREFIX = '/spaces/' + 'a' * 24
TOKEN = 'a' * 43


@pytest.fixture
def entrance(tmp_path):
    config = tmp_path/'routes.json'
    data = {'host': HOST, 'owner_name': 'owner', 'routes': [
        {'prefix': '/campus', 'port': 18771, 'username': 'owner'},
        {'prefix': PREFIX, 'port': 18900, 'username': 'member', 'invitation': {
            'digest': hashlib.sha256(TOKEN.encode()).hexdigest(), 'expires': time.time()+3600}}]}
    config.write_text(json.dumps(data))
    calls = []
    activated = [False]

    def exchange(route, host, endpoint, fields, peer):
        calls.append((route['prefix'], endpoint, fields))
        if endpoint == '/invite':
            if activated[0] or fields['invitation'] != TOKEN:
                return 400, []
            activated[0] = True
            return 303, []
        if fields['password'] != 'fixture-password' or (route['prefix'] != '/campus' and not activated[0]):
            return 401, []
        return 303, ['__Secure-fixture=session; Secure; HttpOnly; Path='+route['prefix']+'/']

    app = create_app(tmp_path/'state', config, exchange)
    return app.test_client(), calls, config, tmp_path/'state'


def get(client, path='/', **kwargs):
    return client.get(path, base_url='https://'+HOST+'/app/', **kwargs)


def post(client, **data):
    page = get(client, '/?login=1')
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    return client.post('/', base_url='https://'+HOST+'/app/', headers={'Origin': 'https://'+HOST},
                       data={'csrf': csrf, 'password': 'fixture-password', **data})


def test_owner_login_remember_and_expired_session_escape(entrance):
    client, calls, _, _ = entrance
    page = get(client)
    assert page.status_code == 200 and '进入面板' in page.text
    assert page.headers['Referrer-Policy'] == 'same-origin'
    result = post(client, username='owner', remember='on')
    assert result.status_code == 303 and result.location == '/campus/'
    assert calls[-1][0] == '/campus' and calls[-1][2]['username'] == 'owner'
    assert any('Path=/campus/' in value for value in result.headers.getlist('Set-Cookie'))
    assert get(client).location == '/campus/'
    assert get(client, '/?login=1').status_code == 200


def test_invited_account_automatically_enters_and_routes_one_instance(entrance):
    client, calls, _, _ = entrance
    result = post(client, action='activate', invitation=f'https://{HOST}/app/#invite={TOKEN}', username='friend')
    assert result.status_code == 303 and result.location == PREFIX+'/'
    assert [c[:2] for c in calls] == [(PREFIX, '/invite'), (PREFIX, '/auth/login')]
    assert post(client, username='FRIEND').location == PREFIX+'/'
    assert calls[-1][2]['username'] == 'member'
    before = len(calls)
    assert post(client, username='unknown').status_code == 401
    assert len(calls) == before  # Never broadcast a password to multiple accounts.
    assert post(client, username='friend', password='wrong').status_code == 401
    assert post(client, action='activate', invitation=TOKEN, username='other').status_code == 409


def test_csrf_host_revocation_and_external_redirect(entrance):
    client, calls, config, _ = entrance
    assert client.post('/', base_url='https://'+HOST, data={}).status_code == 403
    page = get(client, '/?login=1')
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    assert client.post('/', base_url='https://'+HOST+'/app/', headers={'Origin': 'null'},
                       data={'csrf': csrf, 'username': 'owner', 'password': 'fixture-password'}).status_code == 403
    assert client.get('/', base_url='https://evil.test').status_code == 400
    assert client.get('/', base_url='http://'+HOST).status_code == 400
    assert post(client, action='activate', username='friend', invitation='https://evil.test/#invite='+TOKEN).status_code == 400
    assert not calls
    assert post(client, username='owner', next='https://evil.test').location == '/campus/'
    data = json.loads(config.read_text())
    data['routes'] = data['routes'][1:]
    config.write_text(json.dumps(data))
    assert get(client).status_code == 200
    before = len(calls)
    assert post(client, username='owner').status_code == 401
    assert len(calls) == before


@pytest.mark.parametrize('failure', ['lost_response', 'server_error'])
def test_lost_activation_response_keeps_login_route(tmp_path, failure):
    config = tmp_path/'routes.json'
    config.write_text(json.dumps({'host': HOST, 'owner_name': 'owner', 'routes': [
        {'prefix': PREFIX, 'port': 18900, 'username': 'member', 'invitation': {
            'digest': hashlib.sha256(TOKEN.encode()).hexdigest(), 'expires': time.time()+60}}]}))
    def exchange(route, host, endpoint, fields, peer):
        if endpoint == '/invite':
            if failure == 'lost_response':
                raise OSError('Lost response after activation')
            return 500, []
        return 303, []
    client = create_app(tmp_path/'state', config, exchange).test_client()
    assert post(client, action='activate', username='friend', invitation=TOKEN).status_code == 503
    assert post(client, username='friend').location == PREFIX+'/'


def test_activation_retry_after_connection_failed_before_redemption(entrance):
    client, _, config, root = entrance
    fail = [True]
    def exchange(route, host, endpoint, fields, peer):
        if fail[0]:
            fail[0] = False
            raise OSError('Connection failed before redeeming')
        return 303, []
    client = create_app(root, config, exchange).test_client()
    assert post(client, action='activate', username='friend', invitation=TOKEN).status_code == 503
    assert post(client, action='activate', username='friend', invitation=TOKEN).location == PREFIX+'/'


def test_actual_backend_form_exchange_preserves_private_cookie():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from urllib.parse import parse_qs
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<input name="csrf" value="fixture">')
        def do_POST(self):
            fields = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
            seen.append((self.path, dict(self.headers), fields))
            self.send_response(303)
            self.send_header('Set-Cookie', '__Secure-fixture=value; Secure; HttpOnly; Path='+PREFIX+'/')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, cookies = backend({'port': server.server_port, 'prefix': PREFIX}, HOST, '/auth/login',
                               {'username': 'member', 'password': 'fixture-password'}, '192.0.2.1')
        assert code == 303 and 'Path='+PREFIX+'/' in cookies[0]
        assert seen[0][1]['Origin'] == 'https://'+HOST
        assert seen[0][1]['X-Forwarded-Prefix'] == PREFIX
        assert seen[0][2]['csrf'] == ['fixture']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_waitress_preserves_nginx_https_headers(entrance):
    import http.client
    import threading
    client, _, _, _ = entrance
    server = http_server(client.application, port=0)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection('127.0.0.1', server.effective_port, timeout=5)
    try:
        connection.request('GET', '/?login=1', headers={'Host': HOST, 'X-Forwarded-Proto': 'https',
            'X-Forwarded-Prefix': '/app', 'X-Forwarded-Host': 'forged.test'})
        response = connection.getresponse()
        assert response.status == 200 and '进入面板' in response.read().decode()
    finally:
        connection.close()
        server.close()
        thread.join(5)


def test_unified_activation_against_real_private_instance(monkeypatch, tmp_path):
    import http.client
    import threading
    from werkzeug.serving import make_server
    from sustech_dashboard import app, runtime, authentication, hosted, web_auth, instance_routes, paths
    from sustech_dashboard.hosted import Space
    root = tmp_path/'private'
    root.mkdir()
    monkeypatch.setenv('SUSTECH_EXECUTION_MODE', 'hosted')
    monkeypatch.setenv('SUSTECH_SPACE_ID', 'a'*24)
    monkeypatch.setenv('SUSTECH_PROXY_PREFIX', PREFIX)
    monkeypatch.setenv('SUSTECH_PUBLIC_HOST', HOST)
    monkeypatch.setattr(app, 'CLOUD', True)
    for module in (runtime, hosted, web_auth, instance_routes, paths):
        monkeypatch.setattr(module, 'DATA_ROOT', root)
    monkeypatch.setattr(authentication, 'load_owner_credentials', lambda: False)
    space = Space(root)
    token = space.invite()
    server = make_server('127.0.0.1', 0, app.create_app(runtime.Runtime()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path/'routes.json'
    config.write_text(json.dumps({'host': HOST, 'owner_name': 'owner', 'routes': [
        {'prefix': PREFIX, 'port': 18900, 'username': 'member', 'invitation': {
            'digest': hashlib.sha256(token.encode()).hexdigest(), 'expires': time.time()+60}}]}))
    def exchange(route, host, endpoint, fields, peer):
        return backend({**route, 'port': server.server_port}, host, endpoint, fields, peer)
    client = create_app(tmp_path/'entry', config, exchange).test_client()
    try:
        result = post(client, action='activate', username='friend', invitation=token, remember='on')
        assert result.status_code == 303 and result.location == PREFIX+'/'
        cookie = client.get_cookie('__Secure-campus_session_'+'a'*24, domain=HOST, path=PREFIX+'/')
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/instance', headers={'Host': HOST, 'X-Forwarded-Proto': 'https',
                'X-Forwarded-Prefix': PREFIX, 'Cookie': cookie.key+'='+cookie.value})
            response = connection.getresponse()
            assert response.status == 200 and json.loads(response.read())['configured'] is False
        finally:
            connection.close()
        assert post(client, username='friend').status_code == 303
        with space.db() as db:
            assert space.get(db, 'invitation') is None and space.get(db, 'cas') is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
