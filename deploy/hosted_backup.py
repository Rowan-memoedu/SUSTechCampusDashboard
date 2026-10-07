"""Consistent, private recovery copy; never copies downloaded file bodies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from contextlib import closing


def backup(source, destination, service=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir() or destination.exists() or destination.is_relative_to(source):
        raise ValueError('Backup destination must be new and outside the source')
    if service and service != 'campus-metadata.service' and not service.startswith(('sustech-campus-', 'campus-space@')):
        raise ValueError('Unexpected service')
    destination.mkdir(parents=True, mode=0o700)
    active = service and subprocess.check_output(['systemctl', 'is-active', service], text=True).strip() == 'active'
    records = []
    try:
        if active:
            subprocess.run(['systemctl', 'stop', service], check=True)
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source)
            if any(part in {'upstream', 'print-files', 'print-relay-files', 'releases', 'downloads', '__pycache__'} for part in relative.parts):
                continue
            if not path.is_file() or path.is_symlink() or path.suffix in {'.lock', '.part', '.document'} or path.name.endswith(('-wal', '-shm', '-journal')):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open('rb') as header:
                sqlite = header.read(16) == b'SQLite format 3\x00'
            if sqlite:
                with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('SQLite recovery verification failed')
                    original = '\n'.join(src.iterdump()).encode()
                    restored = '\n'.join(dst.iterdump()).encode()
                    if original != restored:
                        raise RuntimeError('SQLite logical restore mismatch')
            else:
                shutil.copyfile(path, target)
                if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                    raise RuntimeError('Recovery file mismatch')
            target.chmod(0o600)
            records.append({'path': str(relative), 'bytes': target.stat().st_size,
                            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        manifest = {'source': str(source), 'files': records, 'verified': True, 'download_bodies': False}
        (destination / 'recovery-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return {'destination': str(destination), 'verified_files': len(records), 'verified': True}
    finally:
        if active:
            subprocess.run(['systemctl', 'start', service], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--service')
    args = parser.parse_args()
    print(json.dumps(backup(args.source, args.destination, args.service)))
