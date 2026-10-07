"""Operator bootstrap is retired; personal-server configuration remains usable."""
import json
from types import SimpleNamespace

import pytest

from sustech_dashboard import pairing, download_agent, protocol, runtime


@pytest.mark.parametrize('path', ['/app/', '/campus/', '/spaces/' + 'f'*24])
def test_operator_pair_is_rejected_before_decryption_and_network(monkeypatch, tmp_path, path):
    config = tmp_path / 'cloud-access.dpapi.json'
    original = json.dumps({'url': 'https://124.221.144.155' + path, 'token_dpapi': 'keep-ciphertext'})
    config.write_text(original)
    monkeypatch.setattr(download_agent, 'CONFIG', config)
    monkeypatch.setattr(download_agent, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(download_agent, 'unprotect_password', lambda *_: pytest.fail('must not decrypt'))
    monkeypatch.setattr(download_agent.requests, 'Session', lambda: pytest.fail('must not connect'))
    assert not download_agent.personal_pair_available()
    with pytest.raises(ValueError, match='托管已停用'):
        download_agent.cloud_session()
    assert config.read_text() == original


def test_personal_server_configuration_is_preserved(monkeypatch, tmp_path):
    config = tmp_path / 'pair.json'
    config.write_text(json.dumps({'url': 'https://personal.example/campus',
        'username': 'owner', 'password_dpapi': 'private'}))
    monkeypatch.setattr(download_agent, 'CONFIG', config)
    monkeypatch.setattr(download_agent, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(download_agent, 'unprotect_password', lambda *_: 'fixture-password')
    assert download_agent.personal_pair_available()
    base, session = download_agent.cloud_session()
    assert base == 'https://personal.example/campus'
    assert session.auth == ('owner', 'fixture-password')
    session.close()


def test_retired_pairing_never_bootstraps_credentials():
    for call, args in [(pairing.connect_ticket, ('https://124.221.144.155/spaces/'+'f'*24, 't'*43)),
                       (pairing.connect, ('https://124.221.144.155/app/', 'owner', 'password'))]:
        with pytest.raises(ValueError, match='托管已停用'):
            call(*args)


@pytest.mark.parametrize('uri', [
    'sustech-campus://login#server=x&key=y', 'sustech-campus://connect#server=x&ticket=y',
    'sustech-campus://open?url=https://evil.example', 'sustech-campus://open#path=D:/private',
    'sustech-campus://open/../../file', 'sustech-campus://open --agent', 'sustech-campus://open%00',
    'sustech-campus://open#', 'sustech-campus://open?', 'sustech-campus://user@open'])
def test_invalid_protocol_never_launches_or_connects(monkeypatch, uri):
    monkeypatch.setattr(runtime, 'supervise', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(ValueError):
        protocol.handle_uri(uri)


def test_open_protocol_only_calls_local_supervisor(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, 'supervise', lambda *a, **k: calls.append((a, k)) or 0)
    assert protocol.handle_uri('sustech-campus://open', 18777) == 0
    assert calls == [((18777,), {'open_browser': True})]


def test_duplicate_start_proves_local_owner_before_opening(monkeypatch, tmp_path):
    from sustech_dashboard.locking import exclusive_file
    monkeypatch.setattr(runtime, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(runtime, 'prepare_private_directory', lambda: None)
    opened = []
    monkeypatch.setattr(runtime.webbrowser, 'open', opened.append)
    monkeypatch.setattr(runtime, 'wait_existing', lambda *a: False)
    with exclusive_file(tmp_path / 'client.lock'):
        with pytest.raises(RuntimeError):
            runtime.supervise(18778)
    assert not opened
    monkeypatch.setattr(runtime, 'wait_existing', lambda *a: True)
    with exclusive_file(tmp_path / 'client.lock'):
        assert runtime.supervise(18778) == 0
    assert opened[0].startswith('http://127.0.0.1:18778/#access=')
