"""Deliberately fail an operator rollout in disposable spaces and verify recovery."""
import json
from pathlib import Path
import sqlite3

from hosted_admin import REGISTRY, ROOT
from hosted_upgrade import upgrade, healthy


def state(sid):
    result = {}
    for path in (ROOT/sid).rglob('*.sqlite3'):
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            result[str(path.relative_to(ROOT/sid))] = list(db.iterdump())
    return result


def verify(broken):
    records = {k:v for k,v in json.loads(REGISTRY.read_text()).items() if v['enabled']}
    if len(records) != 2:
        raise ValueError('This acceptance run requires exactly two disposable unbound spaces')
    from sustech_dashboard.hosted import Space
    for sid in records:
        with Space(ROOT/sid).db() as db:
            if Space.get(db, 'identity'):
                raise ValueError('Never run the failure canary on a bound campus account')
    before = {sid: state(sid) for sid in records}
    try:
        upgrade(broken)
        raise AssertionError('Broken runtime was accepted')
    except RuntimeError as exc:
        if 'previous program restored' not in str(exc):
            raise
    assert all(healthy(record, sid) for sid, record in records.items())
    assert before == {sid: state(sid) for sid in records}
    print(json.dumps({'failed_upgrade_recovered': True, 'all_sqlite_state_unchanged': True, 'school_writes_replayed': 0}))


if __name__ == '__main__':
    import sys
    verify(Path(sys.argv[1]))
