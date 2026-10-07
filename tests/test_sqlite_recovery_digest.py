import importlib.util
from pathlib import Path
import sqlite3


def fingerprint(path):
    spec = importlib.util.spec_from_file_location('sqlite_recovery_digest', Path(__file__).parents[1] / 'deploy/sqlite_recovery_digest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.canonical(path)


def test_portable_content_handles_types_and_ignores_physical_row_order(tmp_path):
    first, second = tmp_path / 'a.db', tmp_path / 'b.db'
    values = [(1, 'line\nbreak', b'\x00\xff', -0.25), (2, 'quote"text', b'blob', 1.125)]
    for path, records in ((first, values), (second, list(reversed(values)))):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE records (id INTEGER, text TEXT, body BLOB, value REAL)')
            db.executemany('INSERT INTO records VALUES (?,?,?,?)', records)
    assert fingerprint(first) == fingerprint(second)
    with sqlite3.connect(second) as db:
        db.execute('UPDATE records SET body=? WHERE id=1', (b'\x00\xfe',))
    assert fingerprint(first) != fingerprint(second)


def test_portable_content_detects_schema_change(tmp_path):
    path = tmp_path / 'schema.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)')
    before = fingerprint(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE INDEX value_lookup ON records(value)')
    assert fingerprint(path) != before
