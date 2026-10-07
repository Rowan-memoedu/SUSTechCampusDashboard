import os
import re
import threading
import time

import pytest

from sustech_dashboard import app, authentication, runtime
from sustech_dashboard.core import save_json, load_json, attachment_key


@pytest.fixture
def instance(monkeypatch, tmp_path):
    monkeypatch.delenv("SUSTECH_CLOUD", raising=False)
    monkeypatch.setattr(app, "CLOUD", False)
    monkeypatch.setattr(runtime, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(authentication, "load_owner_credentials", lambda: False)
    monkeypatch.setattr(app, "SNAPSHOT_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr(app, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(app, "MATERIALS_DB", tmp_path / "materials.sqlite3")
    rt = runtime.Runtime()
    client = app.create_app(rt).test_client()
    headers = {"Host": "127.0.0.1:18765", "Origin": "http://127.0.0.1:18765"}
    return rt, client, headers


def unlock(rt, client, headers):
    result = client.post("/auth/unlock", json={"token": rt.token}, headers=headers)
    assert result.status_code == 200
    assert "HttpOnly" in result.headers["Set-Cookie"] and "SameSite=Strict" in result.headers["Set-Cookie"]


def test_no_account_data_before_local_unlock_and_host_origin_validation(instance):
    rt, client, headers = instance
    save_json(app.SNAPSHOT_PATH, {"assignments": [{"private": "canary-secret"}]})
    assert "canary-secret" not in client.get("/", headers=headers).text
    assert client.get("/api/status", headers=headers).status_code == 401
    assert client.get("/api/instance", headers={"Host": "attacker.example"}).status_code == 400
    assert client.post("/auth/unlock", json={"token": rt.token}, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    unlock(rt, client, headers)
    assert client.get("/", headers=headers).location.endswith("/setup")
    rt.configured.set()
    assert client.get("/api/status", headers=headers).json["assignments"][0]["private"] == "canary-secret"


def test_first_login_validates_once_no_account_switch_or_password_echo(instance, monkeypatch):
    rt, client, headers = instance
    unlock(rt, client, headers)
    page = client.get("/setup", headers=headers)
    csrf = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
    payload = {"sid": "test-account", "password": "not-a-real-password", "remember": False}
    calls = []
    monkeypatch.setattr(authentication, "configure_credentials", lambda *args: calls.append(args))
    assert client.post("/api/instance/login", json=payload, headers=headers).status_code == 400
    headers["X-CSRF-Token"] = csrf
    result = client.post("/api/instance/login", json=payload, headers=headers)
    assert result.status_code == 200 and rt.configured.is_set()
    assert rt.sync_requested.is_set()
    assert "not-a-real-password" not in result.text
    assert client.post("/api/instance/login", json=payload, headers=headers).status_code == 409
    assert calls == [("test-account", "not-a-real-password", False)]


def test_operator_pair_is_retired_without_changing_identity(instance, monkeypatch):
    from sustech_dashboard import pairing
    rt, client, headers = instance
    calls = []
    monkeypatch.setattr(pairing, 'connect_ticket', lambda *args: calls.append(args) or {'ok': True})
    payload = {'url':'https://124.221.144.155/spaces/'+'a'*24, 'ticket':'t'*43}
    assert client.post('/api/instance/pair', json=payload, headers=headers).status_code == 401
    unlock(rt, client, headers)
    assert client.post('/api/instance/pair', json=payload, headers=headers).status_code == 400
    assert client.get('/connect', headers=headers).location.endswith('/setup')
    csrf = re.search(r'const roomCsrf="([^"]+)"', client.get('/setup', headers=headers).text)[1]
    assert client.post('/api/instance/pair', json=payload, headers={**headers,'X-CSRF-Token':csrf}).status_code == 410
    assert not rt.configured.is_set() and not rt.sync_requested.is_set() and not calls


def test_login_wakes_existing_sync_wait_instead_of_waiting_half_hour(monkeypatch):
    monkeypatch.setattr(app, 'hosted', lambda: False)
    called = threading.Event()
    calls = []
    def sync():
        calls.append(True);called.set();return {'errors':{}}
    monkeypatch.setattr(app, 'sync_all', sync)
    stop, configured, requested = threading.Event(), threading.Event(), threading.Event()
    configured.set()
    worker = threading.Thread(target=app._sync_loop, args=(stop,configured,requested))
    worker.start()
    try:
        assert called.wait(3)
        called.clear();requested.set()
        assert called.wait(3) and len(calls) >= 2
    finally:
        stop.set();worker.join(3)
    assert not worker.is_alive()


def test_failed_first_sync_retries_after_thirty_seconds(monkeypatch):
    monkeypatch.setattr(app, 'hosted', lambda: False)
    now, calls = [0.0], []
    class ClockStop:
        done = False
        def is_set(self):return self.done
        def wait(self, seconds):now[0] += seconds;return self.done
    stop = ClockStop()
    def sync():
        calls.append(now[0])
        if len(calls) == 2:stop.done = True
        return {'errors':{'blackboard':'network failure'}}
    monkeypatch.setattr(app.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(app, 'sync_all', sync)
    app._sync_loop(stop)
    assert calls == [0,30]


def test_local_material_endpoints_and_retired_monitor_preserved(instance):
    rt, client, headers = instance
    unlock(rt, client, headers)
    rt.configured.set()
    assert client.get("/api/materials", headers=headers).status_code == 200
    assert client.get("/api/room-watch", headers=headers).status_code == 410
    assert client.post("/api/materials/agent", headers={**headers, "X-Campus-Agent": "1"}, json={}).status_code == 403


def test_update_waits_for_active_school_write_and_rejects_new_writes(instance, monkeypatch):
    rt, client, headers = instance
    unlock(rt, client, headers)
    rt.configured.set()
    monkeypatch.setenv("SUSTECH_MANAGED_RUNTIME", "1")
    monkeypatch.setattr(rt.update, "stage", lambda: {})
    rt.active_writes = 1
    rt.begin_install()
    assert rt.stop.wait(2)
    assert not rt.restart
    assert client.post("/api/venues/book", json={}, headers=headers).status_code == 503
    with rt.guard:
        rt.active_writes = 0
        rt.guard.notify_all()
    deadline = time.monotonic() + 2
    while not rt.restart and time.monotonic() < deadline:
        time.sleep(.01)
    assert rt.restart


def test_cas_certificate_checks_remain_enabled_and_private_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(authentication, "DATA_ROOT", tmp_path)
    monkeypatch.setenv("SUSTECH_HOME", "unused")
    monkeypatch.setenv("SUSTECH_CREDENTIALS", "unused")
    authentication.secure_upstream()
    from sustech_survival.sso.providers.cas import CASAuthorizer
    session = CASAuthorizer._build_cas_session(None)
    assert session.verify is True
    from requests.adapters import HTTPAdapter
    assert type(session.get_adapter("https://cas.sustech.edu.cn")) is HTTPAdapter
    assert os.environ["SUSTECH_HOME"] == str(tmp_path / "upstream")
    assert os.environ["SUSTECH_CREDENTIALS"] == str(tmp_path / "disabled-plaintext-credentials")


def test_shared_download_worker_preserves_baseline_and_never_initializes_from_failed_scan(monkeypatch, tmp_path):
    from sustech_dashboard import download_agent as agent
    monkeypatch.setattr(agent, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(agent, "DOWNLOAD_ROOT", tmp_path / "files")
    stop = threading.Event()
    stop.set()
    class Failed:
        manifest = {}
    with pytest.raises(RuntimeError):
        agent.run_agent(Failed, lambda p: {}, stop)
    assert not (tmp_path / "attachments.json").exists()
    class Complete:
        manifest = {"updated_at": "now", "items": [{"course_id": "c", "content_id": "i", "id": "old"}]}
    agent.run_agent(Complete, lambda p: {}, stop)
    first = (tmp_path / "attachments.json").read_bytes()
    assert load_json(tmp_path / "attachments.json", {})["seen"] == ["c/i/old"]
    Complete.manifest["items"].append({"course_id": "c", "content_id": "i", "id": "new"})
    agent.run_agent(Complete, lambda p: {}, stop)
    assert (tmp_path / "attachments.json").read_bytes() == first


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_compatible_with_previous_powershell_storage():
    from sustech_dashboard.dpapi_store import _pwsh, _ENCRYPT, protect_password, unprotect_password
    secret = "fixture-password-中文"
    assert unprotect_password(_pwsh(_ENCRYPT, secret)) == secret
    assert unprotect_password(protect_password(secret)) == secret
