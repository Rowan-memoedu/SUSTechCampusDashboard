"""Capability contracts, private routes, one-shot writes and hostile school content."""
from datetime import datetime
import uuid
from types import SimpleNamespace

import pytest

from sustech_dashboard import academic_extras as academic, app, actions, extras_routes, browser_services, campus_extras


class BB:
    def current_courses(self):
        return [{'id': 'c', 'name': 'Current course'}]

    def results(self, path):
        if path.endswith('/announcements'):
            return [{'id': 'old', 'title': 'Older', 'created': '2026-10-01', 'body': '<p>Older</p>'},
                    {'id': 'new', 'title': 'With attachment', 'created': '2026-10-12', 'body': '<script>secret()</script><p>Notice<img src="https://tracker.invalid/pixel"><a onclick="bad()" href="/bbcswebdav/lesson.pdf">lesson.pdf</a><a href="javascript:bad()">bad</a></p>'},
                    {'id': 'draft', 'draft': True, 'body': 'Hidden'}]
        if path.endswith('/columns'):
            return [{'id': 'zero', 'name': 'Zero', 'score': {'possible': 10}},
                    {'id': 'exempt', 'name': 'Exempt'}, {'id': 'pending', 'name': 'Not released'}]
        if '/gradebook/users/' in path:
            return [{'columnId': 'zero', 'score': 0}, {'columnId': 'exempt', 'exempt': True}]
        return []

    def json(self, path):
        return {'id': 'me'}

    def _contents(self, cid):
        yield {'id': 'text', 'title': 'Document', 'body': '<p>Needle inside body</p>', 'contentHandler': {'id': 'resource/x-bb-document'}}
        yield {'id': 'video', 'title': 'Video', 'body': '', 'contentHandler': {'id': 'resource/x-bb-video'}}


def test_announcement_order_richtext_attachments_and_course_boundary():
    data = academic.announcements(BB(), 'c', '/campus')
    assert [a['id'] for a in data['items']] == ['new', 'old']
    first = data['items'][0]
    assert first['attachments'][0]['name'] == 'lesson.pdf'
    assert first['attachments'][0]['url'].startswith('/campus/api/blackboard-resource?path=')
    assert '/campus/api/blackboard-resource' in first['body_html']
    assert all(value not in first['body_html'] for value in ['secret()', 'onclick', 'javascript:', 'tracker.invalid'])
    with pytest.raises(ValueError):
        academic.announcements(BB(), 'old-course')


def test_gradebook_zero_exempt_and_unpublished_are_distinct():
    rows = academic.gradebook(BB())['rows']
    assert rows[0]['score'] == 0
    assert rows[1]['exempt'] is True
    assert rows[2]['score'] is None and rows[2]['exempt'] is False
    assert all(r['source'] == 'gradebook_user_grade' for r in rows)


def test_announcement_xid_uses_exact_official_permission_context():
    bb = BB()
    bb.results = lambda _: [{'id': '_14_1', 'body': '<a href="/bbcswebdav/xid-28_1?old=1">Notes</a>'}]
    bb.get = lambda _: SimpleNamespace(text='<ul id="announcementList"><li id="_14_1"><a href="/bbcswebdav/pid-14-dt-announcement-rid-28_1/xid-28_1">Notes</a></li><li id="_99_1"><a href="/bbcswebdav/pid-99-dt-announcement-rid-28_1/xid-28_1">Other</a></li></ul>')
    result = academic.announcements(bb, 'c')['items'][0]
    assert 'pid-14-dt-announcement' in result['body_html']
    assert 'pid-99' not in result['body_html']
    assert 'old' not in result['attachments'][0]['url']
    bb.get = lambda _: SimpleNamespace(text='<li id="_99_1"></li>')
    with pytest.raises(ValueError, match='课程访问地址'):
        academic.announcements(bb, 'c')


def test_content_search_includes_body_and_independent_types():
    data = academic.content_search(BB(), {'q': 'needle'})
    assert [x['id'] for x in data['items']] == ['text']
    assert data['counts'] == {'document': 1, 'video': 1}
    assert academic.content_search(BB(), {'kind': 'video'})['items'][0]['id'] == 'video'


