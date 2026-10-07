import hashlib
import io
import uuid

import pytest

from sustech_dashboard import print_agent, print_relay, printing
from sustech_dashboard.actions import Actions, mark_sent
from sustech_dashboard.print_relay import PrintRelay
from sustech_dashboard.print_auth import allowed_url

A, B = 'a'*32, 'b'*32


def heartbeat(store, agent=A, **kwargs):
    return store.poll(dict(agent_id=agent, name='Fixture computer', ready=True, claim=False, **kwargs))


@pytest.fixture
def relay(tmp_path):
    store = PrintRelay(tmp_path / 'relay')
    heartbeat(store)
    store.pair(A)
    return store


def claim(store, agent=A):
    return store.poll({'agent_id': agent, 'ready': True, 'claim': True})['job']


def upload(store, **kwargs):
    content = b'%PDF-1.4 test-only'
    return store.enqueue('upload', {'filename': 'fixture.pdf', 'options': printing.options({})},
        token=str(uuid.uuid4()), target=printing.upload_target('fixture.pdf', content, printing.options({})),
        content=content, **kwargs)


def receipt(store, jid, result, agent=A):
    return heartbeat(store, agent, receipt={'id': jid, 'result': result})


def test_only_selected_computer_can_claim_and_fetch(relay):
    queued = upload(relay)
    heartbeat(relay, B)
    assert claim(relay, B) is None
    with pytest.raises(ValueError):
        relay.document(queued['operation_id'], B)
    job = claim(relay)
    assert not job['recovery_only']
    content = relay.document(job['id'], A).read_bytes()
    assert hashlib.sha256(content).hexdigest() == job['payload']['sha256']
    with pytest.raises(ValueError):
        receipt(relay, job['id'], {'state': 'confirmed'}, B)
    with pytest.raises(ValueError):
        relay.pair(B)
    receipt(relay, job['id'], {'state': 'confirmed', 'message': 'Verified'})
    assert not relay.file(job['id']).exists()
    receipt(relay, job['id'], {'state': 'rejected', 'message': 'duplicate receipt'})
    assert relay.operation(job['id'])['state'] == 'confirmed'


def test_single_online_computer_is_selected_without_switching_owner(tmp_path):
    store=PrintRelay(tmp_path/'single')
    heartbeat(store)
    assert store.status()['selected']==A
    heartbeat(store,B)
    assert store.status()['selected']==A
    ambiguous=PrintRelay(tmp_path/'multiple')
    heartbeat(ambiguous,A);heartbeat(ambiguous,B)
    assert ambiguous.status()['selected'] is None


def test_offline_never_returns_empty_queue_or_accepts_upload(relay, monkeypatch):
    original = print_relay.time.time()
    monkeypatch.setattr(print_relay.time, 'time', lambda: original + 46)
    assert not relay.status()['agents'][0]['online']
    with pytest.raises(ValueError, match='离线'):
        upload(relay)
    assert not list(relay.root.glob('*.document'))
    with pytest.raises(ValueError, match='离线'):
        relay.enqueue('overview')


def test_lost_claim_never_replays_write_after_restart(relay, tmp_path, monkeypatch):
    upload(relay)
    job = claim(relay)
    recovered = claim(PrintRelay(relay.root))
    assert recovered['recovery_only']
    monkeypatch.setattr(printing, 'upload', lambda *a: pytest.fail('Must not replay a lost claim'))
    ledger = Actions(tmp_path / 'local.sqlite3')
    assert print_agent.execute(recovered, lambda *a: pytest.fail('Must not refetch'), ledger)['state'] == 'needs_review'
    now = print_relay.time.time()
    monkeypatch.setattr(print_relay.time, 'time', lambda: now + 181)
    assert relay.operation(job['id'])['state'] == 'needs_review'
    assert not relay.file(job['id']).exists()
    receipt(relay, job['id'], {'state': 'confirmed', 'message': 'Recovered local receipt'})
    assert relay.operation(job['id'])['state'] == 'confirmed'


