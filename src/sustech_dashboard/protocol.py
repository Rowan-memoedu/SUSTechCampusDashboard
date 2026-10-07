"""Windows URI activation: launch the existing client and use local authenticated IPC."""
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

import requests


def parse_uri(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in '\r\n\x00" '):
        raise ValueError('连接链接无效')
    uri = urlsplit(value)
    if uri.scheme != 'sustech-campus' or uri.netloc not in {'connect', 'login'} or uri.path not in {'', '/'} or uri.query:
        raise ValueError('连接链接无效')
    values = parse_qs(uri.fragment, strict_parsing=True)
    if uri.netloc == 'login':
        if set(values) != {'server', 'key'} or any(len(v) != 1 for v in values.values()):
            raise ValueError('登录连接链接无效')
        expected = 'https://' + os.environ.get('SUSTECH_PAIR_HOST', '124.221.144.155') + '/app'
        if values['server'][0] != expected or not re.fullmatch(r'[A-Za-z0-9_-]{43}', values['key'][0]):
            raise ValueError('登录连接地址无效')
        return {'login_server': expected, 'key': values['key'][0]}
    if set(values) != {'server', 'ticket'} or any(len(v) != 1 for v in values.values()):
        raise ValueError('连接链接无效')
    from .pairing import pair_server_url
    server, ticket = pair_server_url(values['server'][0]), values['ticket'][0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
        raise ValueError('连接链接无效')
    return {'url': server, 'ticket': ticket}


def register_protocol(executable=None):
    if os.name != 'nt' or (executable is None and not getattr(sys, 'frozen', False)):
        return
    import winreg
    executable = Path(executable or sys.executable).resolve()
    if not executable.is_file() or '"' in str(executable):
        raise ValueError('客户端路径无效')
    root = r'Software\Classes\sustech-campus'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:SUSTech Campus Dashboard')
        winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r'\shell\open\command') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'"{executable}" --connect-uri "%1"')


def handle_uri(value, port=18765):
    payload = parse_uri(value)  # Never launch anything for an invalid protocol argument.
    if 'login_server' in payload:
        with requests.Session() as pending:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                try:
                    response = pending.post(payload['login_server']+'/desktop/poll', json={'key': payload['key']},
                        headers={'X-Campus-Agent': '1'}, timeout=(10, 15), allow_redirects=False)
                    response.raise_for_status()
                except requests.RequestException:
                    time.sleep(3)
                    continue
                result = response.json()
                if result.get('uri'):
                    return handle_uri(result['uri'], port)
                if result.get('state') == 'expired':
                    return 1
                time.sleep(3)
        return 1
    from .runtime import owner_token
    from .paths import DATA_ROOT, prepare_private_directory
    from .core import save_json
    prepare_private_directory()
    base = f'http://127.0.0.1:{port}'
    token = owner_token()
    session = requests.Session()
    session.trust_env = False
    session.headers['Origin'] = base
    started = False
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        try:
            reply = session.post(base + '/auth/unlock', json={'token': token}, timeout=2)
            if reply.status_code == 200:
                break
            raise ValueError('本机端口被其他程序占用，未发送连接授权')
        except requests.ConnectionError:
            if not started:
                cmd = [sys.executable]
                if not getattr(sys, 'frozen', False):
                    cmd += ['-m', 'sustech_dashboard.client']
                cmd += ['--no-browser', '--port', str(port)]
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'),
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
                started = True
            time.sleep(.3)
    else:
        raise ValueError('本机组件未能启动，请重新运行客户端')
    page = session.get(base + '/connect', timeout=10)
    csrf = re.search(r'const roomCsrf="([^"]+)"', page.text)
    if not csrf:
        raise ValueError('请更新本机客户端后重新连接')
    result = session.post(base + '/api/instance/pair', json=payload,
                          headers={'X-CSRF-Token': csrf[1]}, timeout=(10, 90))
    data = result.json()
    if not result.ok:
        save_json(DATA_ROOT / 'connection-result.json', {'ok': False, 'message': data.get('error', '连接失败')})
        import webbrowser
        webbrowser.open(base + '/connect#access=' + token)
        return 1
    save_json(DATA_ROOT / 'connection-result.json', {'ok': True, 'message': data['message']})
    return 0