def test_query_failure_is_not_an_empty_success():
    bb = BB()
    bb.results = lambda _: (_ for _ in ()).throw(ConnectionError())
    assert len(academic.gradebook(bb)['warnings']) == 1
    with pytest.raises(ConnectionError):
        academic.announcements(bb, 'c')


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'CLOUD', False)
    monkeypatch.setattr(actions, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(app, 'Blackboard', BB)
    application = app.create_app()
    return application.test_client()


def csrf_headers(client):
    import re
    html = client.get('/').text
    token = re.search(r'const roomCsrf="([^"]+)"', html)[1]
    return {'Origin': 'http://localhost', 'X-CSRF-Token': token}


def test_home_stays_compact_and_all_pages_render_without_school_calls(client):
    html = client.get('/').text
    assert html.count('<section') == 8  # Seven original sections plus the existing update panel.
    assert '更多服务' in html and 'extras.js' in html
    for kind in extras_routes.PAGES:
        assert client.get('/explore/' + kind).status_code == 200
    assert client.get('/api/announcements/c').json['items'][0]['id'] == 'new'


def test_preview_and_commit_are_csrf_guarded_and_one_shot(client, monkeypatch):
    calls = []
    monkeypatch.setattr(browser_services, 'prepare_cle', lambda data: {'summary': {'时段': 'fixture'}, 'target': 'cle:fixture', 'request': data})
    monkeypatch.setattr(browser_services, 'commit_cle', lambda data: (calls.append(data) or {'state': 'confirmed', 'message': 'Fixture readback'}))
    assert client.post('/api/extras/cle/preview', json={}).status_code == 400
    headers = csrf_headers(client)
    preview = client.post('/api/extras/cle/preview', headers=headers, json={}).json
    payload = {'review_id': preview['review_id'], 'operation_id': str(uuid.uuid4()), 'confirmed': True}
    assert client.post('/api/extras/cle/commit', headers=headers, json=dict(payload, confirmed=False)).status_code == 400
    assert client.post('/api/extras/cle/commit', headers=headers, json=payload).json['state'] == 'confirmed'
    assert client.post('/api/extras/cle/commit', headers=headers, json=payload).json['state'] == 'confirmed'
    assert client.post('/api/extras/cle/commit', headers=headers, json=dict(payload, operation_id=str(uuid.uuid4()))).status_code == 400
    assert len(calls) == 1


def test_uncertain_write_is_not_replayed(client, monkeypatch):
    monkeypatch.setattr(browser_services, 'prepare_cle', lambda data: {'summary': {}, 'target': 'cle:uncertain', 'request': data})
    def send(_):
        actions.mark_sent()
        raise ValueError('unexpected response')
    monkeypatch.setattr(browser_services, 'commit_cle', send)
    headers = csrf_headers(client)
    preview = client.post('/api/extras/cle/preview', headers=headers, json={}).json
    result = client.post('/api/extras/cle/commit', headers=headers, json={'review_id': preview['review_id'], 'operation_id': str(uuid.uuid4()), 'confirmed': True})
    assert result.json['state'] == 'needs_review'
    assert client.post('/api/extras/cle/preview', headers=headers, json={}).status_code == 400


def test_runtime_protects_new_private_routes(monkeypatch, tmp_path):
    from sustech_dashboard import runtime, authentication
    monkeypatch.setattr(runtime, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(authentication, 'load_owner_credentials', lambda: False)
    rt = runtime.Runtime()
    c = app.create_app(rt).test_client()
    for path in ['/api/announcements/c', '/api/blackboard/grades', '/api/extras/evaluations', '/explore']:
        assert c.get(path).status_code == 401


def test_unsupported_eval_answers_never_reach_save(monkeypatch):
    from sustech_survival.tis.eval.questions import QuestionType
    monkeypatch.setattr(browser_services, 'evaluation_target', lambda _: {'id': 't', 'title': 'Course', 'status': '0'})
    ev = SimpleNamespace(questions_data=[{'qid': 'a', 'type': QuestionType.RATING, 'text': 'Rating', 'options': ['1', '2']}])
    monkeypatch.setattr(browser_services, 'load_evaluation', lambda _: ev)
    for answers in [{}, {'a': '10'}, {'a': '2', 'injected': '2'}]:
        with pytest.raises(ValueError):
            browser_services.prepare_evaluation({'id': 't', 'action': 'save', 'answers': answers})


def test_public_query_parameter_boundaries():
    with pytest.raises(ValueError):
        campus_extras.mirror({'kind': 'program', 'path': '/courses/本科人才培养方案/../../'})
    with pytest.raises(ValueError):
        campus_extras.transit({'op': 'stops', 'line': '../../private'})


def test_library_fields_are_scoped_to_stable_labels():
    html = '''<prm-full-view>
      <div><div><span data-details-label="title">题名</span></div><div>Example Book</div></div>
      <div><div><span data-details-label="publisher">出版信息</span></div><div>Example Press</div></div>
      <div><div><span data-details-label="creator">作者</span></div><div>A; B</div></div>
      <prm-alma-viewit>全文可用性<a href="/view/action/uresolver.do?service=1">电子图书</a></prm-alma-viewit>
    </prm-full-view>'''
    row = browser_services.parse_library_detail(html, 'https://example.org/discovery/fulldisplay')
    assert row['title'] == 'Example Book' and row['publisher'] == 'Example Press'
    assert row['authors'] == ['A', 'B']
    assert row['online_url'] == 'https://example.org/view/action/uresolver.do?service=1'
    with pytest.raises(ValueError):
        browser_services.parse_library_detail('<html>Loading</html>', 'https://example.org')


def test_eval_partial_save_stops_without_replaying_or_submitting(monkeypatch):
    from sustech_dashboard import study
    calls = []
    ev = SimpleNamespace(build_save_body=lambda **kw: {'pjjglist': [{'pjxxlist': [{'answer': 4}]}]})
    prepared = {'request': {'action': 'save', 'id': 't'}, 'summary': {'score': 4}, 'schema': [], 'evaluation': ev}
    monkeypatch.setattr(browser_services, 'prepare_evaluation', lambda _: prepared)
    monkeypatch.setattr(browser_services, 'mark_sent', lambda: None)
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'code': '500'})
    monkeypatch.setattr(study, 'auth', lambda: SimpleNamespace(BASE_URL='https://school', session=SimpleNamespace(post=lambda *a, **kw: calls.append(kw['json']) or response)))
    assert browser_services.commit_evaluation(prepared)['state'] == 'needs_review'
    assert len(calls) == 1
    response.json = lambda: {'code': '200', 'result': {}}
    calls.clear()
    assert browser_services.commit_evaluation(prepared)['state'] == 'needs_review'
    assert len(calls) == 1  # Missing parent receipt must not send teacher layer.