def test_local_ledger_recovers_confirmation_and_uploads_once(relay, tmp_path, monkeypatch):
    upload(relay)
    job = claim(relay)
    content = relay.document(job['id'], A).read_bytes()
    calls = []
    def uploaded(*args):
        mark_sent()
        calls.append(args)
        return {'state': 'confirmed', 'message': 'Verified school receipt'}
    monkeypatch.setattr(printing, 'upload', uploaded)
    path = tmp_path / 'local.sqlite3'
    result = print_agent.execute(job, lambda _: content, Actions(path))
    recovered = dict(job, recovery_only=True)
    assert print_agent.execute(recovered, lambda *a: pytest.fail('Must not refetch'), Actions(path)) == result
    assert len(calls) == 1
    receipt(relay, job['id'], result)


def test_corrupted_file_never_reaches_school(relay, tmp_path, monkeypatch):
    upload(relay)
    monkeypatch.setattr(printing, 'upload', lambda *a: pytest.fail('Corrupted document must not be sent'))
    result = print_agent.execute(claim(relay), lambda _: b'changed', Actions(tmp_path / 'local.sqlite3'))
    assert result['state'] == 'rejected'


def test_uncertain_write_is_locked_and_not_replayed(relay, tmp_path, monkeypatch):
    upload(relay)
    job = claim(relay)
    content = relay.document(job['id'], A).read_bytes()
    calls = []
    def lost(*args):
        mark_sent()
        calls.append(1)
        raise TimeoutError('private auth details')
    monkeypatch.setattr(printing, 'upload', lost)
    ledger = Actions(tmp_path / 'local.sqlite3')
    result = print_agent.execute(job, lambda _: content, ledger)
    assert result['state'] == 'needs_review' and 'private' not in str(result)
    assert print_agent.execute(dict(job, recovery_only=True), lambda _: content, ledger) == result
    assert calls == [1]
    receipt(relay, job['id'], result)
    with pytest.raises(ValueError, match='尚未确认'):
        upload(relay)
    # Uncertainty does not block read-only queue inspection.
    relay.enqueue('overview')
    assert claim(relay)['kind'] == 'overview'


def test_idempotency_and_same_operation_id_cannot_change_request(relay):
    first = upload(relay)
    assert upload(relay)['operation_id'] == first['operation_id']
    with pytest.raises(ValueError, match='其他请求'):
        relay.enqueue('delete', {'kind': 'print', 'job_id': 1}, token=first['operation_id'])
    with pytest.raises(ValueError):
        relay.document('../escape', A)


