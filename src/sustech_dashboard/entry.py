"""Unified browser entrance; forwards credentials to one registered private instance."""
from collections import OrderedDict
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs

from bs4 import BeautifulSoup
from flask import Flask, abort, redirect, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix


def backend(route, host, endpoint, fields, peer):
    """No caller-controlled host/port/path, cookies or redirects are forwarded."""
    connection = http.client.HTTPConnection('127.0.0.1', route['port'], timeout=10)
    headers = {'Host': host, 'X-Forwarded-Proto': 'https',
               'X-Forwarded-Prefix': route['prefix'], 'X-Forwarded-For': peer}
    try:
        connection.request('GET', endpoint, headers=headers)
        response = connection.getresponse()
        page = response.read(131072)
        if response.status != 200:
            raise OSError('Instance unavailable')
        token = BeautifulSoup(page, 'html.parser').select_one('input[name="csrf"]')
        if token is None:
            raise OSError('Instance login unavailable')
        fields = {**fields, 'csrf': token['value']}
        connection.request('POST', endpoint, body=urlencode(fields), headers={
            **headers, 'Origin': 'https://' + host,
            'Content-Type': 'application/x-www-form-urlencoded'})
        response = connection.getresponse()
        response.read(131072)
        return response.status, [v for k, v in response.getheaders() if k.lower() == 'set-cookie']
    finally:
        connection.close()


