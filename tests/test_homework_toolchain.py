import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sustech_dashboard import homework_backend as backend


@pytest.mark.parametrize('available', ['configured', 'environment', 'path', 'registered', None])
def test_missing_saved_paths_fall_through_to_current_runtime(tmp_path, monkeypatch, available):
    paths = {name: tmp_path / name for name in ('configured', 'environment', 'path', 'registered')}
    if available:
        paths[available].write_bytes(b'executable fixture')
    monkeypatch.setenv('CODEX_TECTONIC_PATH', str(paths['environment']))
    monkeypatch.setattr(backend.shutil, 'which', lambda name: str(paths['path']) if name == 'tectonic' else None)
    discovered = []
    def installed():
        discovered.append(True)
        return backend.existing_executable([paths['registered']])
    monkeypatch.setattr(backend, 'installed_codex_tectonic', installed)
    chain = backend.toolchain({'python': sys.executable, 'tectonic': str(paths['configured'])})
    assert chain['tectonic'] == (str(paths[available]) if available else None)
    assert bool(discovered) == (available in ('registered', None))


def test_existing_configured_runtime_keeps_priority(tmp_path, monkeypatch):
    explicit, environment = tmp_path / 'explicit', tmp_path / 'environment'
    explicit.write_bytes(b'fixture'); environment.write_bytes(b'fixture')
    monkeypatch.setenv('CODEX_TECTONIC_PATH', str(environment))
    monkeypatch.setattr(backend, 'installed_codex_tectonic', lambda: pytest.fail('unneeded app query'))
    assert backend.toolchain({'tectonic': str(explicit)})['tectonic'] == str(explicit)
    explicit.unlink()
    assert backend.toolchain({'tectonic': str(explicit)})['tectonic'] == str(environment)


def test_silent_child_is_terminated_and_can_be_retried(tmp_path):
    from sustech_dashboard.homework_recovery import StageFailure
    with pytest.raises(StageFailure, match='没有新输出') as caught:
        backend.command([sys.executable, '-c', 'import time; time.sleep(30)'],
                        tmp_path, tmp_path / 'idle.log', timeout=10, idle_timeout=.1)
    assert caught.value.kind == 'retry'


def test_output_progress_prevents_idle_timeout(tmp_path):
    backend.command([sys.executable, '-u', '-c',
        'import time\nfor i in range(8):\n print(i, flush=True)\n time.sleep(.2)'],
        tmp_path, tmp_path / 'progress.log', timeout=10, idle_timeout=.8)
    assert '7' in (tmp_path / 'progress.log').read_text()


@pytest.mark.parametrize('response', ['valid', 'failure', 'timeout', 'invalid'])
def test_registered_app_discovery_is_bounded_and_handles_missing_registration(tmp_path, monkeypatch, response):
    target = tmp_path / 'app/resources/tectonic/tectonic.exe'
    target.parent.mkdir(parents=True); target.write_bytes(b'fixture')
    monkeypatch.setattr(backend, 'os', SimpleNamespace(name='nt', environ={'SystemRoot': str(tmp_path)}))
    def run(args, **kwargs):
        assert '-NonInteractive' in args and 'Get-AppxPackage -Name OpenAI.Codex' in args[-1]
        assert kwargs['timeout'] == 20 and kwargs['creationflags'] == 123
        if response == 'timeout':
            raise subprocess.TimeoutExpired(args, 20)
        return SimpleNamespace(returncode=1 if response == 'failure' else 0,
            stdout='not JSON' if response == 'invalid' else json.dumps(str(tmp_path)))
    monkeypatch.setattr(backend, 'subprocess', SimpleNamespace(run=run, CREATE_NO_WINDOW=123,
        TimeoutExpired=subprocess.TimeoutExpired))
    assert backend.installed_codex_tectonic() == (str(target) if response == 'valid' else None)
