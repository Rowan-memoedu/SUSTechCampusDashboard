"""Upgrade a real previous binary through the public signed feed, in isolation."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

import requests


def verify(executable, cache, expected):
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='upgrade-', dir=cache)).resolve()
    retained = {'attachments.json': b'{"seen":["canary-source"],"baseline_at":"2025-01-01"}',
                'credentials-canary.bin': b'opaque owner fixture; not actual credentials',
                'files/retained.txt': b'previously downloaded fixture'}
    for name, data in retained.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    from sustech_dashboard.actions import Actions
    action = Actions(root / 'actions.sqlite3')
    with action.connect() as db:
        db.execute('INSERT INTO actions VALUES (?,?,?,?,?)', ('canary', 'fixture', 'needs_review', '{}', 1))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_DOWNLOAD_ROOT=str(root / 'files'),
               SUSTECH_REGISTER_PROTOCOL='0', SUSTECH_EXECUTION_MODE='local', SUSTECH_INSTALL_ROOT=str(executable.parent))
    for key in ('SUSTECH_CLOUD', 'SUSTECH_PUBLIC_HOST', 'CREDENTIALS_DIRECTORY', 'SUSTECH_MANAGED_RUNTIME'):
        env.pop(key, None)
    before = None
    if os.name == 'nt':
        from verify_installer import snapshot, restore, PROTOCOL, OWNER
        before = {path: snapshot(path) for path in (PROTOCOL, OWNER)}
    log = (root / 'runtime.log').open('wb')
    child = subprocess.Popen([str(executable), '--local-only', '--no-browser', '--port', str(port)], env=env,
                             stdout=log, stderr=log)
    session = requests.Session()
    session.trust_env = False
    base = f'http://127.0.0.1:{port}'
    headers = {'Origin': base}
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if (root / 'browser-token').exists() and session.get(base + '/', timeout=2).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Old binary did not start')
        assert session.post(base + '/auth/unlock', headers=headers,
                            json={'token': (root / 'browser-token').read_text()}).status_code == 200
        page = session.get(base + '/')
        headers['X-CSRF-Token'] = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
        previous = session.get(base + '/api/instance').json()['version']
        assert previous != expected
        assert session.post(base + '/api/instance/updates/install', json={}, headers=headers).status_code == 202
        deadline = time.monotonic() + 900
        last = None
        while time.monotonic() < deadline:
            try:
                response = session.get(base + '/api/instance', timeout=3)
                if response.status_code == 401:
                    session.post(base + '/auth/unlock', headers={'Origin': base},
                                 json={'token': (root / 'browser-token').read_text()}, timeout=3)
                    continue
                value = response.json()
                state = (value['version'], value['update'].get('state'), value['update'].get('progress'))
                if state != last:
                    print(json.dumps({'version': state[0], 'state': state[1], 'progress': state[2]}), flush=True)
                    last = state
                if value['version'] == expected:
                    break
                if value['update'].get('state') == 'error':
                    error = value['update'].get('error', 'unknown')
                    raise RuntimeError(f'Signed upgrade failed ({error}); see private canary log')
            except (requests.RequestException, json.JSONDecodeError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Upgrade timeout')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            record = root / 'updates/last-result.json'
            if record.exists() and json.loads(record.read_text()).get('state') == 'installed':
                break
            time.sleep(.25)
        assert json.loads(record.read_text()) == {'state': 'installed', 'version': expected}
        assert all((root / name).read_bytes() == data for name, data in retained.items())
        with action.connect() as db:
            assert db.execute('SELECT state FROM actions WHERE id=?', ('canary',)).fetchone() == ('needs_review',)
        page = session.get(base + '/')
        headers['X-CSRF-Token'] = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
        assert session.post(base + '/api/instance/stop', json={}, headers=headers).status_code == 202
        assert child.wait(timeout=30) == 0
        result = {'previous': previous, 'version': expected, 'signed_public_upgrade': True,
                  'data_preserved': True, 'unknown_writes_not_replayed': True, 'root': str(root)}
        (root / 'acceptance.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        if child.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
            else:
                child.terminate()
                child.wait(timeout=30)
        log.close()
        if before is not None:
            for path, value in before.items():
                restore(path, value)
            assert all(snapshot(path) == value for path, value in before.items())


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('executable', type=Path)
    p.add_argument('--cache', required=True, type=Path)
    p.add_argument('--expected', required=True)
    a = p.parse_args()
    verify(a.executable.resolve(), a.cache.resolve(), a.expected)
