import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet
import pytest

from sustech_dashboard.hosted import Space
from sustech_dashboard.shared_metadata import MetadataStore, MetadataError, MetadataQuota, service_app


def _process_read(path, owner):
    import time
    store = MetadataStore(path)
    for _ in range(300):
        result = store.acquire(owner, 'independent-course', 'course', 'verified:process-fixture', ['verified:process-fixture'])
        if result['state'] == 'ready':
            return store.resolve(owner, [result['ref']])[0]
        if result['state'] == 'refresh':
            time.sleep(.2)
            store.finish(owner, result['resource'], result['fence'], 'course', {'id': 'other-course', 'name': 'Independent canary'})
        time.sleep(.02)
    raise AssertionError('No shared result')


def test_ten_independent_processes_share_one_refresh(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    path = str(tmp_path/'process-shared.sqlite3')
    store = MetadataStore(path)
    with ProcessPoolExecutor(max_workers=10, mp_context=multiprocessing.get_context('spawn')) as pool:
        values = list(pool.map(_process_read, [path]*10, map(str, range(10))))
    assert len(values) == 10 and all(v['id'] == 'other-course' for v in values)
    assert store.stats()['refresh_rounds'] == 1
    assert store.stats()['objects'] == 1
    assert store.stats()['references'] == 10


def test_packed_snapshots_keep_deadline_and_submission_private(tmp_path):
    from sustech_dashboard.shared_metadata import pack_document, unpack_document
    store = MetadataStore(tmp_path/'metadata.sqlite3')
    class Client:
        def __init__(self, owner): self.owner = owner
        def call(self, action, **payload):
            return store.publish(self.owner, payload['entries']) if action == 'publish' else store.resolve(self.owner, payload['refs'])
    a, b = Client('a'), Client('b')
    common = {'course_id': 'c', 'content_id': 'assignment', 'name': 'Shared assignment', 'kind': 'assignment'}
    first = pack_document({'assignments': [{**common, 'due': '2026-10-09', 'status': 'submitted'}]}, a)
    second = pack_document({'assignments': [{**common, 'due': '2026-10-12', 'status': 'not_submitted'}]}, b)
    assert 'Shared assignment' not in json.dumps(first)
    assert unpack_document(first, a)['assignments'][0]['status'] == 'submitted'
    assert unpack_document(second, b)['assignments'][0]['due'] == '2026-10-12'
    assert store.stats()['objects'] == 1
    assert b'2026-10-12' not in store.path.read_bytes()


def test_failure_backoff_and_permission_expiry(tmp_path):
    now = [100.]
    store = MetadataStore(tmp_path/'fail.sqlite3', clock=lambda: now[0])
    lease = store.acquire('a', 'weather', 'weather', 'public', ['public'])
    store.fail('a', lease['resource'], lease['fence'])
    assert store.acquire('b', 'weather', 'weather', 'public', ['public'])['state'] == 'wait'
    now[0] += 11
    assert store.acquire('b', 'weather', 'weather', 'public', ['public'])['state'] == 'refresh'
    ref = store.publish('a', [{'resource': 'c', 'kind': 'course', 'body': {'id': 'c'}}])[0]
    now[0] += 3601
    with pytest.raises(MetadataError):
        store.resolve('a', [ref])


def test_attachment_revision_preserves_user_file_and_second_device(tmp_path):
    from sustech_dashboard.materials_local import LocalMaterials
    from sustech_dashboard.provider import attachment_version
    class Provider:
        calls = 0
        def download_attachment(self, course, content, attachment, path):
            self.calls += 1
            path.write_bytes(b'school version '+str(self.calls).encode())
    provider = Provider()
    item = {'course_id': 'c', 'course_name': 'Course', 'content_id': 'i', 'id': 'a', 'file_name': 'document.pdf', 'source_version': 'v1'}
    first = LocalMaterials(tmp_path/'device-a', tmp_path/'a.json')
    second = LocalMaterials(tmp_path/'device-b', tmp_path/'b.json')
    receipt = first.save(provider, item)
    assert first.save(provider, item)['status'] == 'existing'
    second.save(provider, item)
    assert provider.calls == 2
    original = first.root/receipt['path']
    original.write_bytes(b'user annotations')
    newer = first.save(provider, {**item, 'source_version': 'v2'})
    assert original.read_bytes() == b'user annotations'
    assert newer['path'] != receipt['path'] and provider.calls == 3
    assert attachment_version({'id': 'a', 'fileSize': 1}, {}) != attachment_version({'id': 'a', 'fileSize': 2}, {})


def test_single_use_invitation_encryption_restart_and_revocation(tmp_path):
    key = Fernet.generate_key()
    space = Space(tmp_path, key)
    invitation = space.invite()
    assert invitation.encode() not in space.path.read_bytes()
    space.redeem(invitation, 'fixture-panel-password')
    with pytest.raises(ValueError):
        space.redeem(invitation, 'fixture-panel-password')
    calls = []
    space.bind('fixture-student', 'fixture-cas-secret', True, lambda *args: calls.append(args))
    restarted = Space(tmp_path, key)
    assert restarted.credentials() == {'sid': 'fixture-student', 'password': 'fixture-cas-secret'}
    assert b'fixture-cas-secret' not in space.path.read_bytes()
    assert b'fixture-panel-password' not in space.path.read_bytes()
    with pytest.raises(ValueError, match='其他校园身份'):
        restarted.bind('another-student', 'secret', True, lambda *args: pytest.fail('No account switch'))
    restarted.disconnect()
    assert restarted.credentials() is None
    restarted.revoke()
    assert restarted.web_config() is None
    assert len(calls) == 1


def test_invitation_expiry_and_failed_school_login_do_not_persist(tmp_path):
    space = Space(tmp_path, Fernet.generate_key())
    token = space.invite(ttl=-1)
    with pytest.raises(ValueError, match='过期'):
        space.redeem(token, 'fixture-password')
    token = space.invite()
    space.redeem(token, 'fixture-password')
    def invalid(*args):
        raise ValueError('school rejected')
    with pytest.raises(ValueError):
        space.bind('fixture', 'rejected-password', True, invalid)
    assert space.credentials() is None


def test_ten_concurrent_verified_scopes_merge_refresh_and_store_once(tmp_path):
    store = MetadataStore(tmp_path/'shared.sqlite3')
    import threading
    import time
    barrier = threading.Barrier(10)
    calls = []
    def access(n):
        barrier.wait()
        for _ in range(300):
            result = store.acquire(str(n), 'school/term/course/c', 'course', 'verified:fixture', ['verified:fixture'])
            if result['state'] == 'ready':
                return store.resolve(str(n), [result['ref']])[0]
            if result['state'] == 'refresh':
                calls.append(n)
                time.sleep(.08)
                store.finish(str(n), result['resource'], result['fence'], 'course', {'id': 'c', 'name': 'Fixture course'}, source_requests=3)
            else:
                time.sleep(.01)
        pytest.fail('Refresh failed to converge')
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(access, range(10)))
    assert len(calls) == 1 and len(results) == 10
    assert store.stats()['objects'] == 1
    assert store.stats()['source_requests'] == 3  # One three-page round, not one HTTP request.
    assert store.stats()['references'] == 10


