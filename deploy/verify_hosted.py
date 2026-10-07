"""Run read-only campus-side acceptance using two disposable, unbound spaces.

Only local invitation/session state is changed; no CAS login or school write occurs.
Passwords and invitations never enter output, arguments or reports.
"""
import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import parse_qs, urlparse
import requests

from hosted_admin import CONFIG, ROOT, identifier
from sustech_dashboard.actions import Actions
from sustech_dashboard.materials_store import MaterialsStore


def csrf(response):
    response.raise_for_status()
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    return match.group(1)


def verify(ids):
    sessions = []
    for sid in ids:
        identifier(sid)
        invitation = (CONFIG/'private'/(sid+'.invitation')).read_text()
        parsed = urlparse(invitation)
        base = 'https://'+parsed.netloc+'/spaces/'+sid
        origin = 'https://'+parsed.netloc
        token = parse_qs(parsed.fragment)['invite'][0]
        session = requests.Session()
        session.trust_env = False
        session.headers['Origin'] = origin
        password = secrets.token_urlsafe(24)
        assert session.get(base+'/api/instance', timeout=10).status_code == 401
        entry = origin+'/app/'
        form = csrf(session.get(entry+'?login=1', timeout=10))
        name = 'fixture-'+sid
        activated = session.post(entry, data={'csrf': form, 'action': 'activate', 'username': name,
            'invitation': token, 'password': password, 'remember': 'on'}, allow_redirects=False, timeout=30)
        assert activated.status_code == 303 and activated.headers['Location'] == '/spaces/'+sid+'/'
        assert session.post(entry, data={'csrf': form, 'action': 'activate', 'username': name,
            'invitation': token, 'password': password}, allow_redirects=False, timeout=30).status_code == 409
        assert session.post(entry, data={'csrf': form, 'username': name, 'password': password},
                            allow_redirects=False, timeout=30).status_code == 303
        form = csrf(session.get(base+'/', timeout=10))
        state = session.get(base+'/api/instance', timeout=10)
        assert state.status_code == 200 and state.json()['configured'] is False
        assert session.post(base+'/api/instance/updates/install', json={}, headers={'X-CSRF-Token': form}, timeout=10).status_code == 403
        assert session.post(base+'/api/instance/stop', json={}, headers={'X-CSRF-Token': form}, timeout=10).status_code == 403
        forged = requests.get(base+'/api/instance', headers={'X-Campus-Original-URI': '/spaces/'+sid+'/invite', 'X-Forwarded-Proto': 'https', 'X-Forwarded-Prefix': '/campus'}, timeout=10)
        assert forged.status_code == 401
        sessions.append((sid, session, base, form, password))
    a, b = sessions
    cookie = next(c.value for c in a[1].cookies if 'campus_session' in c.name)
    replay = requests.get(b[2]+'/api/instance', headers={'Cookie': '__Secure-campus_session_'+b[0]+'='+cookie}, timeout=10)
    assert replay.status_code == 401
    for own, other in ((a[0], b[0]), (b[0], a[0])):
        result = subprocess.run(['runuser', '-u', 'campus-'+own, '--', 'test', '-r', str(ROOT/other/'space.sqlite3')])
        assert result.returncode != 0
    # Download IDs and print operations are stored in different account directories.
    stores = [MaterialsStore(ROOT/sid/'materials.sqlite3') for sid in ids]
    jid = stores[0].enqueue(['course/content/file'], 'isolation fixture')
    try:
        stores[1].retry_keys(jid)
        raise AssertionError('Cross-space task lookup succeeded')
    except ValueError:
        pass
    from sustech_dashboard.print_relay import PrintRelay
    import uuid
    unrelated = str(uuid.uuid4())
    for sid in ids:
        try:
            PrintRelay(ROOT/sid/'print-relay').operation(unrelated)
            raise AssertionError('Unknown print task was returned')
        except ValueError:
            pass
    subprocess.run(['systemctl', 'restart', 'campus-space@'+a[0]], check=True)
    import time
    for _ in range(50):
        try:
            if a[1].get(a[2]+'/api/instance', timeout=2).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(.2)
    else:
        raise AssertionError('Session did not survive restart')
    subprocess.run(['systemctl', 'stop', 'campus-space@'+a[0]], check=True)
    try:
        assert b[1].get(b[2]+'/api/instance', timeout=5).status_code == 200
    finally:
        subprocess.run(['systemctl', 'start', 'campus-space@'+a[0]], check=True)
    new_password = secrets.token_urlsafe(24)
    changed = b[1].post(b[2]+'/api/instance/password', json={'previous': b[4], 'password': new_password}, headers={'X-CSRF-Token': b[3]}, timeout=10)
    assert changed.status_code == 200
    assert b[1].get(b[2]+'/api/instance', timeout=10).status_code == 401
    print(json.dumps({'spaces': ids, 'cross_cookie_denied': True, 'forged_proxy_headers_denied': True,
                      'cross_directory_denied': True, 'cross_download_task_denied': True,
                      'print_queues_isolated': True, 'sessions_survive_restart': True,
                      'stop_one_other_healthy': True, 'password_change_revokes_sessions': True,
                      'unified_activation_and_login': True, 'campus_requests': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('spaces', nargs=2)
    verify(parser.parse_args().spaces)
