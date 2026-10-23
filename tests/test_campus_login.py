import json
import time
from types import SimpleNamespace
import pytest
import requests

from sustech_dashboard import campus_login as login


def auth_with(session):
    return SimpleNamespace(_cas_url='https://cas.sustech.edu.cn/cas/login?service=fixture',
        SERVICE_URL='https://bb.sustech.edu.cn/sso', _headers={},
        _login_timeout=lambda: 1, _build_cas_session=lambda: session)


def response(url, body):
    value = requests.Response()
    value.status_code = 200
    value.url = url
    value._content = body.encode()
    return value


def test_challenge_stops_credentials_and_subsequent_attempts(monkeypatch):
    from sustech_survival.sso.providers.cas import CASAuthorizer
    login.install()
    monkeypatch.setattr(login, '_required', False)
    monkeypatch.setattr(login, '_stored', lambda: [])
    session = requests.Session()
    calls = []
    def get(*a, **k):
        calls.append('GET')
        return response('https://cas.sustech.edu.cn/cas/login', '<input name="g-recaptcha-response" required>')
    monkeypatch.setattr(session, 'get', get)
    monkeypatch.setattr(session, 'post', lambda *a, **k: pytest.fail('CAPTCHA credentials were posted'))
    for _ in range(2):
        with pytest.raises(ValueError, match='人机验证'):
            CASAuthorizer._get_ticket_cookies(auth_with(session), 'fixture', 'secret')
    assert calls == ['GET']


def test_verified_sso_reuses_only_service_cookies(monkeypatch):
    from sustech_survival.sso.providers.cas import CASAuthorizer
    login.install()
    monkeypatch.setattr(login, '_required', False)
    monkeypatch.setattr(login, '_stored', lambda: [])
    session = requests.Session()
    session.cookies.set('TGC', 'cas-secret', domain='cas.sustech.edu.cn')
    session.cookies.set('session', 'bb-secret', domain='.bb.sustech.edu.cn')
    monkeypatch.setattr(session, 'get', lambda *a, **k: response('https://bb.sustech.edu.cn/', 'fixture'))
    monkeypatch.setattr(session, 'post', lambda *a, **k: pytest.fail('SSO reuse posted credentials'))
    assert CASAuthorizer._get_ticket_cookies(auth_with(session), 'fixture', 'secret') == {'session': 'bb-secret'}


def test_session_cache_is_owner_bound_and_expires(monkeypatch, tmp_path):
    from sustech_dashboard import dpapi_store
    monkeypatch.setattr(login, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(login, '_cookies', None)
    monkeypatch.setattr(login, '_owner', lambda: 'owner-a')
    monkeypatch.setattr(dpapi_store, 'unprotect_password', lambda value: value)
    path = tmp_path / 'cas-session.dpapi'
    path.write_text(json.dumps({'owner': 'owner-b', 'until': time.time()+1000, 'cookies': [{'name': 'TGC'}]}))
    assert login._stored() == []
    path.write_text(json.dumps({'owner': 'owner-a', 'until': time.time()-1, 'cookies': [{'name': 'TGC'}]}))
    assert login._stored() == []


def test_rejects_insecure_or_foreign_cookies(monkeypatch):
    monkeypatch.setattr(login, '_owner', lambda: 'owner-a')
    with pytest.raises(ValueError, match='可复用'):
        login.save_session([{'name': 'TGC', 'value': 'fixture', 'domain': 'other.example', 'secure': True},
                            {'name': 'TGC', 'value': 'fixture', 'domain': 'cas.sustech.edu.cn', 'secure': False}])


def test_manual_login_rejects_another_school_account(monkeypatch):
    import contextlib
    import threading
    from sustech_dashboard import authentication, browser_services
    monkeypatch.setattr(authentication, 'current_credentials', lambda: ('owner-a', 'fixture'))
    monkeypatch.setattr(login, '_browser_lock', threading.Lock())
    monkeypatch.setattr(login, '_state', {'state': 'idle', 'message': ''})
    monkeypatch.setattr(login, 'save_session', lambda cookies: pytest.fail('Other owner cookies persisted'))
    page = SimpleNamespace(url='https://bb.sustech.edu.cn/', goto=lambda *a, **k: None, is_closed=lambda: False)
    response = SimpleNamespace(status=200, json=lambda: {'userName': 'owner-b'})
    context = SimpleNamespace(new_page=lambda: page, request=SimpleNamespace(get=lambda *a, **k: response))
    @contextlib.contextmanager
    def browser(**kwargs):
        assert kwargs == {'headless': False}
        yield context
    monkeypatch.setattr(browser_services, 'browser_context', browser)
    runtime = SimpleNamespace(stop=threading.Event(), workers=[], request_sync=lambda: pytest.fail('Wrong owner synchronized'))
    login.begin(runtime)
    runtime.workers[0].join(timeout=3)
    assert not runtime.workers[0].is_alive()
    assert login.status()['state'] == 'required'
    assert '不一致' in login.status()['message']
