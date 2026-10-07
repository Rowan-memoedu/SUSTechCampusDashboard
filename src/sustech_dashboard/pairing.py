"""Explicit local connection to the existing public login, using verified HTTPS."""
from html.parser import HTMLParser
import json
import os
import re
from urllib.parse import urlsplit

import requests

from .core import DATA_ROOT


class LoginToken(HTMLParser):
    token = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'csrf':
            self.token = attrs.get('value')


def server_url(value):
    if not isinstance(value, str):raise ValueError('请输入公共入口地址')
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.fragment
            or parsed.path.rstrip('/') != '/app' or parsed.query not in {'', 'login=1'}):
        raise ValueError('请输入 HTTPS 公共入口地址，例如 https://124.221.144.155/app/')
    return 'https://' + parsed.netloc + '/app/'


def connect(value, username, password):
    from .download_agent import CONFIG
    from .dpapi_store import load_credentials, protect_password, _restrict_directory
    if os.name != 'nt':raise ValueError('Linux 执行主机请使用 systemd campus-pair 加密凭据')
    if CONFIG.exists():raise ValueError('此电脑已有配对信息；请先在本机断开后重新连接')
    if not isinstance(username, str) or not username.strip() or len(username) > 64:
        raise ValueError('请输入面板账号')
    if not isinstance(password, str) or not password or len(password) > 256:
        raise ValueError('请输入面板密码，不能使用 CAS 密码代替')
    try:sid, _ = load_credentials()
    except Exception:raise ValueError('请先在本机登录校园账号，并勾选加密保存凭据') from None
    base = server_url(value)
    origin = 'https://' + urlsplit(base).netloc
    with requests.Session() as session:
        page = session.get(base + '?login=1', timeout=(10,30), allow_redirects=False)
        page.raise_for_status()
        token = LoginToken(); token.feed(page.text)
        if not token.token:raise ValueError('此地址不是校园面板公共入口')
        login = session.post(base, data={'username': username.strip(), 'password': password,
            'csrf': token.token, 'action': 'login'}, headers={'Origin': origin},
            timeout=(10,30), allow_redirects=False)
        prefix = login.headers.get('Location', '')
        if login.status_code != 303 or not re.fullmatch(r'/spaces/[a-f0-9]{24}/', prefix):
            raise ValueError('面板登录失败，请检查账号密码或稍后重试')
        url = origin + prefix.rstrip('/')
        reply = session.get(url + '/api/printing/connection', headers={'X-Campus-Sid': sid},
                            timeout=(10,30), allow_redirects=False)
        if reply.status_code != 200:raise ValueError('本机校园账号与托管空间不一致，或空间尚未绑定，未配对')
        result = reply.json()
        if result.get('same_identity') is not True or result.get('username') != 'member':
            raise ValueError('服务器配对信息无效')
    _restrict_directory(DATA_ROOT)
    encrypted = protect_password(password)
    with CONFIG.open('x', encoding='utf-8') as handle:
        json.dump({'url': url, 'username': result['username'], 'password_dpapi': encrypted}, handle)
    return {'ok': True, 'message': '已连接自己的空间；打印组件正在自动连接，请返回打印页等待主机上线'}


def register_pairing(app, runtime, guard, csrf):
    from flask import jsonify, render_template, request
    from .execution import mode
    if mode() != 'local':return

    @app.get('/connect')
    def connect_page():
        return render_template('connect.html', csrf_token=csrf, api_base=request.script_root,
                               configured=runtime.configured.is_set())

    @app.post('/api/instance/pair')
    def local_pair():
        try:
            guard(write=True)
            if not runtime.configure_lock.acquire(False):raise ValueError('已有连接操作正在进行')
            try:
                payload = request.get_json() or {}
                result = connect(payload.get('url'), payload.get('username'), payload.get('password'))
                runtime.start_print_agent()
                return jsonify(result)
            finally:runtime.configure_lock.release()
        except Exception as exc:
            return jsonify(error=str(exc) if isinstance(exc, ValueError) else '连接失败，请检查网络后重试'),400
