import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
import pytest

from sustech_dashboard.devices import Devices, agent_path
from sustech_dashboard.protocol import parse_uri


def test_grants_single_use_scoped_restart_password_change_and_expiry(tmp_path, monkeypatch):
    from sustech_dashboard import devices
    path = tmp_path / 'devices.sqlite3'
    store = Devices(path)
    gid, ticket = store.issue_grant('owner', {'kind': 'connect'})
    assert ticket.encode() not in path.read_bytes()
    store = Devices(path)
    with pytest.raises(ValueError):
        store.redeem(ticket, 'other-owner', 'a' * 32)
    first = store.redeem(ticket, 'owner', 'a' * 32)
    assert first['id'] == gid and first['intent']['kind'] == 'connect'
    assert first['token'].encode() not in path.read_bytes()
    assert store.valid_device(first['token'], 'owner')
    assert not store.valid_device(first['token'], 'changed-password')
    with pytest.raises(ValueError):
        store.redeem(ticket, 'owner', 'a' * 32)
    _, second = store.issue_grant('owner', {'kind': 'root'})
    assert store.redeem(second, 'owner', 'a' * 32, first['token'])['token'] == first['token']
    _, expired = store.issue_grant('owner', {'kind': 'connect'})
    now = devices.time.time()
    monkeypatch.setattr(devices.time, 'time', lambda: now + 181)
    with pytest.raises(ValueError):
        store.redeem(expired, 'owner', 'a' * 32)


@pytest.mark.parametrize('path', ['/api/instance/login', '/api/venues/book', '/api/materials/jobs',
    '/api/devices/grant', '/api/status', '/setup', '/api/printing/upload', '/api/printing/agent/file/../../status'])
def test_device_credential_cannot_be_used_as_browser_login(path):
    assert not agent_path(path)


@pytest.mark.parametrize('value', ['https://124.221.144.155/spaces/' + 'f'*24,
    'sustech-campus://connect#server=https://evil.test/spaces/'+'f'*24+'&ticket='+'a'*43,
    'sustech-campus://connect#server=x&server=y&ticket='+'a'*43,
    'sustech-campus://connect#server=x&ticket=" --backend', 'sustech-campus://open#path=D:/private'])
def test_untrusted_protocol_arguments_rejected_before_launch(value):
    with pytest.raises(ValueError):
        parse_uri(value)


def test_real_hosted_cookie_grant_identity_and_nginx_bearer_scope(monkeypatch, tmp_path):
    from sustech_dashboard import app, runtime, authentication, hosted, web_auth, instance_routes, paths, quotas
    root = tmp_path/'space'; root.mkdir()
    credentials = tmp_path/'credentials'; credentials.mkdir()
    (credentials/'campus-vault').write_bytes(Fernet.generate_key())
    prefix = '/spaces/' + 'a'*24
    monkeypatch.setenv('SUSTECH_EXECUTION_MODE', 'hosted')
    monkeypatch.setenv('SUSTECH_SPACE_ID', 'a'*24)
    monkeypatch.setenv('SUSTECH_PROXY_PREFIX', prefix)
    monkeypatch.setenv('SUSTECH_PUBLIC_HOST', 'dashboard.test')
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(credentials))
    monkeypatch.setattr(app, 'CLOUD', True)
    for module in (runtime, hosted, web_auth, instance_routes, paths):
        monkeypatch.setattr(module, 'DATA_ROOT', root)
    monkeypatch.setattr(authentication, 'load_owner_credentials', lambda: False)
    monkeypatch.setattr(app, 'MANIFEST_PATH', root/'manifest.json')
    monkeypatch.setattr(app, 'MATERIALS_DB', root/'materials.sqlite3')
    space = hosted.Space(); space.redeem(space.invite(), 'fixture-panel-password')
    space.bind('fixture-student', 'never-transferred-password', False, lambda *args: None)
    rt = runtime.Runtime(); rt.configured.set()
    site = app.create_app(rt)
    client = site.test_client()
    headers = {'Host': 'dashboard.test', 'X-Forwarded-Proto': 'https', 'X-Forwarded-Prefix': prefix}
    def call(client, method, path, **kwargs):
        return getattr(client, method)(path, base_url='https://dashboard.test'+prefix+'/', headers={**headers, **kwargs.pop('headers', {})}, **kwargs)
    page = call(client, 'get', '/auth/login')
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    browser = {'Origin': 'https://dashboard.test', 'X-CSRF-Token': csrf}
    assert call(client, 'post', '/auth/login', headers=browser, data={
        'username': 'member', 'password': 'fixture-panel-password', 'csrf': csrf}).status_code == 303
    grant = call(client, 'post', '/api/devices/grant', headers=browser, json={'kind': 'connect'})
    assert grant.status_code == 200
    ticket = parse_qs(urlsplit(grant.json['uri']).fragment)['ticket'][0]
    machine = site.test_client()
    assert call(machine, 'post', '/api/devices/grant', json={}).status_code == 401
    payload = {'ticket': ticket, 'device': 'b'*32, 'sid': 'other-student'}
    assert call(machine, 'post', '/api/devices/exchange', json=payload, headers={'X-Campus-Agent': '1'}).status_code == 403
    payload['sid'] = 'fixture-student'
    response = call(machine, 'post', '/api/devices/exchange', json=payload, headers={'X-Campus-Agent': '1'})
    assert response.status_code == 200
    bearer = {'Authorization': 'Bearer ' + response.json['token'], 'X-Campus-Agent': '1'}
    assert call(machine, 'get', '/auth/verify', headers={**bearer, 'X-Campus-Original-URI': prefix+'/api/materials/agent'}).status_code == 204
    assert call(machine, 'get', '/auth/verify', headers={**bearer, 'X-Campus-Original-URI': prefix+'/api/instance/login'}).status_code == 401
    assert call(machine, 'post', '/api/materials/agent', headers=bearer, json={'agent_id': 'b'*32, 'claim': False}).status_code == 200
    assert call(machine, 'post', '/api/materials/agent', headers={**bearer, **browser}, json={'agent_id': 'b'*32}).status_code == 403
    assert call(machine, 'post', '/api/devices/exchange', json=payload, headers={'X-Campus-Agent': '1'}).status_code == 400

    # Cold native client: the browser's one-use grant carries an encrypted
    # bootstrap, not a second login or a plaintext credential endpoint.
    from sustech_dashboard.credential_transfer import X25519PrivateKey, public_key, unseal
    monkeypatch.setattr(authentication, '_active_credentials', ('fixture-student', 'fixture-cas-password'))
    private = X25519PrivateKey.generate()
    def issue():
        grant = call(client, 'post', '/api/devices/grant', headers=browser, json={'kind':'connect'}).json
        return parse_qs(urlsplit(grant['uri']).fragment)['ticket'][0]
    bootstrap = {'ticket': issue(), 'device':'c'*32, 'bootstrap_key':public_key(private)}
    assert call(machine, 'post', '/api/devices/exchange', json={**bootstrap,'sid':'other-student'}, headers={'X-Campus-Agent':'1'}).status_code == 403
    assert call(machine, 'post', '/api/devices/exchange', json={**bootstrap,'bootstrap_key':'invalid'}, headers={'X-Campus-Agent':'1'}).status_code == 400
    assert call(machine, 'post', '/api/devices/exchange', json=bootstrap, headers={'X-Campus-Agent':'1','Origin':'https://dashboard.test'}).status_code == 403
    result = call(machine, 'post', '/api/devices/exchange', json=bootstrap, headers={'X-Campus-Agent':'1'})
    assert result.status_code == 200
    assert 'fixture-cas-password' not in result.text and 'fixture-student' not in result.text
    assert unseal(private, result.json['bootstrap'], bootstrap['ticket'], bootstrap['device']) == authentication.current_credentials()
    assert call(machine, 'post', '/api/devices/exchange', json=bootstrap, headers={'X-Campus-Agent':'1'}).status_code == 400
    for path in root.glob('*.sqlite3'):
        assert b'fixture-cas-password' not in path.read_bytes()
    assert space.credentials() is None  # Cloud remember=False still bootstraps this running login.


