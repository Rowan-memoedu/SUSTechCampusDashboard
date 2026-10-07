"""Explicit root operator reset: stop/revoke, verified archive, never replay school writes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from hosted_admin import CONFIG, REGISTRY, ROOT, identifier, save, revoke
from hosted_backup import backup


def recovery_path(path):
    path = Path(path).resolve()
    base = Path('/var/lib/campus-reset-recovery').resolve()
    if not path.is_relative_to(base) or path == base or path.exists():
        raise ValueError('Recovery must be a new child of the private reset directory')
    return path


def archive(source, recovery, name):
    source = Path(source)
    if source.is_symlink():
        raise ValueError('Archive refuses symlink sources')
    if not source.exists():
        return None
    destination = recovery/name
    if destination.exists():
        raise ValueError('Archive destination exists')
    if source.is_dir():
        result = backup(source, recovery/('verified-'+name))
        assert result['verified']
    else:
        import hashlib
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = {'verified': True, 'sha256': source_digest}
    shutil.move(str(source), str(destination))
    if source.exists() or not destination.exists():
        raise RuntimeError('Archive move incomplete')
    if destination.is_file():
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == source_digest
        destination.chmod(0o600)
    else:
        destination.chmod(0o700)
    return result


def stop(unit):
    subprocess.run(['systemctl', 'disable', '--now', unit], check=True)


def retire(recovery, initial=False, sid=None):
    recovery = recovery_path(recovery)
    recovery.mkdir(mode=0o700, parents=True)
    records = json.loads(REGISTRY.read_text())
    selected = [identifier(key) for key in records] if initial else [identifier(sid)]
    if any(s not in records for s in selected):
        raise ValueError('Unknown space')
    results = {}
    subprocess.run(['systemctl', 'stop', 'campus-entry.service'], check=True)
    try:
        if initial:
            stop('sustech-campus-dashboard.service')
        for key in selected:
            stop('campus-space@'+key+'.service')
        for key in selected:
            if (ROOT/key).exists():
                # Revoke before archiving; an accidentally restored directory
                # does not revive sessions/CAS by itself.
                revoke(key)
                results[key] = archive(ROOT/key, recovery, 'space-'+key)
            for path in [CONFIG/(key+'.env'), CONFIG/'routes'/(key+'.conf'), CONFIG/'private'/(key+'.invitation'),
                         Path('/etc/credstore.encrypted')/('campus-'+key+'-vault'),
                         Path('/etc/credstore.encrypted')/('campus-'+key+'-metadata')]:
                archive(path, recovery, path.name)
        if initial:
            subprocess.run(['systemctl', 'stop', 'campus-metadata.service'], check=True)
            results['owner'] = archive('/var/lib/sustech-room-monitor/dashboard', recovery, 'owner-data')
            for path in ['/etc/credstore/sustech-campus-web.json', '/etc/credstore.encrypted/sustech-cas']:
                archive(path, recovery, Path(path).name)
            results['metadata'] = archive('/var/lib/campus-metadata', recovery, 'shared-data')
            archive('/var/lib/campus-entry', recovery, 'entry-data')
            archive(CONFIG/'registry.json', recovery, 'registry.json')
            archive(CONFIG/'metadata-clients.json', recovery, 'metadata-clients.json')
            save(REGISTRY, {})
            save(CONFIG/'metadata-clients.json', {}, 0o640)
            shutil.chown(CONFIG/'metadata-clients.json', group='campus-metadata')
            for directory, user in [('/var/lib/campus-metadata', 'campus-metadata'),('/var/lib/campus-entry','campus-entry')]:
                Path(directory).mkdir(mode=0o700)
                shutil.chown(directory,user=user,group=user)
            subprocess.run(['systemctl','start','campus-metadata.service'],check=True)
        else:
            with sqlite3.connect('/var/lib/campus-entry/accounts.sqlite3') as db:
                db.execute('DELETE FROM accounts WHERE prefix=?',('/spaces/'+sid,))
            records=json.loads(REGISTRY.read_text())
            records.pop(sid)
            save(REGISTRY,records)
            clients=json.loads((CONFIG/'metadata-clients.json').read_text())
            clients.pop(sid,None)
            save(CONFIG/'metadata-clients.json',clients,0o640)
            shutil.chown(CONFIG/'metadata-clients.json',group='campus-metadata')
        from hosted_routes import install
        install()
        report={'verified':True,'created':time.time(),'retired_spaces':len(selected),'initial':initial,
                'recovery':str(recovery),'active_data_archived':results,'school_write_replays':0}
        save(recovery/'reset-report.json',report)
        return {'verified':True,'retired_spaces':len(selected),'owner_retired':initial,
                'recovery':str(recovery),'school_write_replays':0}
    finally:
        subprocess.run(['systemctl','start','campus-entry.service'],check=True)


if __name__=='__main__':
    if os.name!='posix' or os.geteuid()!=0:
        raise SystemExit('Linux root required')
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--initial',action='store_true')
    group.add_argument('--space')
    parser.add_argument('--recovery',required=True)
    args=parser.parse_args()
    print(json.dumps(retire(args.recovery,args.initial,args.space)))
