"""Exact operator-host retirement, with frozen recovery before resource removal.

Run each phase explicitly on the operator Linux host. Private evidence stays in
the root-only work directory; stdout contains counts and hashes only.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time

ROOTS = tuple(map(Path, (
    '/opt/sustech-campus-hosted', '/var/lib/campus-spaces', '/var/lib/campus-entry',
    '/var/lib/campus-metadata', '/etc/campus-spaces', '/etc/campus-entry',
    '/var/backups/campus-hosted', '/var/lib/campus-reset-recovery',
    '/var/lib/campus-hosted-recovery', '/var/lib/sustech-room-monitor', '/opt/sustech-room-monitor')))
WORK = Path('/tmp/campus-retirement-040')
CONFIG = Path('/etc/nginx/sites-available/graspmemoedu')
MARKERS = ('Campus hosted spaces', 'SUSTech campus dashboard')
UNITS = ('campus-entry.service', 'campus-metadata.service', 'campus-hosted-retention.service',
         'campus-hosted-retention.timer', 'sustech-campus-dashboard.service', 'campus-space@.service')


def run(*args, check=True):
    value = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if check and value.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return value.stdout.strip()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(name, value):
    path = WORK / name
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


def load(name):
    return json.loads((WORK / name).read_text())


def strip_blocks(text):
    for label in MARKERS:
        pattern = r'(?m)^[ \t]*# BEGIN ' + re.escape(label) + r'\n.*?^[ \t]*# END ' + re.escape(label) + r'[^\n]*'
        text, count = re.subn(pattern, '', text, flags=re.S)
        if count != 1:
            raise ValueError('Unexpected shared nginx markers')
    return text


def replace_blocks(text, body):
    original = strip_blocks(text)
    for index, label in enumerate(MARKERS):
        pattern = r'(?m)^[ \t]*# BEGIN ' + re.escape(label) + r'\n.*?^[ \t]*# END ' + re.escape(label) + r'[^\n]*'
        content = body if index == 0 else '    location = /campus { return 410; }\n    location ^~ /campus/ { return 410; }'
        text, count = re.subn(pattern, '# BEGIN ' + label + '\n' + content + '\n# END ' + label, text, flags=re.S)
        if count != 1:
            raise ValueError('Unexpected marker count')
    if strip_blocks(text) != original:
        raise ValueError('Shared configuration changed')
    return text


def nginx(text):
    before = CONFIG.read_bytes()
    temporary = CONFIG.with_suffix('.campus-new')
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(CONFIG)
    try:
        run('/usr/sbin/nginx', '-t')
        run('systemctl', 'reload', 'nginx')
    except Exception:
        CONFIG.write_bytes(before)
        run('/usr/sbin/nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        raise


def inflight(root):
    counts = {}
    for name, table, states in (
        ('actions.sqlite3', 'actions', ('sending',)),
        ('print-agent-actions.sqlite3', 'actions', ('sending',)),
        ('materials.sqlite3', 'jobs', ('running',)),
        ('print-relay/relay.sqlite3', 'operations', ('queued', 'running'))):
        path = root / name
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                counts[name] = db.execute(f"SELECT count(*) FROM {table} WHERE state IN ({','.join('?' for _ in states)})", states).fetchone()[0]
    return counts


def prepare():
    import pwd
    if (WORK / 'inventory.json').exists() or (WORK / 'frozen.json').exists():
        raise ValueError('Preparation already completed')
    WORK.mkdir(mode=0o700, exist_ok=True)
    registry = json.loads(Path('/etc/campus-spaces/registry.json').read_text())
    active = [sid for sid, record in registry.items() if record.get('enabled')]
    if len(registry) != 1 or len(active) != 1 or not re.fullmatch(r'[a-f0-9]{24}', active[0]):
        raise ValueError('Unexpected other spaces; preserve them for review')
    unit = 'campus-space@' + active[0] + '.service'
    pid = int(run('systemctl', 'show', unit, '-p', 'MainPID', '--value'))
    env = {}
    for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        if b'=' in item:
            key, value = item.decode().split('=', 1)
            if key.startswith('SUSTECH_') or key == 'CREDENTIALS_DIRECTORY':
                env[key] = value
    credentials = WORK / 'runtime-credentials'
    credentials.mkdir(mode=0o700, exist_ok=True)
    for name in ('campus-vault', 'campus-metadata'):
        shutil.copyfile(Path(env['CREDENTIALS_DIRECTORY']) / name, credentials / name)
        (credentials / name).chmod(0o600)
    env['CREDENTIALS_DIRECTORY'] = str(credentials)
    save('source-environment.json', env)
    units = []
    for name in (*UNITS, unit):
        if name == 'campus-space@.service':
            template = Path('/etc/systemd/system') / name
            if not template.is_file():
                raise ValueError('Space template missing')
            properties = 'FragmentPath=' + str(template)
        else:
            properties = run('systemctl', 'show', name, '-p', 'FragmentPath,DropInPaths,ActiveState,UnitFileState,User')
        units.append({'name': name, 'properties': properties})
    paths = []
    for root in ROOTS:
        if root.is_symlink() or root.resolve() != root or not root.is_dir():
            raise ValueError('Retirement root differs')
        paths.append({'path': str(root), 'allocated': int(run('du', '-s', '-B1', '--', str(root)).split()[0])})
    accounts = [dict(name=a.pw_name, uid=a.pw_uid, gid=a.pw_gid, home=a.pw_dir, shell=a.pw_shell)
                for a in pwd.getpwall() if any(Path(a.pw_dir) == r or Path(a.pw_dir).is_relative_to(r) for r in ROOTS)]
    # Every deleted identity must be a dedicated, unprivileged service account.
    if any(a['uid'] == 0 or a['shell'] not in ('/usr/sbin/nologin', '/bin/false') for a in accounts):
        raise ValueError('Unexpected account in project roots')
    (WORK / 'nginx.before').write_bytes(CONFIG.read_bytes())
    run('/usr/sbin/nginx', '-t')
    shared = strip_blocks(CONFIG.read_text())
    private_credentials = [str(p) for p in Path('/etc/credstore.encrypted').iterdir()
                           if p.name in {f'campus-{active[0]}-vault', f'campus-{active[0]}-metadata'}]
    if len(private_credentials) != 2:
        raise ValueError('Credential inventory differs')
    save('inventory.json', {'unit': unit, 'units': units, 'paths': paths, 'accounts': accounts,
                           'credentials': private_credentials, 'shared_nginx_sha256': hashlib.sha256(shared.encode()).hexdigest(),
                           'disk': shutil.disk_usage('/var/lib')._asdict(),
                           'created': datetime.now(timezone.utc).isoformat(),
                           'retention_until': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()})
    return {'prepared': True, 'roots': len(paths), 'accounts': len(accounts), 'source_unchanged': True,
            'inflight': sum(inflight(Path(env['SUSTECH_DASHBOARD_DATA_ROOT'])).values())}


def freeze():
    inv = load('inventory.json')
    env = load('source-environment.json')
    root = Path(env['SUSTECH_DASHBOARD_DATA_ROOT'])
    # First prevent new external work; keep existing backend requests alive.
    body = '    location = /app { return 503; }\n    location ^~ /app/ { return 503; }\n    location = /spaces { return 503; }\n    location ^~ /spaces/ { return 503; }'
    nginx(replace_blocks(CONFIG.read_text(), body))
    run('systemctl', 'stop', 'campus-hosted-retention.timer', 'campus-hosted-retention.service')
    deadline = time.monotonic() + 120
    while sum(inflight(root).values()):
        if time.monotonic() >= deadline:
            raise ValueError('Unresolved in-flight operations; keep recovery and inspect')
        time.sleep(2)
    run('systemctl', 'stop', 'campus-entry.service', inv['unit'])
    if any(run('systemctl', 'is-active', name, check=False) == 'active' for name in (inv['unit'], 'campus-entry.service')):
        raise ValueError('Source remains active')
    os.environ.update(env)
    from sustech_dashboard.hosted import Space
    from sustech_dashboard.shared_metadata import configured_client
    from migrate_local import export_owner
    space = Space()
    owner = space.credentials()
    with space.db() as db:
        identity = space.get(db, 'identity', '')
    if not owner or not hmac.compare_digest(identity, hmac.new(space.key_bytes(), owner['sid'].encode(), hashlib.sha256).hexdigest()):
        raise ValueError('Source owner mismatch')
    result = export_owner(root, WORK / 'export', owner['sid'], configured_client())
    run('systemctl', 'stop', 'campus-metadata.service', 'sustech-campus-dashboard.service')
    save('frozen.json', dict(result, source_stopped=True, inflight=0))
    archive = WORK / 'migration.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for path in sorted((WORK / 'export').rglob('*')):
            if path.is_file():
                stream.add(path, arcname=path.relative_to(WORK / 'export'))
    return dict(result, source_stopped=True, migration_sha256=digest(archive))


def backup():
    import pwd
    inv = load('inventory.json')
    load('frozen.json')
    if any(run('systemctl', 'is-active', u['name'], check=False) == 'active' for u in inv['units']):
        raise ValueError('Project units still active')
    sources = list(ROOTS) + [Path(x) for x in inv['credentials']]
    for unit in inv['units']:
        for line in unit['properties'].splitlines():
            if line.startswith(('FragmentPath=', 'DropInPaths=')):
                sources.extend(Path(p) for p in line.split('=', 1)[1].split() if p)
    sources = sorted(set(sources))
    files, databases = {}, {}
    for source in sources:
        for path in sorted(source.rglob('*')) if source.is_dir() else (source,):
            if path.is_file() and not path.is_symlink():
                files[str(path)] = digest(path)
                if path.suffix in ('.sqlite3', '.sqlite', '.db') and path.read_bytes()[:16] == b'SQLite format 3\0':
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise ValueError('Source SQLite integrity failed')
                        databases[str(path)] = hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
    save('recovery-manifest.json', {'files': files, 'databases': databases, 'source_roots': [str(p) for p in sources]})
    archive = WORK / 'recovery.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for source in sources:
            stream.add(source, arcname='server/' + str(source).lstrip('/'))
        for path in sorted(WORK.iterdir()):
            if path.name not in {'recovery.tar.gz', 'recovery-check.json'}:
                stream.add(path, arcname='recovery/' + path.name)
    owner = pwd.getpwnam('ubuntu')
    # Only these two private transport files become readable to the SSH owner.
    for path in (archive, WORK / 'migration.tar.gz'):
        path.chmod(0o600)
        destination = Path('/tmp') / ('campus-local040-' + path.name)
        if destination.exists():
            raise ValueError('Transport file already exists')
        shutil.copyfile(path, destination)
        destination.chmod(0o600)
        os.chown(destination, owner.pw_uid, owner.pw_gid)
    result = {'recovery_sha256': digest(archive), 'migration_sha256': digest(WORK / 'migration.tar.gz'),
              'files': len(files), 'databases': len(databases), 'bytes': archive.stat().st_size}
    save('recovery-check.json', result)
    return result


def static_site(source):
    inv = load('inventory.json')
    if hashlib.sha256(strip_blocks(CONFIG.read_text()).encode()).hexdigest() != inv['shared_nginx_sha256']:
        raise ValueError('Shared nginx changed')
    destination = Path('/var/www/sustech-campus-site')
    if destination.exists():
        source_files = {p.relative_to(source): digest(p) for p in source.rglob('*') if p.is_file()}
        current_files = {p.relative_to(destination): digest(p) for p in destination.rglob('*') if p.is_file()}
        if source_files != current_files:
            raise ValueError('Existing site differs; review before replacing it')
    else:
        shutil.copytree(source, destination)
    for path in (destination, *destination.rglob('*')):
        path.chmod(0o755 if path.is_dir() else 0o644)
    nginx(replace_blocks(CONFIG.read_text(), static_routes(destination)))
    return {'static_site': True, 'shared_nginx_unchanged': True, 'private_routes_retired': True}


def static_routes(destination=Path('/var/www/sustech-campus-site')):
    body = '''    location = /app {
        if ($request_method !~ ^(GET|HEAD)$) { return 410; }
        return 302 /app/;
    }
    location = /app/ {
        if ($request_method !~ ^(GET|HEAD)$) { return 410; }
        root /var/www/sustech-campus-site;
        try_files /index.html =404;
        default_type text/html;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'" always;
        add_header Referrer-Policy no-referrer always;
        add_header Cache-Control no-cache always;
    }
    location ~ ^/app/(site\\.css|site\\.js|tokens\\.css|release\\.json)$ {
        alias /var/www/sustech-campus-site/$1;
        limit_except GET { deny all; }
    }
    location ^~ /app/downloads/ {
        alias /var/www/sustech-campus-site/downloads/;
        autoindex off;
        limit_except GET { deny all; }
        add_header X-Content-Type-Options nosniff always;
    }
    location /app/ { return 410; }
    location = /spaces { return 410; }
    location ^~ /spaces/ { return 410; }'''
    return body.replace('/var/www/sustech-campus-site', str(destination))


def extras():
    import pwd
    audit = load('extra-audit.json')
    old_unit = Path('/etc/systemd/system/sustech-room-monitor.service')
    if any(not r['recognized_unit'] and r['file'] != str(old_unit) for r in audit['account_refs']):
        raise ValueError('Other unit consumes a project account')
    if audit['owned_outside_roots'] or audit['active_account_processes']:
        raise ValueError('Other account-owned resources require review')
    if audit['namespaces'] != ['campus-hosted']:
        raise ValueError('Unexpected journal namespace')
    if run('systemctl', 'is-active', 'sustech-room-monitor.service', check=False) == 'active':
        raise ValueError('Retired monitor unexpectedly running')
    run('systemctl', 'stop', 'systemd-journald@campus-hosted.socket', 'systemd-journald@campus-hosted.service')
    paths = [old_unit] + [Path(p) for p in audit['extras']]
    for path in paths:
        safe = path == old_unit or path == Path('/etc/systemd/journald@campus-hosted.conf') or (
            path.parent in (Path('/var/log/journal'), Path('/run/log/journal')) and re.fullmatch(r'[a-f0-9]{32}\.campus-hosted', path.name))
        if not safe or path.is_symlink() or path.resolve() != path:
            raise ValueError('Unexpected supplementary path')
    files = {str(p): digest(p) for source in paths for p in (source.rglob('*') if source.is_dir() else (source,))
             if p.is_file() and not p.is_symlink()}
    record = {'files': files, 'databases': {}, 'source_roots': [str(p) for p in paths]}
    save('extras-manifest.json', record)
    archive = WORK / 'extras.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for path in paths:
            stream.add(path, arcname='server' + str(path))
        stream.add(WORK / 'extras-manifest.json', arcname='recovery/recovery-manifest.json')
        stream.add(WORK / 'inventory.json', arcname='recovery/inventory.json')
        stream.add(WORK / 'extra-audit.json', arcname='recovery/extra-audit.json')
    destination = Path('/tmp/campus-local040-extras.tar.gz')
    if destination.exists():
        raise ValueError('Extra transport already exists')
    shutil.copyfile(archive, destination)
    owner = pwd.getpwnam('ubuntu')
    destination.chmod(0o600)
    os.chown(destination, owner.pw_uid, owner.pw_gid)
    result = {'sha256': digest(archive), 'bytes': archive.stat().st_size, 'files': len(files), 'paths': len(paths)}
    save('extras-check.json', result)
    return result


def purge(proof_directory):
    import grp
    import pwd
    inv, audit = load('inventory.json'), load('extra-audit.json')
    for file, record in (('recovery.json', load('recovery-check.json')), ('extras.json', load('extras-check.json'))):
        proof = json.loads((proof_directory / file).read_text())
        expected = record.get('recovery_sha256', record.get('sha256'))
        if not proof.get('verified') or proof.get('archive_sha256') != expected:
            raise ValueError('D-drive recovery verification missing')
    local = json.loads((proof_directory / 'local.json').read_text())
    if not all(local.get(k) for k in ('owner_verified_with_school', 'applied', 'protected_local_files_unchanged', 'standalone')):
        raise ValueError('Local migration verification missing')
    live = json.loads((proof_directory / 'live.json').read_text())
    if live.get('version') != '0.4.0' or not live.get('verified'):
        raise ValueError('Live replacement verification missing')
    if hashlib.sha256(strip_blocks(CONFIG.read_text()).encode()).hexdigest() != inv['shared_nginx_sha256']:
        raise ValueError('Shared nginx changed')
    if not Path('/var/www/sustech-campus-site/index.html').is_file() or '/var/www/sustech-campus-site/' not in CONFIG.read_text():
        raise ValueError('Static site is not configured')
    units = [u['name'] for u in inv['units'] if u['name'] != 'campus-space@.service'] + ['sustech-room-monitor.service']
    if any(run('systemctl', 'is-active', name, check=False) == 'active' for name in units):
        raise ValueError('Operator service remains active')
    if audit['owned_outside_roots'] or any(g['other_users'] or g['members'] for g in audit['groups']):
        raise ValueError('Project accounts have outside consumers')
    for account in inv['accounts']:
        current = pwd.getpwnam(account['name'])
        if current.pw_uid != account['uid'] or current.pw_dir != account['home']:
            raise ValueError('Service account changed')
    for process in Path('/proc').iterdir():
        try:
            if process.name.isdigit() and process.stat().st_uid in {a['uid'] for a in inv['accounts']}:
                raise ValueError('Service account still owns a process')
        except FileNotFoundError:
            pass
    targets = set()
    for file in ('recovery-manifest.json', 'extras-manifest.json'):
        manifest = load(file)
        for name, expected in manifest['files'].items():
            path = Path(name)
            if not path.is_file() or path.is_symlink() or digest(path) != expected:
                raise ValueError('Frozen recovery source changed')
        targets.update(Path(name) for name in manifest['source_roots'])
    known_units = set(UNITS) | {'sustech-room-monitor.service'}
    for path in targets:
        allowed = path in ROOTS or str(path) in inv['credentials'] or str(path) in audit['extras'] or (
            path.parent == Path('/etc/systemd/system') and path.name in known_units) or (
            path.parent.parent == Path('/etc/systemd/system') and path.parent.name.removesuffix('.d') in known_units)
        if not allowed or path.is_symlink() or path.resolve() != path:
            raise ValueError('Unclassified removal target')
    before = sum(int(run('du', '-s', '-B1', '--', str(p)).split()[0]) for p in targets)
    disk_before = shutil.disk_usage('/var/lib').free
    run('systemctl', 'disable', *units)
    for path in sorted(targets, key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for account in inv['accounts']:
        run('userdel', account['name'])  # Never recursively remove a service home.
        try:
            group = grp.getgrgid(account['gid'])
        except KeyError:
            continue
        if not group.gr_mem and not any(p.pw_gid == group.gr_gid for p in pwd.getpwall()):
            run('groupdel', group.gr_name)
    run('systemctl', 'daemon-reload')
    for name in units:
        run('systemctl', 'reset-failed', name, check=False)
    if any(path.exists() for path in targets):
        raise ValueError('A retired path remains')
    if any(run('systemctl', 'is-active', name, check=False) == 'active' for name in units):
        raise ValueError('A retired unit is active')
    run('/usr/sbin/nginx', '-t')
    result = {'retired': True, 'removed_paths': len(targets), 'removed_roots': len(ROOTS),
              'removed_accounts': len(inv['accounts']), 'allocated_bytes_removed': before,
              'filesystem_free_delta': shutil.disk_usage('/var/lib').free - disk_before,
              'shared_nginx_unchanged': True, 'D_recovery_retained': True}
    save('retirement-result.json', result)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=('prepare', 'freeze', 'backup', 'site', 'extras', 'purge'))
    p.add_argument('--site', type=Path)
    p.add_argument('--proof', type=Path)
    a = p.parse_args()
    result = static_site(a.site) if a.phase == 'site' else purge(a.proof) if a.phase == 'purge' else globals()[a.phase]()
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if WORK.exists():
            import traceback
            (WORK / 'failure.txt').write_text(traceback.format_exc())
        print(json.dumps({'retirement_stopped': True, 'reason': type(exc).__name__,
                          'detail': str(exc) if isinstance(exc, ValueError) else 'Inspect private evidence'}), flush=True)
        raise SystemExit(1) from None
