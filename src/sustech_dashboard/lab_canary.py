"""Verify the packaged Lab worker on a frozen sample without school or IDEA access."""
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from . import __version__
from .homework_backend import confined
from .homework_engine import save
from .lab_engine import LabEngine
from .paths import DATA_ROOT, default_data_root


def verify_fixture(manifest):
    if 'SUSTECH_DASHBOARD_DATA_ROOT' not in os.environ or DATA_ROOT == default_data_root().resolve():
        raise ValueError('Frozen Lab verification requires an explicit isolated data directory')
    manifest = Path(manifest).resolve(strict=True)
    fixture = json.loads(manifest.read_text(encoding='utf-8'))
    assignment = fixture['assignment']
    worker = fixture['mac_worker']
    if assignment['oj'] or not all(worker.get(key) for key in ('host', 'python')):
        raise ValueError('Use a non-OJ sample and a configured Mac worker')
    for source in (assignment['pdf'], *assignment.get('source_images', [])):
        source['path'] = str(confined(manifest.parent, source['path']))

    def no_device_access(*_args):
        raise AssertionError('Frozen Lab verification cannot access school or IDEA')

    root = DATA_ROOT / 'lab-verification'
    root.mkdir(parents=True, exist_ok=False)
    shared = SimpleNamespace(snapshot=no_device_access, settings=lambda: {'backend': 'codex'})
    engine = LabEngine(root, shared, no_device_access, threading.Lock())
    engine.store.set_setting('configuration', {
        'enabled': True, 'java_home': fixture['java_home'], 'mac_worker': worker,
    })
    engine.next_scan = float('inf')
    engine.publish = engine.connect = no_device_access
    job = engine.store.enqueue(assignment)
    deadline = time.monotonic() + 1200
    while job['stage'] != 'publish' and job['state'] not in ('needs_attention', 'failed'):
        if time.monotonic() >= deadline:
            break
        worked = engine.step()
        refreshed = engine.store.get(job['id'])
        if refreshed['revision'] != job['revision']:
            print('LAB_VERIFY', refreshed['state'], refreshed['stage'], flush=True)
        job = refreshed
        if not worked:
            time.sleep(5)
    data = job['data']
    if job['stage'] != 'publish' or not data.get('verified_sha') or data['verified_sha'] != data['candidate_sha']:
        raise RuntimeError(f"Frozen Lab verification stopped at {job['state']} / {job['stage']}")
    workspace = engine.workspace(job)
    receipts = []
    for path in (workspace / 'agent-runs').glob('mac-*.json'):
        if path.stat().st_size:
            reply = json.loads(path.read_bytes())
            receipts.append({key: reply[key] for key in ('stage', 'input_sha256')})
    if {item['stage'] for item in receipts} != {'lab-generate', 'lab-independent-review'}:
        raise RuntimeError('Missing Mac generation or independent review receipt')
    candidate = json.loads((workspace / 'candidate.json').read_bytes())
    save(root / 'result.json', {
        'version': __version__, 'mac_worker': worker['host'], 'mac_receipts': receipts,
        'java_runs': sum(len(task['runs']) for task in candidate['tasks']),
        'verified_candidate_sha256': data['verified_sha'], 'school_or_idea_calls': 0,
    })
