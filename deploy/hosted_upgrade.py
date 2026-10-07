"""Sequential hosted rollout with a consistent fleet backup and automatic code rollback.

No schema downgrade or campus write replay is performed. Backups contain metadata,
personal state and sealed credentials only, not downloaded/uploaded attachment bodies.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import requests

from hosted_admin import CONFIG, REGISTRY, ROOT, render_unit, metadata_unit, maintenance_units, save
from hosted_backup import backup


def systemctl(action, unit):
    subprocess.run(['systemctl', action, unit], check=True)


def healthy(record, sid):
    session = requests.Session()
    session.trust_env = False
    for _ in range(75):
        try:
            result = session.get(f"http://127.0.0.1:{record['port']}/auth/login", headers={
                'Host': record['host'], 'X-Forwarded-Proto': 'https', 'X-Forwarded-Prefix': '/spaces/'+sid}, timeout=1)
            if result.status_code == 200 and 'csrf' in result.text:
                return True
        except requests.RequestException:
            pass
        time.sleep(.2)
    return False


def metadata_healthy():
    session = requests.Session()
    session.trust_env = False
    for _ in range(75):
        try:
            if session.post('http://127.0.0.1:18800/v1/metadata', json={}, timeout=1).status_code == 401:
                return True
        except requests.RequestException:
            pass
        time.sleep(.2)
    return False


def upgrade(program):
    records = {k:v for k,v in json.loads(REGISTRY.read_text()).items() if v['enabled']}
    units = {Path('/etc/systemd/system/campus-space@.service'): render_unit(program),
             Path('/etc/systemd/system/campus-metadata.service'): metadata_unit(program)}
    previous = {path: path.read_text() for path in units}
    recovery = Path('/var/backups/campus-hosted')/str(time.time_ns())
    recovery.mkdir(mode=0o700, parents=True)
    services = ['campus-space@'+sid+'.service' for sid in records]
    stopped = []
    try:
        for service in services:
            systemctl('stop', service)
            stopped.append(service)
        systemctl('stop', 'campus-metadata.service')
        stopped.append('campus-metadata.service')
        for sid in records:
            backup(ROOT/sid, recovery/'spaces'/sid)
        backup('/var/lib/campus-metadata', recovery/'metadata')
        for path, value in previous.items():
            save(recovery/path.name, value)
        shutil.copytree(CONFIG, recovery/'config', ignore=shutil.ignore_patterns('private', '*.lock'))
        sealed = recovery/'sealed-credentials'
        sealed.mkdir(mode=0o700)
        for sid in records:
            for kind in ('vault', 'metadata'):
                source = Path('/etc/credstore.encrypted')/('campus-'+sid+'-'+kind)
                shutil.copyfile(source, sealed/source.name)
        save(recovery/'fleet.json', {'schema': 1, 'verified': True, 'created': time.time(), 'spaces': list(records)})
        for path, value in units.items():
            save(path, value, 0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        systemctl('start', 'campus-metadata.service')
        if not metadata_healthy():
            raise RuntimeError('Metadata candidate did not become healthy')
        for sid, record in records.items():
            systemctl('start', 'campus-space@'+sid+'.service')
            if not healthy(record, sid):
                raise RuntimeError('Hosted candidate did not become healthy')
        for path, value in maintenance_units(program).items():
            save(path, value, 0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', '--now', 'campus-hosted-retention.timer'], check=True)
        return {'upgraded': len(records), 'recovery': str(recovery), 'backup_verified': True}
    except Exception:
        for path, value in previous.items():
            save(path, value, 0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'reset-failed', 'campus-metadata.service', *services], check=True)
        systemctl('restart', 'campus-metadata.service')
        if not metadata_healthy():
            raise RuntimeError('Metadata rollback needs operator attention') from None
        for sid, record in records.items():
            systemctl('restart', 'campus-space@'+sid+'.service')
            if not healthy(record, sid):
                raise RuntimeError('Rollback needs operator attention') from None
        raise RuntimeError('Hosted upgrade failed; previous program restored, personal state retained') from None
    finally:
        for service in stopped:
            subprocess.run(['systemctl', 'start', service], check=True)


def collect_directory(root, marker_name, prefix='', now=None):
    root = Path(root).resolve()
    if not root.exists():
        return 0
    removed = 0
    for child in root.iterdir():
        if not child.name.startswith(prefix) or child.is_symlink() or not child.is_dir() or not child.resolve().is_relative_to(root):
            continue
        marker = child/marker_name
        if marker.is_file() and not marker.is_symlink():
            try:
                state = json.loads(marker.read_text())
            except (ValueError, OSError):
                continue
            created = state.get('created', marker.stat().st_mtime)
            if state.get('verified') is True and isinstance(created, (float, int)) and created < (now or time.time())-7*86400:
                shutil.rmtree(child)
                removed += 1
    return removed


def collect_backups():
    removed = collect_directory('/var/backups/campus-hosted', 'fleet.json')
    removed += collect_directory('/var/lib/sustech-room-monitor/upgrade-recovery', 'recovery-manifest.json', 'hosted-phase1-')
    return removed


if __name__ == '__main__':
    raise SystemExit('Operator hosting is retired; use explicit migration and retirement tooling.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--program')
    parser.add_argument('--collect-only', action='store_true')
    args = parser.parse_args()
    if args.collect_only:
        result = {'expired_verified_backups_removed': collect_backups()}
    else:
        if not args.program:
            parser.error('--program is required for upgrade')
        result = upgrade(Path(args.program).resolve())
        collect_backups()
    print(json.dumps(result))
