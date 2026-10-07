"""Owner-checked, staged migration. No credentials or queued writes are imported."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3

from sustech_dashboard.core import load_json, save_json
from sustech_dashboard.paths import prepare_private_directory

DOCUMENTS = ('snapshot.json', 'manifest.json', 'attachments.json', 'downloaded-files.json')
DATABASES = ('materials.sqlite3', 'actions.sqlite3', 'print-agent-actions.sqlite3', 'print-relay/relay.sqlite3')
FILES = DOCUMENTS + DATABASES


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(path):
    if path.suffix != '.sqlite3':
        return digest(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()


def owner_tag(owner, salt):
    return hmac.new(bytes.fromhex(salt), owner.strip().encode(), hashlib.sha256).hexdigest()


def standalone(value):
    if isinstance(value, dict):
        return '_metadata_ref' not in value and all(standalone(v) for v in value.values())
    return not isinstance(value, list) or all(standalone(v) for v in value)


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == '.sqlite3':
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
            if list(src.iterdump()) != list(dst.iterdump()):
                raise ValueError('Database recovery differs')
    else:
        shutil.copyfile(source, target)


def export_owner(root, destination, owner, client):
    """Caller has verified the space's bound identity and frozen its writers."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root):
        raise ValueError('Export needs a new directory outside the original data')
    prepare_private_directory(destination)
    from sustech_dashboard.shared_metadata import unpack_document
    records = {}
    for name in FILES:
        source, target = root / name, destination / name
        if not source.exists():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError('Unexpected source path')
        if name in {'snapshot.json', 'manifest.json'}:
            value = unpack_document(load_json(source, {}), client)
            if not standalone(value):
                raise ValueError('Shared references remain unresolved')
            save_json(target, value)
        else:
            copy_file(source, target)
        records[name] = digest(target)
    salt = secrets.token_hex(32)
    manifest = {'schema': 1, 'owner_salt': salt, 'owner': owner_tag(owner, salt), 'files': records,
                'credentials_included': False, 'standalone': True}
    save_json(destination / 'migration.json', manifest)
    return {'exported_files': len(records), 'standalone': True, 'credentials_included': False}


def validate_package(source, owner):
    manifest = load_json(source / 'migration.json', {})
    if manifest.get('schema') != 1 or not hmac.compare_digest(
            manifest.get('owner', ''), owner_tag(owner, manifest.get('owner_salt', ''))):
        raise ValueError('Migration belongs to another campus identity')
    records = manifest.get('files', {})
    if not isinstance(records, dict) or set(records) - set(FILES):
        raise ValueError('Unexpected migration file')
    for name, expected in records.items():
        path = source / name
        if path.is_symlink() or not path.resolve().is_relative_to(source) or digest(path) != expected:
            raise ValueError('Migration file integrity failed')
        if name in DOCUMENTS and not standalone(load_json(path, {})):
            raise ValueError('Unresolved shared reference')
    return manifest


def merge_document(local, remote):
    # A newer snapshot wins each matching record; older unique records remain
    # available as historical data. Live sync will enforce current term scope.
    older, newer = (remote, local) if str(local.get('updated_at', '')) >= str(remote.get('updated_at', '')) else (local, remote)
    result = {**older, **newer}
    for field in ('courses', 'blackboard_courses', 'items', 'assignments'):
        if field not in result:
            continue
        def key(row):
            if field in {'courses', 'blackboard_courses'}:
                return (row.get('id'),)
            return tuple(row.get(k) for k in ('course_id', 'content_id', 'id'))
        rows = {}
        for row in older.get(field, []) + newer.get(field, []):
            identity = key(row)
            if not any(identity):
                raise ValueError('Migration record lacks a stable identity')
            rows[identity] = row
        result[field] = list(rows.values())
    return result


def rows(path, table):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
            return []
        return [dict(row) for row in db.execute('SELECT * FROM ' + table)]


