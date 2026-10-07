"""Drain the existing local background service using its authenticated API."""
import argparse
import json
from pathlib import Path
import re
import time

import requests


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--port', type=int, default=18765)
    a = p.parse_args()
    session = requests.Session()
    session.trust_env = False
    base = f'http://127.0.0.1:{a.port}'
    headers = {'Origin': base}
    assert session.post(base + '/auth/unlock', headers=headers,
                        json={'token': (a.root / 'browser-token').read_text()}, timeout=5).status_code == 200
    value = session.get(base + '/api/instance', timeout=5).json()
    if Path(value['data_root']).resolve() != a.root.resolve() or value['execution_mode'] != 'local':
        raise ValueError('Unexpected target instance')
    page = session.get(base + '/settings', timeout=5)
    headers['X-CSRF-Token'] = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
    assert session.post(base + '/api/instance/stop', headers=headers, json={}, timeout=5).status_code == 202
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            session.get(base + '/api/instance', timeout=3)
        except requests.ConnectionError:
            break
        time.sleep(1)
    else:
        raise ValueError('Drain did not complete; preserve existing instance')
    # The supervisor may still be recording its final exit when HTTP closes.
    from sustech_dashboard.locking import exclusive_file, InstanceBusy
    from contextlib import ExitStack
    while time.monotonic() < deadline:
        try:
            with ExitStack() as stack:
                for name in ('client.lock', 'backend.lock', 'download-agent.lock', 'print-agent.lock'):
                    stack.enter_context(exclusive_file(a.root / name))
            break
        except InstanceBusy:
            time.sleep(.5)
    else:
        raise ValueError('A target worker remains active')
    print(json.dumps({'previous_version': value['version'], 'local_background_stopped': True,
                      'all_worker_locks_released': True}))


if __name__ == '__main__':
    main()
