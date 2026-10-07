"""Read the real replacement instance without recording campus response bodies."""
import argparse
import json
from pathlib import Path
import time

import requests


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--report', required=True, type=Path)
    p.add_argument('--stage', required=True, type=Path)
    a = p.parse_args()
    session = requests.Session()
    session.trust_env = False
    base = 'http://127.0.0.1:18765'
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if session.get(base + '/', timeout=2).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise ValueError('Local component did not start')
    assert session.get(base + '/api/instance').status_code == 401
    assert session.post(base + '/auth/unlock', headers={'Origin': base},
                        json={'token': (a.root / 'browser-token').read_text()}, timeout=5).status_code == 200
    value = session.get(base + '/api/instance', timeout=10).json()
    assert value['version'] == '0.4.0' and value['execution_mode'] == 'local' and value['configured']
    assert Path(value['data_root']).resolve() == a.root.resolve()
    from migrate_local import standalone
    for name in ('/api/status', '/api/attachments'):
        response = session.get(base + name, timeout=30)
        assert response.status_code == 200 and standalone(response.json())
    for name in ('/', '/settings', '/static/update.js', '/static/update.css'):
        assert session.get(base + name, timeout=10).status_code == 200
    old = json.loads((a.stage / 'attachments.json').read_text())
    current = json.loads((a.root / 'attachments.json').read_text())
    assert set(old.get('seen', [])) <= set(current.get('seen', []))
    assert current.get('baseline_at') == old.get('baseline_at')
    from sustech_dashboard.materials_local import LocalMaterials
    inventory = LocalMaterials(Path(value['download_root']), a.root / 'downloaded-files.json').inventory()
    staged = json.loads((a.stage / 'stage.json').read_text())
    counts = {state: sum(x['status'] == state for x in inventory) for state in ('saved', 'missing', 'modified')}
    assert counts['saved'] >= staged['inventory']['saved']
    assert counts['missing'] <= staged['inventory']['missing'] and counts['modified'] <= staged['inventory']['modified']
    from sustech_dashboard.download_agent import personal_pair_available
    assert not personal_pair_available()
    result = {'verified': True, 'version': value['version'], 'configured': True, 'execution_mode': 'local',
              'operator_pair_inactive': True, 'standalone_api': True, 'baseline_preserved': True,
              'file_inventory': counts, 'private_api_requires_auth': True, 'school_writes_performed': False}
    a.report.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
