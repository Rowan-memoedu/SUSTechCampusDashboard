"""Explicit operator reset of one account's connections; preserve its web login."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing

from hosted_admin import ROOT, REGISTRY, identifier
from hosted_backup import backup
from sustech_dashboard.hosted import Space

RESET_ROOT = Path('/var/lib/campus-reset-recovery')
ENTRY_ROOT = Path('/var/lib/campus-entry')


def revoke_metadata(sid):
    from sustech_dashboard.shared_metadata import MetadataStore
    MetadataStore(Path('/var/lib/campus-metadata/metadata.sqlite3')).revoke(sid)


def reset(sid, recovery):
    sid = identifier(sid)
    records = json.loads(REGISTRY.read_text())
    if not records.get(sid, {}).get('enabled'):
        raise ValueError('Unknown active space')
    source = ROOT/sid
    recovery = Path(recovery).resolve()
    if source.is_symlink() or source.resolve().parent != ROOT.resolve():
        raise ValueError('Unexpected space directory')
    if not recovery.is_relative_to(RESET_ROOT.resolve()) or recovery == RESET_ROOT.resolve() or recovery.exists():
        raise ValueError('Recovery must be a new private reset directory')
    unit = 'campus-space@'+sid+'.service'
    subprocess.run(['systemctl', 'stop', unit], check=True)
    try:
        relay = source/'print-relay/relay.sqlite3'
        if relay.exists():
            with closing(sqlite3.connect(relay)) as db:
                pending = db.execute("SELECT count(*) FROM operations WHERE kind IN ('upload','delete') AND state IN ('queued','running','needs_review')").fetchone()[0]
            if pending:
                raise ValueError('Unresolved school writes must be reviewed before resetting')
        with Space(source).db() as db:
            retained = {k: Space.get(db, k) for k in ('web', 'identity', 'enabled')}
        if not retained['web'] or not retained['enabled']:
            raise ValueError('Space is not activated')
        paths = list(source.rglob('*'))
        if any(p.is_symlink() for p in paths):
            raise ValueError('Recovery refuses symlinks')
        digests = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
        recovery.mkdir(parents=True, mode=0o700)
        verified = backup(source, recovery/'verified')
        archive = recovery/'space'
        shutil.move(str(source), str(archive))
        assert not source.exists()
        for name, digest in digests.items():
            assert hashlib.sha256((archive/name).read_bytes()).hexdigest() == digest
        source.mkdir(mode=0o700)
        with Space(source).db() as db:
            for key, value in retained.items():
                if value is not None:
                    Space.put(db, key, value)
        user = 'campus-'+sid
        shutil.chown(source, user=user, group=user)
        shutil.chown(source/'space.sqlite3', user=user, group=user)
        revoke_metadata(sid)
        handoffs = ENTRY_ROOT/'desktop-handoffs.sqlite3'
        if handoffs.exists():
            with closing(sqlite3.connect(handoffs)) as db:
                db.execute('DELETE FROM handoffs WHERE uri LIKE ?', ('%'+sid+'%',))
                db.commit()
        with Space(source).db() as db:
            assert Space.get(db, 'cas') is None
            assert Space.get(db, 'web') == retained['web']
        report = {'connections_reset': True, 'web_account_preserved': True, 'cas_removed': True,
                  'device_and_browser_sessions_removed': True, 'private_history_cleared': True,
                  'recovery_verified': verified['verified'], 'archived_files': len(digests),
                  'recovery': str(recovery), 'school_write_replays': 0}
        (recovery/'reset-report.json').write_text(json.dumps(report, indent=2))
        return report
    finally:
        subprocess.run(['systemctl', 'start', unit], check=True)


if __name__ == '__main__':
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Linux root required')
    parser = argparse.ArgumentParser()
    parser.add_argument('--space', required=True)
    parser.add_argument('--recovery', required=True)
    args = parser.parse_args()
    print(json.dumps(reset(args.space, args.recovery)))
