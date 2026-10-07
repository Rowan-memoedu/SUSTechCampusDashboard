import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


def module(monkeypatch, tmp_path):
    deploy = Path(__file__).resolve().parents[1]/'deploy'
    monkeypatch.syspath_prepend(str(deploy))
    spec = importlib.util.spec_from_file_location('reset_connections', deploy/'reset_connections.py')
    reset = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reset)
    reset.ROOT = tmp_path/'spaces'
    reset.RESET_ROOT = tmp_path/'recovery'
    reset.ENTRY_ROOT = tmp_path/'entry'
    reset.REGISTRY = tmp_path/'registry.json'
    monkeypatch.setattr(reset.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(reset.shutil, 'chown', lambda *a, **k: None)
    reset.revoked = []
    monkeypatch.setattr(reset, 'revoke_metadata', reset.revoked.append)
    return reset


def test_reset_revokes_credentials_and_sessions_preserving_web_account_and_other_spaces(monkeypatch, tmp_path):
    reset = module(monkeypatch, tmp_path)
    sid, other = 'a'*24, 'b'*24
    reset.REGISTRY.write_text(json.dumps({sid: {'enabled': True}, other: {'enabled': True}}))
    root = reset.ROOT/sid
    with reset.Space(root).db() as db:
        for k, v in dict(web={'username':'member','password_hash':'fixture'}, enabled=True, identity='bound', cas='encrypted-fixture').items():
            reset.Space.put(db, k, v)
    (root/'upstream').mkdir()
    (root/'upstream/session').write_bytes(b'private fixture')
    (root/'browser-sessions.sqlite3').write_bytes(b'fixture')
    sibling = reset.ROOT/other
    sibling.mkdir()
    (sibling/'state').write_bytes(b'unchanged')
    report = reset.reset(sid, reset.RESET_ROOT/'once')
    assert report['recovery_verified'] and report['cas_removed']
    with reset.Space(root).db() as db:
        assert reset.Space.get(db, 'web')['password_hash'] == 'fixture'
        assert reset.Space.get(db, 'identity') == 'bound'
        assert reset.Space.get(db, 'cas') is None
    assert {p.name for p in root.iterdir()} == {'space.sqlite3'}
    assert (reset.RESET_ROOT/'once/space/upstream/session').read_bytes() == b'private fixture'
    assert (sibling/'state').read_bytes() == b'unchanged'
    assert reset.revoked == [sid]
    with pytest.raises(ValueError):
        reset.reset(sid, tmp_path/'outside')


def test_reset_refuses_to_erase_uncertain_print_operations(monkeypatch, tmp_path):
    reset = module(monkeypatch, tmp_path)
    sid = 'a'*24
    reset.REGISTRY.write_text(json.dumps({sid: {'enabled': True}}))
    root = reset.ROOT/sid
    (root/'print-relay').mkdir(parents=True)
    with sqlite3.connect(root/'print-relay/relay.sqlite3') as db:
        db.execute('CREATE TABLE operations(kind TEXT,state TEXT)')
        db.execute("INSERT INTO operations VALUES ('upload','needs_review')")
    with pytest.raises(ValueError, match='Unresolved'):
        reset.reset(sid, reset.RESET_ROOT/'once')
    assert (root/'print-relay/relay.sqlite3').exists()
    assert not reset.RESET_ROOT.exists()
