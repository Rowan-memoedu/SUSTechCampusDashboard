import importlib.util
import json
from pathlib import Path


def test_retention_only_removes_verified_expired_task_backups(tmp_path, monkeypatch):
    deploy = Path(__file__).resolve().parents[1]/'deploy'
    monkeypatch.syspath_prepend(str(deploy))
    spec = importlib.util.spec_from_file_location('campus_retention_canary', deploy/'hosted_upgrade.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now = 2_000_000_000
    for name, verified, created in [('hosted-expired', True, now-8*86400), ('hosted-current', True, now-86400),
                                    ('hosted-incomplete', False, now-8*86400), ('unrelated', True, now-8*86400)]:
        directory = tmp_path/name
        directory.mkdir()
        (directory/'fleet.json').write_text(json.dumps({'verified': verified, 'created': created}))
        (directory/'recovery-state').write_text('private fixture')
    assert module.collect_directory(tmp_path, 'fleet.json', 'hosted-', now) == 1
    assert not (tmp_path/'hosted-expired').exists()
    assert all((tmp_path/name/'recovery-state').read_text() == 'private fixture'
               for name in ['hosted-current', 'hosted-incomplete', 'unrelated'])
