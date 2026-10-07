"""Root-only entrance installation and route export. No passwords or CAS are exported."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit, parse_qs

from hosted_admin import CONFIG, REGISTRY, ROOT, save

ENTRY_CONFIG = Path('/etc/campus-entry/routes.json')


def refresh():
    owner_path = Path('/etc/credstore/sustech-campus-web.json')
    owner = json.loads(owner_path.read_text()) if owner_path.exists() else None
    records = json.loads(REGISTRY.read_text())
    routes = [{'prefix': '/campus', 'port': 18771, 'username': owner['username']}] if owner else []
    for sid, record in records.items():
        if not record['enabled']:
            continue
        with sqlite3.connect(f'file:{ROOT/sid/"space.sqlite3"}?mode=ro', uri=True) as db:
            row = db.execute("SELECT value FROM settings WHERE key='invitation'").fetchone()
        invitation = json.loads(row[0]) if row else {}
        private = CONFIG/'private'/(sid+'.invitation')
        if invitation and private.exists():
            token = parse_qs(urlsplit(private.read_text()).fragment).get('invite', [''])[0]
            if hashlib.sha256(token.encode()).hexdigest() == invitation['digest']:
                save(private, 'https://'+record['host']+'/app/?login=1#invite='+token)
        routes.append({'prefix': '/spaces/'+sid, 'port': record['port'], 'username': 'member',
                       'invitation': invitation})
    save(ENTRY_CONFIG, {'host': '124.221.144.155', 'owner_name': owner['username'] if owner else '', 'routes': routes}, 0o640)
    ENTRY_CONFIG.parent.chmod(0o750)
    shutil.chown(ENTRY_CONFIG.parent, group='campus-entry')
    shutil.chown(ENTRY_CONFIG, group='campus-entry')


def install(program):
    program = Path(program).resolve()
    if not program.is_relative_to('/opt/sustech-campus-hosted') or not (program/'src/sustech_dashboard/entry.py').is_file():
        raise ValueError('Use a verified hosted source release')
    if subprocess.run(['id', '-u', 'campus-entry'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/campus-entry',
                        '--shell', '/usr/sbin/nologin', 'campus-entry'], check=True)
    root = Path('/var/lib/campus-entry')
    root.mkdir(mode=0o700, exist_ok=True)
    shutil.chown(root, user='campus-entry', group='campus-entry')
    refresh()
    save('/etc/systemd/system/campus-entry.service', f'''[Unit]
Description=Unified campus dashboard entrance
After=network.target
[Service]
User=campus-entry
Group=campus-entry
Environment=PYTHONPATH={program}/src
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=CAMPUS_ENTRY_ROOT=/var/lib/campus-entry
Environment=CAMPUS_ENTRY_CONFIG=/etc/campus-entry/routes.json
ExecStart=/opt/sustech-campus-hosted/venv/bin/python -m sustech_dashboard.entry
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/campus-entry
InaccessiblePaths=-/var/lib/campus-spaces -/var/lib/sustech-room-monitor -/etc/credstore -/etc/credstore.encrypted -/etc/campus-spaces
MemoryMax=128M
TasksMax=32
LimitCORE=0
[Install]
WantedBy=multi-user.target
''', 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'campus-entry.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'campus-entry.service'], check=True)


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Linux root required')
    parser = argparse.ArgumentParser()
    parser.add_argument('--program')
    args = parser.parse_args()
    install(args.program) if args.program else refresh()
    print(json.dumps({'entrance_configured': True}))
