"""Verify every recovery file and restore SQLite databases on the owner's PC."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile


def verify(archive, expected, output, logical_manifest=None):
    from sustech_dashboard.paths import prepare_private_directory
    if output.exists():
        raise ValueError('Use a new private verification directory')
    prepare_private_directory(output)
    portable = json.loads(logical_manifest.read_text()) if logical_manifest else None
    if portable and (portable.get('recovery_sha256') != expected or not portable.get('source_files_unchanged')):
        raise ValueError('Portable verification does not match this recovery')
    dump_differences = 0
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('Recovery transport hash differs')
    with tarfile.open(archive) as stream:
        manifest = json.load(stream.extractfile('recovery/recovery-manifest.json'))
        inventory = json.load(stream.extractfile('recovery/inventory.json'))
        if portable and set(portable['databases']) != set(manifest['databases']):
            raise ValueError('Portable database inventory differs')
        for index, (name, wanted) in enumerate(manifest['files'].items()):
            if not name.startswith('/') or '..' in PurePosixPath(name).parts:
                raise ValueError('Invalid recovery path')
            item = stream.extractfile('server' + name)
            if item is None or hashlib.file_digest(item, 'sha256').hexdigest() != wanted:
                raise ValueError('Archived file differs')
            if index % 2000 == 0:
                print(json.dumps({'verified_files': index}), flush=True)
        for index, (name, wanted) in enumerate(manifest['databases'].items()):
            directory = output / f'database-{index}'
            directory.mkdir()
            restored = directory / 'recovered.sqlite3'
            for suffix in ('', '-wal', '-shm'):
                key = name + suffix
                if key in manifest['files']:
                    with stream.extractfile('server' + key) as source, Path(str(restored) + suffix).open('wb') as dest:
                        import shutil
                        shutil.copyfileobj(source, dest)
            with closing(sqlite3.connect(restored)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Restored database integrity failed')
                logical = hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
                if logical != wanted:
                    dump_differences += 1
                    if not portable:
                        raise ValueError('Restored database content differs')
            if portable:
                from sqlite_recovery_digest import canonical
                if canonical(restored) != portable['databases'][name]:
                    raise ValueError('Restored typed rows or schema differ')
        result = {'verified': True, 'archive_sha256': expected, 'files': len(manifest['files']),
                  'restored_databases': len(manifest['databases']), 'retention_until': inventory['retention_until'],
                  'credentials_private': True, 'automatic_deletion': False,
                  'sql_dump_text_differences': dump_differences, 'portable_typed_content_verified': bool(portable)}
        (output / 'acceptance.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('archive', type=Path)
    p.add_argument('--sha256', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--logical-manifest', type=Path)
    a = p.parse_args()
    verify(a.archive, a.sha256, a.output, a.logical_manifest)