def test_cloud_routes_do_not_call_school_and_machine_api_rejects_browser(monkeypatch, tmp_path):
    from sustech_dashboard import app as dashboard
    monkeypatch.setattr(dashboard, 'CLOUD', True)
    monkeypatch.setenv('SUSTECH_PUBLIC_HOST', 'dashboard.test')
    monkeypatch.delenv('SUSTECH_BROWSER_LOGIN', raising=False)
    monkeypatch.setattr(print_relay, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(printing, 'overview', lambda: pytest.fail('Cloud must not query campus print'))
    client = dashboard.create_app().test_client()
    base = 'https://dashboard.test'
    assert client.post('/api/printing/agent', base_url=base, json={'agent_id': A}).status_code == 400
    machine = {'Authorization': 'Basic b3duZXI6Zml4dHVyZQ==', 'X-Campus-Agent': '1'}
    assert client.post('/api/printing/agent', base_url=base, headers=machine,
                       json={'agent_id': A, 'ready': True}).status_code == 200
    store = PrintRelay(tmp_path / 'print-relay')
    store.pair(A)
    assert client.get('/api/printing', base_url=base).json['state'] == 'queued'
    # Existing browser CSRF remains required for both upload and computer selection.
    for endpoint in ('/api/printing/upload', '/api/printing/relay/pair'):
        assert client.post(endpoint, base_url=base, headers={'Origin': base}).status_code == 400


def test_local_mode_keeps_direct_printing(monkeypatch):
    from sustech_dashboard import app as dashboard
    monkeypatch.setattr(dashboard, 'CLOUD', False)
    monkeypatch.setattr(printing, 'overview', lambda: {'jobs': [], 'scans': [], 'stations': []})
    client = dashboard.create_app().test_client()
    assert client.get('/api/printing/relay').json == {'enabled': False}
    assert client.get('/api/printing').json['jobs'] == []


@pytest.mark.parametrize('url', ['http://cas.sustech.edu.cn/cas/login', 'https://pms.sustech.edu.cn.evil.test/',
    'https://owner:secret@pms.sustech.edu.cn/', 'https://pms.sustech.edu.cn:8080/', 'file:///private'])
def test_print_sso_never_sends_credentials_or_tickets_to_untrusted_routes(url):
    with pytest.raises(ValueError):allowed_url(url)


def test_legacy_school_redirect_is_upgraded_before_requesting_session_token():
    value = 'http://pms.sustech.edu.cn/api/client/Auth/UniEntry?SessionId=fixture&Func=client'
    assert allowed_url(value) == value.replace('http://','https://',1)


def test_sso_uses_current_school_flow_and_verified_https(monkeypatch):
    from sustech_dashboard import print_auth
    from types import SimpleNamespace
    from sustech_survival.sso.authlib import pms
    calls = []
    class Reply:
        def __init__(self,url,status=200,payload=None,location=None,text=''):
            self.url=url;self.status_code=status;self.text=text;self.payload=payload
            self.headers={'Location':location} if location else {}
        def raise_for_status(self):assert self.status_code < 400
        def json(self):return self.payload
    class Session:
        verify=True
        headers={}
        def get(self,url,**kw):
            calls.append(('get',url,kw))
            assert url.startswith('https://')
            if url.endswith('/SSoPage'):return Reply(url,payload={'code':0,'result':'https://pms.sustech.edu.cn/authcenter/toLoginPage'})
            if url.endswith('/toLoginPage'):return Reply(url,302,location='https://cas.sustech.edu.cn/cas/login?service=fixture')
            if 'cas.sustech.edu.cn' in url:return Reply(url,text='<input name="execution" value="fixture-nonce">')
            if 'doAuth' in url:return Reply(url,302,location='http://pms.sustech.edu.cn/api/client/Auth/UniEntry?SessionId=fixture')
            return Reply(url)
        def post(self,url,**kw):
            calls.append(('post',url,kw))
            if 'cas.sustech.edu.cn' in url:
                assert kw['allow_redirects'] is False
                assert kw['data']['execution']=='fixture-nonce'
                return Reply(url,302,location='https://pms.sustech.edu.cn/authcenter/doAuth/fixture?ticket=fixture')
            return Reply(url,payload={'code':0})
    monkeypatch.setattr(print_auth.requests,'Session',Session)
    monkeypatch.setattr(pms,'PMSAuth',lambda:SimpleNamespace(username='fixture-owner',password='fixture-password'))
    assert print_auth.login().verify is True
    credential_posts=[c for c in calls if 'password' in c[2].get('data',{})]
    assert len(credential_posts)==1 and 'cas.sustech.edu.cn' in credential_posts[0][1]


def test_linux_host_uses_own_systemd_pair_credential(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from sustech_dashboard import download_agent
    import json
    (tmp_path/'campus-pair').write_text(json.dumps({'url':'https://host.test/campus/',
        'username':'fixture-owner','password':'fixture-pair-password'}))
    monkeypatch.setattr(download_agent,'os',SimpleNamespace(name='posix',environ={'CREDENTIALS_DIRECTORY':str(tmp_path)}))
    monkeypatch.setattr(download_agent,'unprotect_password',lambda *a:pytest.fail('Linux must not use Windows DPAPI'))
    base,session=download_agent.cloud_session()
    assert base=='https://host.test/campus' and session.auth==('fixture-owner','fixture-pair-password')
    session.close()


def test_distinct_owner_instances_cannot_claim_or_read_each_others_operations(tmp_path):
    first=PrintRelay(tmp_path/'owner-a');second=PrintRelay(tmp_path/'owner-b')
    heartbeat(first,A);first.pair(A)
    heartbeat(second,B);second.pair(B)
    operation=upload(first)
    assert claim(second,B) is None
    with pytest.raises(ValueError):second.operation(operation['operation_id'])
    with pytest.raises(ValueError):second.document(operation['operation_id'],B)