def test_authorization_revocation_version_fencing_and_304(tmp_path):
    now = [1000.]
    store = MetadataStore(tmp_path/'shared.sqlite3', clock=lambda: now[0])
    ref = store.publish('a', [{'resource': 'c', 'kind': 'course', 'body': {'id': 'c', 'name': 'Original'}}])[0]
    with pytest.raises(MetadataError):
        store.resolve('b', [ref])
    ref_b = store.publish('b', [{'resource': 'c', 'kind': 'course', 'body': {'id': 'c', 'name': 'Original'}}])[0]
    store.revoke('a')
    with pytest.raises(MetadataError):
        store.resolve('a', [ref])
    assert store.resolve('b', [ref_b])[0]['name'] == 'Original'
    with pytest.raises(MetadataError):
        store.acquire('a', 'c', 'course', 'unverified', [])
    lease = store.acquire('a', 'c', 'course', 'verified', ['verified'])
    now[0] += 121
    newer = store.acquire('b', 'c', 'course', 'verified', ['verified'])
    with pytest.raises(MetadataError):
        store.finish('a', lease['resource'], lease['fence'], 'course', {'id': 'c'})
    store.finish('b', newer['resource'], newer['fence'], 'course', {'id': 'c', 'name': 'New'}, etag='revision-2')
    now[0] += 301
    conditional = store.acquire('a', 'c', 'course', 'verified', ['verified'])
    assert conditional['etag'] == 'revision-2'
    store.finish('a', conditional['resource'], conditional['fence'], 'course', not_modified=True)
    assert store.acquire('b', 'c', 'course', 'verified', ['verified'])['body']['name'] == 'New'
    store.invalidate('b', 'c', 'course', 'verified', ['verified'])
    assert store.acquire('a', 'c', 'course', 'verified', ['verified'])['state'] == 'refresh'


def test_service_rejects_tokens_raw_files_and_unverified_scopes(tmp_path):
    store = MetadataStore(tmp_path/'shared.sqlite3')
    registry = {'a': {'digest': hashlib.sha256(b'fixture-token').hexdigest(), 'scopes': ['public:weather']}}
    client = service_app(store, lambda: registry).test_client()
    assert client.post('/v1/metadata', json={'operation': 'resolve', 'refs': []}).status_code == 401
    headers = {'Authorization': 'Bearer fixture-token'}
    response = client.post('/v1/metadata', headers=headers, json={'operation': 'publish', 'entries': [
        {'resource': 'c', 'kind': 'course', 'body': {'id': 'c', 'password': 'secret', 'bytes': 'data:document'}}]})
    assert response.status_code == 400 and 'secret' not in response.text
    assert client.post('/v1/metadata', headers=headers, json={'operation': 'acquire', 'resource': 'c', 'kind': 'course', 'scope': 'same-course'}).status_code == 400
    with pytest.raises(MetadataQuota):
        MetadataStore(tmp_path/'small.sqlite3', max_bytes=65536).publish('a', [{'resource': 'c', 'kind': 'course', 'body': {'id': 'c'}}])