def test_borrow_prepare_uses_exact_room_and_never_sends(monkeypatch):
    from datetime import timedelta
    from sustech_dashboard import borrow, classrooms, study
    from sustech_survival.semester import Semester
    from sustech_survival.tis.classroom import booking
    from sustech_dashboard.core import CHINA_TZ
    sem = Semester('2026-2027', '1')
    monkeypatch.setattr(study, 'semester', lambda: sem)
    monkeypatch.setattr(study, 'auth', lambda: SimpleNamespace(username='fixture-owner'))
    monkeypatch.setattr(classrooms, 'client', lambda _: SimpleNamespace(_room_code_for_name=lambda n: 'ROOM-1', occupancy=lambda *a: []))
    monkeypatch.setattr(classrooms, 'room_inventory', lambda *a: [{'name': 'Room', 'capacity': 20}])
    monkeypatch.setattr(classrooms, 'calendar_day', lambda *a: ({'kind': 'ordinary'}, 7, 2, 7))
    permission = SimpleNamespace(allowed=True)
    busy = []
    monkeypatch.setattr(booking, 'VenueBorrowClient', lambda: SimpleNamespace(check_permission=lambda _: permission, query_venue_occupancy=lambda **kw: busy))
    request = dict(date=(datetime.now(CHINA_TZ).date() + timedelta(days=1)).isoformat(), name='Room', start=3, end=4,
                   headcount=10, applicant='Fixture', phone='000', purpose='Fixture study')
    prepared = borrow.prepare(request)
    wire = prepared['form'].to_api()
    assert wire['hlddct'] == '0' and wire['shbj'] == '1'
    assert wire['cdjymxlist'][0]['rs'] == '10' and wire['cdjymxlist'][0]['jyyy'] == 'Fixture study'
    assert wire['cdjymxlist'][0]['cddm'] == 'ROOM-1'
    assert wire['sqrzgh'] == 'fixture-owner'
    busy.append(SimpleNamespace(period_start=4, period_end=5))
    with pytest.raises(ValueError, match='冲突'):
        borrow.prepare(request)
    busy.clear()
    permission.allowed = False
    with pytest.raises(ValueError, match='权限'):
        borrow.prepare(request)