def merge_ledger(source, destination, table):
    with sqlite3.connect(destination) as db:
        db.row_factory = sqlite3.Row
        expected_columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        for record in rows(source, table):
            if set(record) != expected_columns:
                raise ValueError('Unexpected operation schema')
            if record['state'] in {'sending', 'running', 'queued'}:
                record['state'] = 'needs_review'
                if 'lease' in record:
                    record['lease'], record['owner'] = None, None
            existing = db.execute(f'SELECT * FROM {table} WHERE id=?', (record['id'],)).fetchone()
            if existing:
                field = 'object' if table == 'actions' else 'target' if table == 'operations' else 'signature'
                if existing[field] != record[field]:
                    raise ValueError('Conflicting operation identity')
                stamp = 'updated' if table == 'jobs' else 'touched' if table == 'operations' else 'at'
                if (existing[stamp] or 0) >= (record[stamp] or 0):
                    continue
            columns = list(record)
            db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(record.values()))
        if table in {'jobs', 'operations'}:
            db.execute(f"UPDATE {table} SET state='needs_review' WHERE state IN ('queued','running')")
        else:
            db.execute("UPDATE actions SET state='needs_review' WHERE state='sending'")


def stage(source, local, destination, owner, download_root):
    source, local, destination = (Path(p).resolve() for p in (source, local, destination))
    package = validate_package(source, owner)
    if destination.exists() or destination.is_relative_to(local) or destination.is_relative_to(source):
        raise ValueError('Stage needs a new separate private directory')
    prepare_private_directory(destination)
    before = {}
    for name in FILES:
        original = local / name
        before[name] = fingerprint(original) if original.exists() else None
        if original.exists():
            copy_file(original, destination / name)
    for name in ('snapshot.json', 'manifest.json'):
        if name in package['files']:
            save_json(destination / name, merge_document(load_json(destination / name, {}), load_json(source / name, {})))
    old, incoming = load_json(destination / 'attachments.json', {}), load_json(source / 'attachments.json', {})
    if old or incoming:
        # The first baseline time remains meaningful; union only the owner's seen keys.
        save_json(destination / 'attachments.json', {**incoming, **old,
                  'seen': sorted(set(old.get('seen', [])) | set(incoming.get('seen', [])))})
    from sustech_dashboard.materials_store import MaterialsStore
    from sustech_dashboard.materials_local import LocalMaterials
    store = MaterialsStore(destination / 'materials.sqlite3')
    with store.connect(True) as db:
        store.put(db, 'auto', False)  # No historical task is replayed at migration startup.
        for row in rows(source / 'materials.sqlite3', 'seen'):
            db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (row['key'],))
        ready = bool(db.execute('SELECT 1 FROM seen LIMIT 1').fetchone())
        store.put(db, 'baseline_ready', ready)
    merge_ledger(source / 'materials.sqlite3', store.path, 'jobs')
    receipts = load_json(source / 'downloaded-files.json', {})
    for row in rows(source / 'materials.sqlite3', 'files'):
        receipt = json.loads(row['receipt'])
        if receipt.get('path'):
            receipts.setdefault(row['key'], receipt)
    receipts.update(load_json(local / 'downloaded-files.json', {}))
    save_json(destination / 'downloaded-files.json', receipts)
    inventory = LocalMaterials(download_root, destination / 'downloaded-files.json').inventory()
    with store.connect(True) as db:
        # Reconcile every saved-file claim against this computer's actual bytes.
        db.execute('DELETE FROM files')
        for receipt in inventory:
            db.execute('INSERT INTO files VALUES (?,?)', (receipt['key'], json.dumps(receipt)))
    from sustech_dashboard.actions import Actions
    for name in ('actions.sqlite3', 'print-agent-actions.sqlite3'):
        Actions(destination / name)
        merge_ledger(source / name, destination / name, 'actions')
    from sustech_dashboard.print_relay import PrintRelay
    PrintRelay(destination / 'print-relay')
    merge_ledger(source / 'print-relay/relay.sqlite3', destination / 'print-relay/relay.sqlite3', 'operations')
    final = {name: digest(destination / name) for name in FILES if (destination / name).exists()}
    report = {'schema': 1, 'owner_salt': package['owner_salt'], 'owner': package['owner'],
              'local_before': before, 'files': final, 'auto_download_paused': True,
              'inventory': {state: sum(r['status'] == state for r in inventory) for state in ('saved', 'existing', 'missing', 'modified')},
              'credentials_included': False, 'standalone': True}
    save_json(destination / 'stage.json', report)
    return {'staged_files': len(final), 'inventory': report['inventory'], 'standalone': True, 'auto_download_paused': True}