def test_hosted_browser_auth_isolated_routes_and_update_controls(monkeypatch, tmp_path):
    from sustech_dashboard import app, runtime, authentication, hosted, web_auth, instance_routes, paths
    root = tmp_path/'a'
    root.mkdir()
    credentials = tmp_path/'credentials'
    credentials.mkdir()
    (credentials/'campus-vault').write_bytes(Fernet.generate_key())
    monkeypatch.setenv('SUSTECH_EXECUTION_MODE', 'hosted')
    monkeypatch.setenv('SUSTECH_SPACE_ID', 'a'*24)
    monkeypatch.setenv('SUSTECH_PROXY_PREFIX', '/spaces/'+'a'*24)
    monkeypatch.setenv('SUSTECH_PUBLIC_HOST', 'dashboard.test')
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(credentials))
    monkeypatch.setattr(app, 'CLOUD', True)
    for module in (runtime, hosted, web_auth, instance_routes, paths):
        monkeypatch.setattr(module, 'DATA_ROOT', root)
    monkeypatch.setattr(authentication, 'load_owner_credentials', lambda: False)
    space = Space(root)
    invitation = space.invite()
    rt = runtime.Runtime()
    site = app.create_app(rt)
    client = site.test_client()
    original_open = client.open
    def with_prefix(*args, **kwargs):
        kwargs.setdefault('base_url', 'https://dashboard.test/spaces/'+'a'*24+'/')
        return original_open(*args, **kwargs)
    client.open = with_prefix
    headers = {'Host': 'dashboard.test', 'X-Forwarded-Proto': 'https', 'X-Forwarded-Prefix': '/spaces/'+'a'*24, 'Origin': 'https://dashboard.test'}
    assert client.get('/api/status', headers=headers).status_code == 401
    assert client.get('/invite', headers=headers).status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get('/invite', headers=headers).text).group(1)
    assert client.post('/invite', headers=headers, data={'csrf': csrf, 'invitation': invitation, 'password': 'fixture-password'}).status_code == 303
    assert client.post('/auth/login', headers=headers, data={'csrf': csrf, 'username': 'member', 'password': 'fixture-password'}).status_code == 303
    assert client.get('/setup', headers=headers).status_code == 200
    assert client.get('/api/instance', headers=headers).json['execution_mode'] == 'hosted'
    assert client.get('/api/instance', headers={**headers, 'X-Forwarded-Prefix': '/spaces/'+'b'*24}).status_code == 400
    assert client.post('/api/instance/updates/install', headers={**headers, 'X-CSRF-Token': csrf}).status_code == 403
    assert client.post('/auth/logout', headers=headers, data={'csrf': csrf}).status_code == 303
    assert client.get('/api/instance', headers=headers).status_code == 401


def test_cloud_attachment_redirect_never_reads_file_bytes(monkeypatch, tmp_path):
    from sustech_dashboard import app
    monkeypatch.setenv('SUSTECH_EXECUTION_MODE', 'personal_server')
    monkeypatch.delenv('SUSTECH_BROWSER_LOGIN', raising=False)
    monkeypatch.setenv('SUSTECH_PUBLIC_HOST', 'dashboard.test')
    monkeypatch.setattr(app, 'CLOUD', True)
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'items': [{'course_id': 'c', 'content_id': 'i', 'id': 'f', 'file_name': 'sample.pdf'}]}))
    monkeypatch.setattr(app, 'MANIFEST_PATH', manifest)
    class Blackboard:
        def current_courses(self): return [{'id': 'c'}]
        def results(self, path):
            assert not path.endswith('download')
            return [{'id': 'f'}]
        def get(self, *args, **kwargs): pytest.fail('Cloud must not read file bytes')
    monkeypatch.setattr(app, 'Blackboard', Blackboard)
    client = app.create_app().test_client()
    headers = {'Host': 'dashboard.test', 'X-Forwarded-Proto': 'https'}
    page = client.get('/api/attachment?key=c/i/f', headers=headers)
    assert page.status_code == 200 and '打开官方内容页并登录' in page.text
    response = client.get('/api/attachment?key=c/i/f&direct=1', headers=headers)
    assert response.status_code == 303
    assert response.location == 'https://bb.sustech.edu.cn/learn/api/public/v1/courses/c/contents/i/attachments/f/download'
