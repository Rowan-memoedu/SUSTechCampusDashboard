import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
migration = importlib.import_module('migrate_local')
from sustech_dashboard.core import load_json, save_json
from sustech_dashboard.actions import Actions
from sustech_dashboard.materials_store import MaterialsStore
from sustech_dashboard.locking import exclusive_file, InstanceBusy


@pytest.fixture
def package(tmp_path):
    source, local, download = [tmp_path / p for p in ('hosted', 'local', 'downloads')]
    for path in (source, local, download):
        path.mkdir()
    save_json(source / 'snapshot.json', {'updated_at': '2026-10-07', 'courses': [{'_metadata_ref': {'opaque': 'owner-only'}, 'personal': 'keep'}]})
    save_json(source / 'attachments.json', {'baseline_at': 'original', 'seen': ['remote-key']})
    save_json(local / 'attachments.json', {'baseline_at': 'local-original', 'seen': ['local-key']})
    (source / 'credentials.dpapi.json').write_text('never export')
    (local / 'credentials.dpapi.json').write_text('keep local credential')
    store = MaterialsStore(source / 'materials.sqlite3')
    store.enqueue(['remote-key'], 'pending fixture')
    with store.connect(True) as db:
        db.execute("INSERT INTO seen VALUES ('remote-key')")
    ledger = Actions(source / 'actions.sqlite3')
    with ledger.connect() as db:
        db.execute("INSERT INTO actions VALUES ('action-id','assignment-id','sending',NULL,1)")
    class Client:
        def call(self, operation, **payload):
            assert operation == 'resolve'
            return [{'id': 'course-id', 'name': 'Course'}]
    export = tmp_path / 'export'
    migration.export_owner(source, export, 'fixture-owner', Client())
    return source, export, local, download


def test_owner_isolation_and_invalid_reference_stop_before_writes(package, tmp_path):
    _, export, local, download = package
    with pytest.raises(ValueError, match='another campus identity'):
        migration.stage(export, local, tmp_path / 'rejected', 'different-owner', download)
    assert not (tmp_path / 'rejected').exists()
    assert not (export / 'credentials.dpapi.json').exists()
    assert migration.standalone(load_json(export / 'snapshot.json', {}))
    (export / 'snapshot.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity'):
        migration.stage(export, local, tmp_path / 'tampered', 'fixture-owner', download)


def test_real_files_baseline_and_uncertain_receipts_preserved_without_replay(package, tmp_path):
    _, export, local, download = package
    (download / 'kept.pdf').write_bytes(b'original bytes')
    receipt = {'key': 'local-key', 'path': 'kept.pdf', 'status': 'saved', 'size': 14,
               'sha256': hashlib.sha256(b'original bytes').hexdigest(), 'source_version': '1'}
    save_json(local / 'downloaded-files.json', {'local-key': receipt})
    staged = tmp_path / 'staged'
    result = migration.stage(export, local, staged, 'fixture-owner', download)
    assert result['inventory']['saved'] == 1
    assert load_json(staged / 'attachments.json', {}) == {'baseline_at': 'local-original', 'seen': ['local-key', 'remote-key']}
    with sqlite3.connect(staged / 'materials.sqlite3') as db:
        assert db.execute('SELECT state FROM jobs').fetchone()[0] == 'needs_review'
        assert db.execute("SELECT value FROM settings WHERE key='auto'").fetchone()[0] == 'false'
    with sqlite3.connect(staged / 'actions.sqlite3') as db:
        assert db.execute('SELECT state FROM actions').fetchone()[0] == 'needs_review'
    migration.commit(staged, local, tmp_path / 'recovery', 'fixture-owner')
    assert (local / 'credentials.dpapi.json').read_text() == 'keep local credential'
    assert (download / 'kept.pdf').read_bytes() == b'original bytes'
    assert load_json(local / 'snapshot.json', {})['courses'][0]['personal'] == 'keep'


def test_running_instance_or_changed_original_blocks_commit(package, tmp_path):
    _, export, local, download = package
    staged = tmp_path / 'staged'
    migration.stage(export, local, staged, 'fixture-owner', download)
    with exclusive_file(local / 'backend.lock'), pytest.raises(InstanceBusy):
        migration.commit(staged, local, tmp_path / 'recovery-busy', 'fixture-owner')
    save_json(local / 'attachments.json', {'seen': ['new']})
    with pytest.raises(ValueError, match='changed'):
        migration.commit(staged, local, tmp_path / 'recovery-stale', 'fixture-owner')
    assert not (tmp_path / 'recovery-stale').exists()


def test_independent_canary_modified_and_missing_files_never_claim_saved(tmp_path):
    source, local, files = [tmp_path / p for p in ('hosted', 'target', 'files')]
    for p in (source, local, files): p.mkdir()
    (files / 'edited.txt').write_text('edits')
    receipts = {key: {'key': key, 'path': name, 'status': 'saved', 'size': 5, 'sha256': 'f'*64}
                for key, name in [('edited', 'edited.txt'), ('missing', 'missing.txt'), ('outside', '../outside.txt')]}
    save_json(source / 'downloaded-files.json', receipts)
    export = tmp_path / 'export'
    migration.export_owner(source, export, 'second-owner', None)
    result = migration.stage(export, local, tmp_path / 'stage', 'second-owner', files)
    assert result['inventory'] == {'saved': 0, 'existing': 0, 'missing': 2, 'modified': 1}


def test_expired_shared_authorization_prevents_export(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    save_json(source / 'snapshot.json', {'courses': [{'_metadata_ref': {'opaque': 'expired'}}]})
    class Refused:
        def call(self, *args, **kwargs): raise ValueError('expired')
    with pytest.raises(ValueError, match='expired'):
        migration.export_owner(source, tmp_path / 'export', 'owner', Refused())
    assert not (tmp_path / 'export/migration.json').exists()


def test_existing_local_database_handle_closes_before_atomic_replacement(package, tmp_path):
    _, export, local, download = package
    existing = MaterialsStore(local / 'materials.sqlite3')
    with existing.connect(True) as db:
        db.execute("INSERT INTO seen VALUES ('already-seen')")
    staged = tmp_path / 'stage-existing'
    migration.stage(export, local, staged, 'fixture-owner', download)
    migration.commit(staged, local, tmp_path / 'recovery-existing', 'fixture-owner')
    with existing.connect() as db:
        assert db.execute("SELECT 1 FROM seen WHERE key='already-seen'").fetchone()


def test_partial_replacement_failure_restores_originals(package, tmp_path, monkeypatch):
    _, export, local, download = package
    existing = MaterialsStore(local / 'materials.sqlite3')
    staged = tmp_path / 'stage-failure'
    migration.stage(export, local, staged, 'fixture-owner', download)
    before = {name: migration.fingerprint(local / name) if (local / name).exists() else None for name in migration.FILES}
    replace = migration.os.replace
    def interrupted(source, target):
        if Path(target).name == 'materials.sqlite3' and Path(source).name.endswith('.migration-new'):
            raise RuntimeError('Injected I/O failure')
        return replace(source, target)
    monkeypatch.setattr(migration.os, 'replace', interrupted)
    with pytest.raises(RuntimeError, match='Injected'):
        migration.commit(staged, local, tmp_path / 'recovery-failure', 'fixture-owner')
    after = {name: migration.fingerprint(local / name) if (local / name).exists() else None for name in migration.FILES}
    assert before == after