def test_paired_downloads_claim_one_queue_and_acknowledge_only_owner(monkeypatch, tmp_path):
    from sustech_dashboard import download_agent as agent
    from sustech_dashboard.materials_store import MaterialsStore
    from sustech_dashboard.core import save_json
    monkeypatch.setattr(agent, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(agent, 'CONFIG', tmp_path/'pair.json')
    agent.CONFIG.write_text('{"url":"https://personal.example/campus"}')
    monkeypatch.setattr(agent, 'personal_pair_available', lambda: True)
    manifest = {'updated_at': 'now', 'items': [{'course_id': 'c', 'content_id': 'i', 'id': 'a'}]}
    local, cloud = MaterialsStore(tmp_path/'local.sqlite3'), MaterialsStore(tmp_path/'cloud.sqlite3')
    local.set_auto(False); cloud.set_auto(False)
    local_id = local.enqueue(['c/i/a'], 'local')
    cloud_id = cloud.enqueue(['c/i/a'], 'cloud')
    poll = agent.PairedPoll(lambda p: local.agent_poll(p, manifest))
    def remote(p):
        result = cloud.agent_poll(p, manifest)
        if result['job']: result['job']['_remote'] = True
        return result
    monkeypatch.setattr(poll, 'remote_poll', remote)
    payload = {'agent_id': 'a'*32, 'claim': True, 'active_job': None}
    assert poll(payload)['job']['id'] == local_id
    assert cloud.view(manifest)['jobs'][0]['state'] == 'queued'
    finish = {'id': local_id, 'results': [{'key': 'c/i/a', 'status': 'saved', 'sha256': 'a'*64, 'size': 8}], 'finished': True}
    poll({**payload, 'claim': False, 'active_job': local_id, 'job_update': finish})
    job = poll(payload)['job']; assert job['id'] == cloud_id and job['_remote']
    assert local.view(manifest)['jobs'][0]['state'] == 'completed'
    poll({**payload, 'claim': False, 'active_job': cloud_id, '_remote': True, 'job_update': {**finish, 'id': cloud_id}})
    assert cloud.view(manifest)['jobs'][0]['state'] == 'completed'
    def offline(p): raise ConnectionError('fixture')
    monkeypatch.setattr(poll, 'remote_poll', offline)
    assert poll(payload)['job'] is None
    with pytest.raises(ConnectionError):
        poll({**payload, 'claim': False, 'active_job': cloud_id, '_remote': True})
