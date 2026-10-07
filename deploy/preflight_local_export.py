"""Read the single active owner's data while hosting still runs; no retirement."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile


def main():
    records = json.loads(Path('/etc/campus-spaces/registry.json').read_text())
    active = [sid for sid, record in records.items() if record.get('enabled')]
    if len(active) != 1 or not re.fullmatch(r'[a-f0-9]{24}', active[0]):
        raise ValueError('Expected exactly the inventoried active space')
    unit = 'campus-space@' + active[0] + '.service'
    pid = int(subprocess.check_output(['systemctl', 'show', unit, '-p', 'MainPID', '--value'], text=True))
    if pid <= 0:
        raise ValueError('Source service must be available for the preflight')
    for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\x00'):
        if b'=' not in item: continue
        key, value = item.decode().split('=', 1)
        if key.startswith('SUSTECH_') or key == 'CREDENTIALS_DIRECTORY':
            os.environ[key] = value
    from sustech_dashboard.hosted import Space
    from sustech_dashboard.shared_metadata import configured_client
    from sustech_dashboard.paths import DATA_ROOT
    from migrate_local import export_owner
    space = Space()
    credentials = space.credentials()
    if not credentials:
        raise ValueError('The source needs its owner to authenticate')
    with space.db() as db:
        identity = space.get(db, 'identity', '')
    expected = hmac.new(space.key_bytes(), credentials['sid'].encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(identity, expected):
        raise ValueError('Stored source owner differs from bound identity')
    destination = Path('/tmp/campus-local040-export')
    result = export_owner(DATA_ROOT, destination, credentials['sid'], configured_client())
    archive = destination.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
        for path in sorted(destination.rglob('*')):
            if path.is_file(): stream.add(path, arcname=path.relative_to(destination))
    archive.chmod(0o600)
    import pwd
    owner = pwd.getpwnam('ubuntu')
    os.chown(archive, owner.pw_uid, owner.pw_gid)
    result.update(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  source_stopped=False, preflight_only=True)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'preflight_failed': type(exc).__name__, 'source_unchanged': True}))
        raise SystemExit(1) from None
