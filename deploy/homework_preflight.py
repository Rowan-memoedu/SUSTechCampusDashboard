"""Bounded local toolchain / Codex canary; no school login or course data."""
import argparse
import json
from pathlib import Path

from sustech_dashboard.homework_backend import CodexBackend, toolchain
from sustech_dashboard.homework_engine import RESULT

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--codex', action='store_true')
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
chain = toolchain()
print(json.dumps(chain), flush=True)
if a.codex:
    import secrets
    sentinel = secrets.token_hex(12)
    (a.output / 'probe.txt').write_text(sentinel, encoding='ascii')
    result = CodexBackend().run(a.output, 'preflight',
        'Read probe.txt in the working directory. Return status ready and notes containing exactly its contents. '
        'This Windows machine has a pwsh startup problem; use exec_command with shell cmd.exe and login false for the read. '
        'Do not modify files, search the web, or read anything outside this workspace.', RESULT)
    assert result['status'] == 'ready' and result['notes'].strip() == sentinel
    print('CODEX_FILE_CANARY_PASS', flush=True)
