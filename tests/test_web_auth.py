import base64
import json
import importlib
from pathlib import Path
import re

import pytest
from werkzeug.security import generate_password_hash

from sustech_dashboard import app as dashboard, web_auth

URL = "https://dashboard.test/campus/"
HEADERS = {"X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/campus", "Origin": "https://dashboard.test"}


@pytest.fixture
def site(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "campus-web").write_text(json.dumps({
        "username": "owner", "password_hash": generate_password_hash("fixture-only-password")}))
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    monkeypatch.setenv("SUSTECH_BROWSER_LOGIN", "1")
    monkeypatch.setenv("SUSTECH_PUBLIC_HOST", "dashboard.test")
    monkeypatch.setattr(dashboard, "CLOUD", True)
    monkeypatch.setattr(dashboard, "SNAPSHOT_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr(web_auth, "DATA_ROOT", tmp_path / "data")
    return dashboard.create_app


def request(client, method, path="/", **kwargs):
    headers = {**HEADERS, **kwargs.pop("headers", {})}
    return getattr(client, method)(path, base_url=URL, headers=headers, **kwargs)


def login(client, remember=True, **kwargs):
    page = request(client, "get", "/auth/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    data = {"username": "owner", "password": "fixture-only-password", "csrf": csrf}
    if remember:
        data["remember"] = "on"
    data.update(kwargs)
    return request(client, "post", "/auth/login", data=data)


def basic(password="fixture-only-password"):
    return {"Authorization": "Basic " + base64.b64encode(("owner:" + password).encode()).decode()}


def test_no_private_pages_or_data_without_login(site):
    client = site().test_client()
    page = request(client, "get")
    assert page.status_code == 302 and page.location == "/campus/auth/login"
    response = request(client, "get", "/api/status")
    assert response.status_code == 401
    assert "WWW-Authenticate" not in response.headers
    assert request(client, "get", "/auth/login").status_code == 200


def test_nginx_auth_gate_only_exempts_login_and_public_css(site):
    client = site().test_client()
    assert request(client, "get", "/auth/verify").status_code == 401
    assert request(client, "get", "/auth/verify", headers={"X-Campus-Original-URI": "/campus/auth/login"}).status_code == 204
    assert request(client, "get", "/auth/verify", headers={"X-Campus-Original-URI": "/campus/api/status"}).status_code == 401
    assert request(client, "get", "/auth/verify", headers=basic()).status_code == 204
    assert client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/") is None
    login(client)
    assert request(client, "get", "/auth/verify").status_code == 204


def test_persistent_cookie_survives_browser_and_server_restart(site):
    client = site().test_client()
    result = login(client)
    assert result.status_code == 303
    cookie = result.headers.getlist("Set-Cookie")[0]
    assert all(flag in cookie for flag in ("Max-Age=2592000", "Secure", "HttpOnly", "SameSite=Lax", "Path=/campus/"))
    assert "fixture-only-password" not in cookie and "owner" not in cookie
    saved = client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/").value
    restarted = site().test_client()
    restarted.set_cookie(web_auth.COOKIE, saved, domain="dashboard.test", path="/campus/")
    assert request(restarted, "get").status_code == 200
    assert request(restarted, "get", "/api/status").status_code == 200
    assert saved.encode() not in (web_auth.DATA_ROOT / "browser-sessions.sqlite3").read_bytes()


def test_logout_revokes_copied_cookie_and_suppresses_cached_basic(site):
    client = site().test_client()
    login(client)
    saved = client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/").value
    page = request(client, "get")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert request(client, "post", "/auth/logout", data={"csrf": csrf}).status_code == 303
    assert request(client, "get", headers=basic()).status_code == 302
    replay = site().test_client()
    replay.set_cookie(web_auth.COOKIE, saved, domain="dashboard.test", path="/campus/")
    assert request(replay, "get", "/api/status").status_code == 401


def test_basic_agent_and_browser_migration_keep_existing_password(site):
    client = site().test_client()
    assert request(client, "get", "/api/status", headers=basic()).status_code == 200
    assert client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/") is None
    assert request(client, "get", headers=basic()).status_code == 200
    assert client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/") is not None
    assert request(client, "get").status_code == 200


def test_expired_and_forged_cookies_rejected(site, monkeypatch):
    client = site().test_client()
    login(client)
    original = web_auth.time.time
    with monkeypatch.context() as patch:
        patch.setattr(web_auth.time, "time", lambda: original() + web_auth.REMEMBER_SECONDS + 1)
        assert request(client, "get", "/api/status").status_code == 401
    client.set_cookie(web_auth.COOKIE, "x" * 43, domain="dashboard.test", path="/campus/")
    assert request(client, "get", "/api/status").status_code == 401


def test_password_change_invalidates_existing_sessions(site, monkeypatch):
    client = site().test_client()
    login(client)
    saved = client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/").value
    path = web_auth.Path(web_auth.os.environ["CREDENTIALS_DIRECTORY"]) / "campus-web"
    path.write_text(json.dumps({"username": "owner", "password_hash": generate_password_hash("different-fixture")}))
    changed = site().test_client()
    changed.set_cookie(web_auth.COOKIE, saved, domain="dashboard.test", path="/campus/")
    assert request(changed, "get", "/api/status").status_code == 401


@pytest.mark.parametrize("case", ["origin", "csrf", "unicode", "password"])
def test_bad_login_does_not_create_session(site, case):
    client = site().test_client()
    page = request(client, "get", "/auth/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    data = {"username": "owner", "password": "fixture-only-password", "csrf": csrf, "remember": "on"}
    headers = {}
    if case == "origin":
        headers["Origin"] = "https://other.test"
    elif case == "csrf":
        data["csrf"] = "wrong"
    elif case == "unicode":
        data["csrf"] = "非 ASCII"
    else:
        data["password"] = "wrong"
    result = request(client, "post", "/auth/login", data=data, headers=headers)
    assert result.status_code == (401 if case == "password" else 403)
    assert client.get_cookie(web_auth.COOKIE, domain="dashboard.test", path="/campus/") is None


def test_temporary_cookie_and_rate_limit(site):
    client = site().test_client()
    response = login(client, remember=False)
    assert "Max-Age" not in response.headers.getlist("Set-Cookie")[0]
    assert "Expires" not in response.headers.getlist("Set-Cookie")[0]
    attacker = site().test_client()
    for _ in range(8):
        assert request(attacker, "get", "/api/status", headers=basic("wrong")).status_code == 401
    assert request(attacker, "get", "/api/status", headers=basic("wrong")).status_code == 429


def test_activation_requires_verified_new_runtime_before_ssh_or_credentials(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "deploy"))
    owner = importlib.import_module("owner_browser_login")
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"version": "0.1.0"}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
        @property
        def auth(self):
            raise AssertionError("Must not access credentials before runtime readiness")
    monkeypatch.setattr(owner.subprocess, "run", lambda *a, **kw: pytest.fail("SSH must not run"))
    with pytest.raises(RuntimeError, match="Finish and verify"):
        owner.enable("https://dashboard.test/campus", Session())
