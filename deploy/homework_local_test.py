"""Run one explicitly selected local assignment through the real worker, to its next human gate."""
import argparse
import json
import threading
from pathlib import Path

from sustech_dashboard.authentication import load_owner_credentials
from sustech_dashboard.core import load_json
from sustech_dashboard.homework_backend import toolchain
from sustech_dashboard.homework_engine import HomeworkEngine
from sustech_dashboard.locking import exclusive_file
from sustech_dashboard.paths import DATA_ROOT
from sustech_dashboard.provider import Blackboard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--course-id', required=True)
    parser.add_argument('--content-id', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cmd-shell', action='store_true')
    args = parser.parse_args()
    snapshot = lambda: load_json(DATA_ROOT / 'snapshot.json', {})
    match = next((x for x in snapshot().get('assignments', [])
                  if x['course_id'] == args.course_id and x['content_id'] == args.content_id), None)
    if not match:
        raise ValueError('The selected assignment is not in the current local snapshot')
    runner = HomeworkEngine(DATA_ROOT / 'homework', snapshot, Blackboard, threading.Lock())
    with exclusive_file(runner.root / 'worker.lock'):
        if not load_owner_credentials():
            raise ValueError('Open the campus panel and complete its existing login first')
        chain = toolchain(runner.settings())
        if not all(chain.values()):
            raise ValueError('Local Codex, Python and Tectonic must all be available')
        settings = runner.settings() | chain
        if args.cmd_shell:
            settings['shell_hint'] = ('PowerShell startup is unavailable on this machine. Every exec_command '
                'must set shell to C:/Windows/System32/cmd.exe and login=false. Use Python for file operations. '
                'Do not wrap cmd.exe inside PowerShell.')
        runner.store.set_setting('runner', settings)
        runner.enable(True)
        job = runner.store.enqueue(match, explicit=True)
        runner.store.recover()
        if job['state'] == 'failed':
            job = runner.store.decide(job['id'], job['revision'], 'retry')
        args.report.parent.mkdir(parents=True, exist_ok=True)
        while True:
            job = runner.store.get(job['id'])
            report = {key: job[key] for key in ('id', 'name', 'state', 'stage', 'revision', 'error')}
            report.update(plan=job['data'].get('plan'), workspace=str(runner.workspace(job)))
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({key: report[key] for key in ('id', 'state', 'stage') }), flush=True)
            if job['state'] not in {'queued', 'running'}:
                return 1 if job['state'] in {'failed', 'needs_attention'} else 0
            runner.step()


if __name__ == '__main__':
    raise SystemExit(main())