def create_app(root, config_path, exchange=backend):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    secret = root / 'session.key'
    if not secret.exists():
        with secret.open('xb') as stream:
            stream.write(secrets.token_bytes(32))
        if os.name != 'nt':
            secret.chmod(0o600)
    database = root / 'accounts.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, prefix TEXT UNIQUE NOT NULL)')
    if os.name != 'nt':
        database.chmod(0o600)
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=0, x_prefix=1)
    app.config.update(SECRET_KEY=secret.read_bytes(), MAX_CONTENT_LENGTH=8192,
                      SESSION_COOKIE_NAME='__Secure-campus_entry', SESSION_COOKIE_PATH='/app/',
                      SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    attempts, lock = OrderedDict(), threading.Lock()

    def configuration():
        config = json.loads(Path(config_path).read_text())
        # Even an operator error cannot turn the entrance into a general proxy.
        for route in config['routes']:
            if not ((route['prefix'] == '/campus' and route['port'] == 18771) or
                    (re.fullmatch(r'/spaces/[a-f0-9]{24}', route['prefix']) and
                     isinstance(route['port'], int) and 18900 <= route['port'] <= 18999)):
                raise ValueError('Invalid entrance route')
        return config

    @app.before_request
    def guard():
        if request.host != configuration()['host'] or not request.is_secure:
            abort(400)
        if request.method == 'POST':
            if request.headers.get('Origin') != 'https://' + request.host or not secrets.compare_digest(
                    request.form.get('csrf', '').encode(), session.get('csrf', secrets.token_urlsafe(32)).encode()):
                abort(403)
            peer, now = request.headers.get('X-Forwarded-For', request.remote_addr or '')[:128], time.monotonic()
            with lock:
                recent = [at for at in attempts.get(peer, []) if now - at < 60]
                if len(recent) >= 8:
                    abort(429)
                attempts[peer] = recent + [now]
                attempts.move_to_end(peer)
                if len(attempts) > 4096:
                    attempts.popitem(last=False)

    @app.after_request
    def headers(response):
        # Chrome sends Origin: null on navigation POSTs under no-referrer.
        # same-origin retains the origin needed by our form guard while still
        # suppressing referrers when leaving this site.
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    def enter(route, cookies):
        response = redirect(route['prefix'] + '/', code=303)
        for value in cookies:
            response.headers.add('Set-Cookie', value)
        response.set_cookie('__Secure-campus_route', route['prefix'], path='/app/',
                            max_age=30 * 86400, secure=True, httponly=True, samesite='Lax')
        return response

    @app.route('/', methods=['GET', 'POST'])
    def index():
        config = configuration()
        routes = {r['prefix']: r for r in config['routes']}
        error, status = None, 200
        if request.method == 'GET' and 'login' not in request.args:
            previous = request.cookies.get('__Secure-campus_route')
            if previous in routes:
                return redirect(previous + '/', code=303)
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if request.method == 'POST':
            name = request.form.get('username', '').strip().casefold()
            password = request.form.get('password', '')
            route = None
            if not 1 <= len(name) <= 64 or not 1 <= len(password) <= 1024:
                error, status = '请输入面板账号和密码', 400
            elif request.form.get('action') == 'activate':
                token = request.form.get('invitation', '').strip()
                if token.startswith('https://'):
                    url = urlsplit(token)
                    token = parse_qs(url.fragment).get('invite', [''])[0] if url.netloc == config['host'] else ''
                digest = hashlib.sha256(token.encode()).hexdigest()
                route = next((r for r in routes.values() if r.get('invitation', {}).get('expires', 0) > time.time()
                              and secrets.compare_digest(r['invitation'].get('digest', ''), digest)), None)
                if len(token) != 43 or route is None or not 12 <= len(password) <= 256:
                    error, status = '邀请无效或已过期，密码需为 12–256 个字符', 400
                elif name == config['owner_name'].casefold():
                    error, status = '此面板账号已被使用，请换一个', 409
                else:
                    try:
                        # Persist the route before redemption: a lost response must
                        # not strand an already activated account. Login proves ownership.
                        with sqlite3.connect(database) as db:
                            try:
                                db.execute('INSERT INTO accounts VALUES (?, ?)', (name, route['prefix']))
                            except sqlite3.IntegrityError:
                                row = db.execute('SELECT prefix FROM accounts WHERE name=?', (name,)).fetchone()
                                if row is None or row[0] != route['prefix']:
                                    raise
                        result, _ = exchange(route, config['host'], '/invite',
                                              {'invitation': token, 'password': password}, request.headers.get('X-Forwarded-For', request.remote_addr or '')[:128])
                        if result >= 500:
                            raise OSError('Activation result uncertain')
                        if result != 303:
                            # Keep the reservation for retry/login. Another request
                            # may have activated the instance while this one failed.
                            error, status = '邀请已使用或开通失败，请检查邀请', 400
                            route = None
                    except sqlite3.IntegrityError:
                        error, status, route = '账号或邀请已使用；已开通的账号请直接登录', 409, None
                    except (OSError, http.client.HTTPException):
                        error, status, route = '连接暂时中断，请用刚设置的账号和密码登录重试', 503, None
            else:
                if name == config['owner_name'].casefold():
                    route = routes.get('/campus')
                else:
                    with sqlite3.connect(database) as db:
                        row = db.execute('SELECT prefix FROM accounts WHERE name=?', (name,)).fetchone()
                    route = routes.get(row[0]) if row else None
                if route is None:
                    error, status = '面板账号或密码不正确', 401
            if route is not None and error is None:
                try:
                    result, cookies = exchange(route, config['host'], '/auth/login',
                        {'username': route['username'], 'password': password,
                         'remember': 'on' if request.form.get('remember') == 'on' else ''}, request.headers.get('X-Forwarded-For', request.remote_addr or '')[:128])
                    if result == 303:
                        return enter(route, cookies)
                    error, status = ('登录尝试过多，请一分钟后重试', 429) if result == 429 else ('面板账号或密码不正确', 401)
                except (OSError, http.client.HTTPException):
                    error, status = '面板暂时无法连接，请稍后重试', 503
        return render_template('entry.html', csrf=session['csrf'], error=error), status

    return app


def http_server(app, port=18801):
    from waitress import create_server
    # Matches the existing private backend: only nginx reaches this loopback
    # listener, and nginx overwrites forwarded headers before ProxyFix uses them.
    return create_server(app, host='127.0.0.1', port=port, threads=4,
                         clear_untrusted_proxy_headers=False, max_request_body_size=8192)


if __name__ == '__main__':
    http_server(create_app(os.environ['CAMPUS_ENTRY_ROOT'], os.environ['CAMPUS_ENTRY_CONFIG'])).run()
