"""Resume one owned homework repair under the original worker lock, to a human gate.

This is a maintenance entry point into HomeworkEngine, not a queue or scheduler.
Drain the installed backend first; supply the actual regression/canary receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import threading

from sustech_dashboard.authentication import load_owner_credentials
from sustech_dashboard.homework_engine import HomeworkEngine, StageYield, save
from sustech_dashboard.homework_recovery import classify
from sustech_dashboard.locking import exclusive_file
from sustech_dashboard.provider import Blackboard


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True, help='Instance data root')
    p.add_argument('--job', required=True)
    p.add_argument('--owner', required=True)
    p.add_argument('--revision', type=int, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    source = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=source, check=True, capture_output=True)
    receipt = json.loads(a.receipt.read_text(encoding='utf-8'))
    if receipt['source_commit'] != commit:
        raise ValueError('Validation receipt does not match fixed production source')
    engine = HomeworkEngine(a.root / 'homework', lambda: {}, Blackboard, threading.Lock())
    with exclusive_file(a.root / 'backend.lock'), exclusive_file(engine.root / 'worker.lock'):
        if not load_owner_credentials():
            raise ValueError('Existing owner login is unavailable')
        engine.store.unblock(a.job, a.revision, a.owner, receipt={
            'path': str(a.receipt.resolve()), 'sha256': hashlib.sha256(a.receipt.read_bytes()).hexdigest(),
            'source_commit': commit})
        engine.cooperative = True
        while True:
            job = engine.store.get(a.job)
            report = {k: job[k] for k in ('id', 'name', 'state', 'stage', 'revision', 'error')}
            report.update(source_commit=commit, workspace=str(engine.workspace(job)),
                          pipeline=job['data'].get('pipeline', {}), artifacts=job['data'].get('artifacts', []))
            save(a.report, report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if job['state'] not in {'queued', 'running'}:
                return 1 if job['state'] in {'retry_wait', 'failed', 'needs_attention'} else 0
            if job['stage'] not in {'discover', 'extract', 'produce'}:
                raise ValueError('Unexpected production stage')
            job = engine.store.update(a.job, state='running')
            try:
                getattr(engine, job['stage'])(job)
            except StageYield:
                continue
            except Exception as exc:
                current = engine.store.get(a.job)
                kind, phase = classify(current, exc)
                # Detailed local error remains in the private maintenance report.
                save(a.report.with_suffix('.failure.json'), {'type': type(exc).__name__, 'error': str(exc),
                                                           'source_commit': commit, 'phase': phase})
                error = str(exc)[:5000] if isinstance(exc, ValueError) else f'{type(exc).__name__}：当前阶段未完成，材料已保留'
                engine.store.schedule_recovery(a.job, error, kind, phase)


if __name__ == '__main__':
    raise SystemExit(main())
