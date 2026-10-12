"""Install an already built candidate into the existing owner's local panel."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import requests


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def connect(root, base):
    session = requests.Session()
    session.trust_env = False
    response = session.post(base + '/auth/unlock', headers={'Origin': base},
        json={'token': (root / 'browser-token').read_text()}, timeout=5)
    response.raise_for_status()
    value = session.get(base + '/api/instance', timeout=10).json()
    if Path(value['data_root']).resolve() != root.resolve() or value['execution_mode'] != 'local':
        raise ValueError('Unexpected local account directory')
    return session, value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--program', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    base = 'http://127.0.0.1:18765'
    previous_release = json.loads((args.program / 'release.json').read_text())
    was_running = True
    try:
        session, before = connect(args.root, base)
    except requests.ConnectionError:
        from contextlib import ExitStack
        from sustech_dashboard.locking import exclusive_file
        from sustech_dashboard.paths import DATA_ROOT, download_root
        assert args.root.resolve() == DATA_ROOT.resolve()
        with ExitStack() as locks:
            for name in ('client.lock', 'backend.lock', 'download-agent.lock', 'print-agent.lock', 'homework/worker.lock'):
                locks.enter_context(exclusive_file(args.root / name))
        was_running = False
        before = {'version': previous_release['version'], 'download_root': str(download_root())}
    installer_record = json.loads(args.installer.with_name('installer.json').read_text())
    assert args.installer.name == installer_record['filename'] and sha(args.installer) == installer_record['sha256']
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\SUSTechCampusDashboard') as key:
        assert Path(winreg.QueryValueEx(key, 'InstallLocation')[0]).resolve() == args.program.resolve()
    protected = {name: sha(args.root / name) for name in ('browser-token', 'credentials.dpapi.json', 'instance.json')
                 if (args.root / name).exists()}
    before_baseline = json.loads((args.root / 'attachments.json').read_text())
    previous = {'version': before['version'], 'release': previous_release,
                'protected_hashes': protected, 'installer_sha256': installer_record['sha256']}
    args.report.with_name('before-' + args.report.name).write_text(json.dumps(previous, indent=2), encoding='utf-8')
    if was_running:
        subprocess.run([sys.executable, str(Path(__file__).with_name('stop_local_for_migration.py')), '--root', str(args.root)],
                       check=True, timeout=200)
    subprocess.run([str(args.installer.resolve()), '/VERYSILENT', '/SP-', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/NOLAUNCH=1', '/DIR=' + str(args.program.resolve()), '/LOG=' + str(args.report.with_suffix('.install.log').resolve())],
        check=True, timeout=240, creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.Popen([str(args.program / 'campus-client.exe'), '--no-browser'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            session, after = connect(args.root, base)
            break
        except requests.RequestException:
            time.sleep(1)
    else:
        raise ValueError('The candidate did not become ready; retained previous installer is needed for recovery')
    assert after['version'] == installer_record['version'] and after['configured']
    assert after['download_root'] == before['download_root']
    assert all(sha(args.root / name) == value for name, value in protected.items())
    baseline = json.loads((args.root / 'attachments.json').read_text())
    assert baseline.get('baseline_at') == before_baseline.get('baseline_at')
    assert set(before_baseline.get('seen', [])) <= set(baseline.get('seen', []))
    homework = session.get(base + '/api/homework', timeout=10)
    homework.raise_for_status()
    page = session.get(base + '/', timeout=10)
    assert '/static/homework.js' in page.text
    for file in ('homework.js', 'homework.css'):
        assert session.get(base + '/static/' + file, timeout=10).status_code == 200
    jobs = homework.json()['jobs']
    report = {'local_candidate_installed': True, 'version': after['version'], 'configured': True,
              'protected_settings_unchanged': True, 'downloads_baseline_preserved': True,
              'homework_enabled': homework.json()['enabled'], 'jobs': [{k: job[k] for k in ('id', 'state', 'stage')} for job in jobs],
              'school_writes_performed': False, 'public_release_changed': False,
              'installer_sha256': installer_record['sha256']}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__': main()
