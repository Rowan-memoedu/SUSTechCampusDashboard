"""Explicit local connection to the existing public login, using verified HTTPS."""
from html.parser import HTMLParser
import json
import os
import re
import uuid
from urllib.parse import urlsplit

import requests

from .core import DATA_ROOT


def pair_server_url(value):
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('连接地址无效')
    parsed = urlsplit(value)
    allowed = os.environ.get('SUSTECH_PAIR_HOST', '124.221.144.155')
    if (parsed.scheme != 'https' or parsed.netloc != allowed or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not re.fullmatch(r'/spaces/[a-f0-9]{24}', parsed.path)):
        raise ValueError('连接地址不是此客户端的公共入口')
    return value


def connect_ticket(value, ticket):
    from .download_agent import CONFIG
    from .dpapi_store import load_credentials, protect_password, unprotect_password, _restrict_directory
    from .core import load_json, save_json
    url = pair_server_url(value)
    if os.name != 'nt':
        raise ValueError('网页唤起当前适用于 Windows 客户端')
    if not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
        raise ValueError('连接授权无效，请重新点击网页中的连接电脑')
    old = load_json(CONFIG, {})
    if old and old.get('url') != url:
        raise ValueError('此电脑已连接另一空间，请使用已配对空间登录')
    try:
        sid, _ = load_credentials()
    except Exception:
        raise ValueError('请先在本机客户端登录校园账号并加密保存；无需重复输入面板账号密码') from None
    _restrict_directory(DATA_ROOT)
    identity = DATA_ROOT / 'download-agent-id.txt'
    try:
        with identity.open('x', encoding='ascii') as handle:
            handle.write(uuid.uuid4().hex)
    except FileExistsError:
        pass
    previous = unprotect_password(old['token_dpapi']) if old.get('token_dpapi') else None
    with requests.Session() as session:
        reply = session.post(url + '/api/devices/exchange', json={'ticket': ticket, 'sid': sid,
            'device': identity.read_text(encoding='ascii').strip(), 'previous': previous},
            headers={'X-Campus-Agent': '1'}, timeout=(10, 30), allow_redirects=False)
        if reply.status_code != 200:
            raise ValueError('连接授权已过期或校园账号不一致，请从已登录网页重新连接')
        result = reply.json()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', result.get('token', '')):
            raise ValueError('服务器返回无效电脑授权')
        save_json(CONFIG, {'url': url, 'token_dpapi': protect_password(result['token'])})
        intent = result['intent']
        state = 'connected'
        if intent.get('kind') != 'connect':
            from .local_files import target, open_target
            try:
                open_target(target(intent.get('key'), intent.get('kind')))
                state = 'opened'
            except (ValueError, OSError):
                state = 'open_failed'
        session.post(url + '/api/devices/result', json={'id': result['id'], 'state': state},
            headers={'Authorization': 'Bearer ' + result['token'], 'X-Campus-Agent': '1'},
            timeout=(10, 30), allow_redirects=False).raise_for_status()
    return {'ok': True, 'message': '电脑已连接，打印与下载组件正在上线', 'state': state}


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
        from .core import load_json
        return render_template('connect.html', csrf_token=csrf, api_base=request.script_root,
                               configured=runtime.configured.is_set(), result=load_json(DATA_ROOT / 'connection-result.json', {}))

    @app.post('/api/instance/pair')
    def local_pair():
        try:
            guard(write=True)
            if not runtime.configure_lock.acquire(False):raise ValueError('已有连接操作正在进行')
            try:
                payload = request.get_json() or {}
                result = connect_ticket(payload.get('url'), payload.get('ticket'))
                runtime.start_print_agent()
                return jsonify(result)
            finally:runtime.configure_lock.release()
        except Exception as exc:
            return jsonify(error=str(exc) if isinstance(exc, ValueError) else '连接失败，请检查网络后重试'),400
