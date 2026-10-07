"""Small Linux/systemd operator CLI. No web endpoint can provision services."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time

ROOT = Path('/var/lib/campus-spaces')
CONFIG = Path('/etc/campus-spaces')
REGISTRY = CONFIG/'registry.json'
MAX_SPACES = 5


def identifier(value):
    if not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Invalid space ID')
    return value


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def save(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, indent=2) if not isinstance(value, str) else value)
    temp.chmod(mode)
    temp.replace(path)


@contextmanager
def registry():
    import fcntl
    CONFIG.mkdir(mode=0o750, parents=True, exist_ok=True)
    with (CONFIG/'registry.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
        yield data
        save(REGISTRY, data)


def render_nginx(sid, port, host):
    identifier(sid)
    if not 18900 <= int(port) <= 18999 or not re.fullmatch(r'[a-zA-Z0-9.-]+', host):
        raise ValueError('Invalid registered endpoint')
    template = (Path(__file__).with_name('nginx-campus-browser.conf')).read_text()
    return (template.replace('SUSTech campus dashboard', 'Campus space '+sid)
            .replace('/campus', '/spaces/'+sid).replace('18771', str(port))
            .replace('124.221.144.155', host).replace('_sustech_campus_auth', '_campus_auth_'+sid)
            .replace('sustech_campus_login', 'campus_login_'+sid))


def render_unit(program):
    program = Path(program).resolve()
    if not re.fullmatch(r'[a-zA-Z0-9/_.+-]+', str(program)) or not program.is_relative_to('/opt/sustech-campus-hosted') or not (program/'src/sustech_dashboard').is_dir():
        raise ValueError('Program must be a verified release under /opt/sustech-campus-hosted')
    return f'''[Unit]
Description=Independent campus space %i
After=network-online.target campus-metadata.service
Wants=network-online.target campus-metadata.service
[Service]
User=campus-%i
Group=campus-%i
WorkingDirectory=/var/lib/campus-spaces/%i
Environment=PYTHONPATH={program}/src
Environment=SUSTECH_EXECUTION_MODE=hosted
Environment=SUSTECH_CLOUD=1
Environment=SUSTECH_BROWSER_LOGIN=1
Environment=SUSTECH_SPACE_ID=%i
Environment=SUSTECH_DASHBOARD_DATA_ROOT=/var/lib/campus-spaces/%i
Environment=SUSTECH_PROXY_PREFIX=/spaces/%i
Environment=SUSTECH_METADATA_URL=http://127.0.0.1:18800
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=/etc/campus-spaces/%i.env
LoadCredentialEncrypted=campus-vault:/etc/credstore.encrypted/campus-%i-vault
LoadCredentialEncrypted=campus-metadata:/etc/credstore.encrypted/campus-%i-metadata
ExecStart=/opt/sustech-campus-hosted/venv/bin/python -m sustech_dashboard.client --backend --port $CAMPUS_PORT
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/campus-spaces/%i
InaccessiblePaths=-/var/lib/sustech-room-monitor -/etc/campus-spaces/private
MemoryHigh=256M
MemoryMax=512M
CPUQuota=50%
TasksMax=64
LimitFSIZE=268435456
LimitCORE=0
LogNamespace=campus-hosted
RestrictSUIDSGID=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
[Install]
WantedBy=multi-user.target
'''


def metadata_unit(program):
    return f'''[Unit]
Description=Campus metadata index (no attachment bodies)
After=network-online.target
[Service]
User=campus-metadata
Group=campus-metadata
Environment=PYTHONPATH={program}/src
Environment=SUSTECH_METADATA_ROOT=/var/lib/campus-metadata
Environment=SUSTECH_METADATA_REGISTRY=/etc/campus-spaces/metadata-clients.json
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/sustech-campus-hosted/venv/bin/python -m sustech_dashboard.shared_metadata
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/campus-metadata
MemoryMax=192M
CPUQuota=30%
TasksMax=32
LimitCORE=0
LogNamespace=campus-hosted
[Install]
WantedBy=multi-user.target
'''


def maintenance_units(program):
    program = Path(program).resolve()
    render_unit(program)  # Reuse the constrained release-path validation.
    return {
        Path('/etc/systemd/system/campus-hosted-retention.service'): f'''[Unit]
Description=Expire verified campus recovery backups
[Service]
Type=oneshot
ExecStart=/opt/sustech-campus-hosted/venv/bin/python {program}/deploy/hosted_upgrade.py --collect-only
User=root
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=-/var/backups/campus-hosted -/var/lib/sustech-room-monitor/upgrade-recovery
''',
        Path('/etc/systemd/system/campus-hosted-retention.timer'): '''[Unit]
Description=Check campus recovery retention hourly
[Timer]
OnBootSec=15min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
''',
    }


def install(program):
    import pwd
    program = Path(program).resolve()
    unit = render_unit(program)
    try:
        pwd.getpwnam('campus-metadata')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', '/var/lib/campus-metadata', '--shell', '/usr/sbin/nologin', 'campus-metadata')
    CONFIG.mkdir(mode=0o750, exist_ok=True)
    shutil.chown(CONFIG, group='campus-metadata')
    Path('/var/lib/campus-metadata').mkdir(mode=0o700, exist_ok=True)
    shutil.chown('/var/lib/campus-metadata', user='campus-metadata', group='campus-metadata')
    ROOT.mkdir(mode=0o711, exist_ok=True)
    if not (CONFIG/'metadata-clients.json').exists():
        save(CONFIG/'metadata-clients.json', {}, 0o640)
        shutil.chown(CONFIG/'metadata-clients.json', group='campus-metadata')
    save('/etc/systemd/system/campus-space@.service', unit, 0o644)
    save('/etc/systemd/system/campus-metadata.service', metadata_unit(program), 0o644)
    for path, value in maintenance_units(program).items():
        save(path, value, 0o644)
    save('/etc/systemd/journald@campus-hosted.conf', '[Journal]\nStorage=persistent\nSystemMaxUse=32M\nMaxRetentionSec=14day\n', 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'campus-metadata.service')
    run('systemctl', 'enable', '--now', 'campus-hosted-retention.timer')


def create(host):
    from cryptography.fernet import Fernet
    from sustech_dashboard.hosted import Space
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', host):
        raise ValueError('Invalid public host')
    with registry() as records:
        if sum(r['enabled'] for r in records.values()) >= MAX_SPACES:
            raise ValueError('Pilot limit reached: 5 active spaces')
        sid = secrets.token_hex(12)
        port = next(p for p in range(18900, 19000) if p not in {r['port'] for r in records.values()})
        name = 'campus-'+sid
        root = ROOT/sid
        root.mkdir(mode=0o700)
        run('useradd', '--system', '--user-group', '--home-dir', str(root), '--shell', '/usr/sbin/nologin', name)
        for credential, value in [('vault', Fernet.generate_key()), ('metadata', secrets.token_urlsafe(32).encode())]:
            if credential == 'metadata':
                digest = hashlib.sha256(value).hexdigest()
            run('systemd-creds', 'encrypt', '--name=campus-'+credential, '-', '/etc/credstore.encrypted/campus-'+sid+'-'+credential,
                input=value, stdout=subprocess.DEVNULL)
        token = Space(root).invite()
        for child in root.iterdir():
            shutil.chown(child, user=name, group=name)
        shutil.chown(root, user=name, group=name)
        save(CONFIG/(sid+'.env'), f'CAMPUS_PORT={port}\nSUSTECH_PUBLIC_HOST={host}\n')
        clients_path = CONFIG/'metadata-clients.json'
        clients = json.loads(clients_path.read_text())
        clients[sid] = {'digest': digest, 'enabled': True, 'scopes': ['public:weather']}
        save(clients_path, clients, 0o640)
        shutil.chown(clients_path, group='campus-metadata')
        records[sid] = {'port': port, 'host': host, 'enabled': True, 'created': time.time()}
        save(CONFIG/'routes'/(sid+'.conf'), render_nginx(sid, port, host), 0o644)
        # Invitation stays private and is deliberately never printed or published.
        save(CONFIG/'private'/(sid+'.invitation'), 'https://'+host+'/app/?login=1#invite='+token)
        (CONFIG/'private').chmod(0o700)
        run('systemctl', 'enable', '--now', 'campus-space@'+sid+'.service')
        return {'space_id': sid, 'url': 'https://'+host+'/app/', 'invitation_file': str(CONFIG/'private'/(sid+'.invitation'))}


def revoke(sid):
    from sustech_dashboard.hosted import Space
    identifier(sid)
    with registry() as records:
        if sid not in records:
            raise ValueError('Unknown space')
        run('systemctl', 'disable', '--now', 'campus-space@'+sid+'.service')
        Space(ROOT/sid).revoke()
        records[sid]['enabled'] = False
        path = CONFIG/'metadata-clients.json'
        clients = json.loads(path.read_text())
        clients[sid]['enabled'] = False
        save(path, clients, 0o640)
        shutil.chown(path, group='campus-metadata')
        from sustech_dashboard.shared_metadata import MetadataStore
        MetadataStore('/var/lib/campus-metadata/metadata.sqlite3').revoke(sid)
        (CONFIG/'private'/(sid+'.invitation')).unlink(missing_ok=True)
        # Data and operation ledger are retained for separately confirmed exit handling.


def renew_invitation(sid):
    from sustech_dashboard.hosted import Space
    identifier(sid)
    with registry() as records:
        record = records.get(sid)
        if not record or not record['enabled']:
            raise ValueError('Space is not enabled')
        token = Space(ROOT/sid).invite()
        path = CONFIG/'private'/(sid+'.invitation')
        save(path, 'https://'+record['host']+'/app/?login=1#invite='+token)
        return {'space_id': sid, 'invitation_file': str(path), 'expires_in_hours': 24}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('install').add_argument('--program', required=True)
    sub.add_parser('create').add_argument('--host', required=True)
    sub.add_parser('revoke').add_argument('space_id')
    sub.add_parser('invite').add_argument('space_id')
    sub.add_parser('list')
    args = parser.parse_args()
    if args.command in {'install', 'create', 'invite'}:
        parser.error('Operator hosting is retired; new deployments and invitations are disabled.')
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Run this operator command as Linux root')
    if args.command == 'install':
        install(args.program)
        print(json.dumps({'installed': True}))
    elif args.command == 'create':
        print(json.dumps(create(args.host)))
    elif args.command == 'revoke':
        revoke(args.space_id)
        print(json.dumps({'revoked': args.space_id}))
    elif args.command == 'invite':
        print(json.dumps(renew_invitation(args.space_id)))
    else:
        with registry() as records:
            print(json.dumps(records))
    if args.command in {'create', 'invite', 'revoke'} and Path('/etc/campus-entry/routes.json').exists():
        from entry_admin import refresh
        refresh()