def commit(staged, local, recovery, owner):
    """Verify unchanged originals and stage first; caller must stop the old agent."""
    from sustech_dashboard.locking import exclusive_file
    staged, local, recovery = (Path(p).resolve() for p in (staged, local, recovery))
    report = load_json(staged / 'stage.json', {})
    if not hmac.compare_digest(report.get('owner', ''), owner_tag(owner, report.get('owner_salt', ''))):
        raise ValueError('Migration owner mismatch')
    if recovery.exists() or recovery.is_relative_to(local):
        raise ValueError('Recovery directory must be new and outside local data')
    if set(report.get('files', {})) - set(FILES) or set(report.get('local_before', {})) != set(FILES):
        raise ValueError('Invalid migration stage')
    with ExitStack() as locks:
        for name in ('client.lock', 'backend.lock', 'download-agent.lock', 'print-agent.lock'):
            locks.enter_context(exclusive_file(local / name))
        for name, expected in report['local_before'].items():
            original = local / name
            if (fingerprint(original) if original.exists() else None) != expected:
                raise ValueError('Local data changed; stage migration again')
        for name, expected in report['files'].items():
            if digest(staged / name) != expected:
                raise ValueError('Migration stage changed')
        prepare_private_directory(recovery)
        for name, expected in report['local_before'].items():
            if expected is not None:
                copy_file(local / name, recovery / name)
                if fingerprint(recovery / name) != expected:
                    raise ValueError('Local recovery readback failed')
                if name in DATABASES:
                    with closing(sqlite3.connect(local / name)) as db:
                        if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                            raise ValueError('Local database still has active writers')
        save_json(recovery / 'recovery.json', {'before': report['local_before'], 'retention_days': 7})
        replaced = []
        try:
            for name in report['files']:
                target = local / name
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + '.migration-new')
                shutil.copyfile(staged / name, temp)
                os.replace(temp, target)
                replaced.append(name)
            if any(digest(local / name) != expected for name, expected in report['files'].items()):
                raise ValueError('Migration readback failed; retain recovery')
        except Exception:
            for name in reversed(replaced):
                target = local / name
                if report['local_before'][name] is None:
                    target.unlink()
                else:
                    temp = target.with_name(target.name + '.migration-restore')
                    shutil.copyfile(recovery / name, temp)
                    os.replace(temp, target)
            raise
        save_json(local / 'migration-result.json', {'standalone': True, 'verified': True, 'auto_download_paused': True})
    return {'verified': True, 'standalone': True, 'files': len(report['files']), 'auto_download_paused': True}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='operation', required=True)
    export = sub.add_parser('export')
    export.add_argument('--destination', type=Path, required=True)
    merge = sub.add_parser('stage')
    merge.add_argument('--source', type=Path, required=True)
    merge.add_argument('--destination', type=Path, required=True)
    apply = sub.add_parser('commit')
    apply.add_argument('--source', type=Path, required=True)
    apply.add_argument('--recovery', type=Path, required=True)
    args = parser.parse_args()
    from sustech_dashboard.paths import DATA_ROOT, download_root
    from sustech_dashboard.authentication import load_owner_credentials, current_credentials
    if not load_owner_credentials():
        raise ValueError('Authenticate the existing owner before migration')
    from sustech_dashboard.provider import Blackboard
    Blackboard()  # Independent school authentication; no campus writes.
    owner = current_credentials()[0]
    if args.operation == 'export':
        from sustech_dashboard.hosted import Space
        from sustech_dashboard.shared_metadata import configured_client
        space = Space()
        with space.db() as db:
            bound = space.get(db, 'identity', '')
        if not hmac.compare_digest(bound, hmac.new(space.key_bytes(), owner.encode(), hashlib.sha256).hexdigest()):
            raise ValueError('Source bound identity differs')
        result = export_owner(DATA_ROOT, args.destination, owner, configured_client())
    elif args.operation == 'stage':
        result = stage(args.source, DATA_ROOT, args.destination, owner, download_root())
    else:
        result = commit(args.source, DATA_ROOT, args.recovery, owner)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Authentication/library errors may contain private URLs or identity.
        print(json.dumps({'migration_failed': type(exc).__name__, 'changes_require_review': True}))
        raise SystemExit(1) from None
