"""Portable typed-content fingerprint independent of Python iterdump formatting."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical(path):
    def encode(value):
        if isinstance(value, bytes):
            return {'blob': value.hex()}
        if isinstance(value, float):
            return {'float': value.hex()}
        return value
    def dump(value):
        return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as db:
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql').fetchall()
        content = []
        for kind, name, _, _ in schema:
            if kind == 'table':
                # Names come only from SQLite's schema, never shell text.
                escaped = name.replace('"', '""')
                rows = sorted(dump([encode(value) for value in row]) for row in db.execute(f'SELECT * FROM "{escaped}"'))
                content.append((name, rows))
        return hashlib.sha256(dump([schema, content]).encode()).hexdigest()


if __name__ == '__main__':
    from retire_hosted import WORK, digest, load, save
    manifest = load('recovery-manifest.json')
    values = {}
    for name in manifest['databases']:
        path = Path(name)
        if digest(path) != manifest['files'][name]:
            raise ValueError('Frozen source changed')
        values[name] = canonical(path)
    result = {'recovery_sha256': load('recovery-check.json')['recovery_sha256'], 'databases': values,
              'sqlite_version': sqlite3.sqlite_version, 'source_files_unchanged': True}
    save('logical-check.json', result)
    destination = Path('/tmp/campus-local040-logical-check.json')
    destination.write_bytes((WORK / 'logical-check.json').read_bytes())
    destination.chmod(0o600)
    import os, pwd
    owner = pwd.getpwnam('ubuntu')
    os.chown(destination, owner.pw_uid, owner.pw_gid)
    print(json.dumps({'databases': len(values), 'source_files_unchanged': True, 'sqlite_version': sqlite3.sqlite_version}))
