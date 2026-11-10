import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, request

from sustech_dashboard import lab_idea, lab_sources
from sustech_dashboard.lab_engine import LabEngine
from sustech_dashboard.lab_routes import register_labs
from sustech_dashboard.homework_recovery import StageFailure


def project(tmp_path):
    root = tmp_path / 'course'
    root.mkdir()
    (root / 'course-info.yaml').write_text('content:\n  - OJ\n')
    (root / 'settings.gradle').write_text("rootProject.name = 'Fixture'\n")
    (root / 'OJ').mkdir()
    (root / 'OJ/section-info.yaml').write_text('content:\n  - Practice\n')
    (root / 'OJ/Practice').mkdir()
    (root / 'OJ/Practice/lesson-info.yaml').write_text('content: []\n')
    return root


def unit(code='class Main {}\n'):
    return {'path': 'OJ/Practice/T9999_Test', 'kind': 'edu', 'pid': 'T9999', 'source': 'src/Main.java',
            'markdown': '# Fixture', 'files': [{'path': 'src/Main.java', 'content': code}]}


def bridge(tmp_path):
    p = tmp_path / 'hydro.json'
    p.write_text(json.dumps({'bindings': [], 'private_key_fixture': 'do-not-lose'}))
    return p


def engine(tmp_path, monkeypatch):
    # Avoid tool discovery during isolated store/scheduler tests.
    from sustech_dashboard import homework_engine
    shared = SimpleNamespace(settings=lambda: {'backend': 'codex'}, snapshot=lambda: {}, wake=threading.Event())
    e = LabEngine(tmp_path / 'labs', shared, lambda: None, threading.Lock(), threading.Event())
    e.store.set_setting('configuration', {'enabled': True, 'min_week': 9})
    monkeypatch.setattr(lab_sources, 'collect', lambda *a: [])
    return e


def assignment():
    return {'course_id': 'course', 'content_id': 'item@hash', 'course': 'Fixture', 'name': 'Week 9', 'week': 9}


def test_additive_publish_is_idempotent_and_preserves_user_edits(tmp_path):
    root, cfg = project(tmp_path), bridge(tmp_path)
    record = tmp_path / 'published.json'
    first = lab_idea.publish(root, [unit()], record, cfg)
    assert first['preserved_files'] == []
    assert json.loads(cfg.read_text())['private_key_fixture'] == 'do-not-lose'
    code = root / 'OJ/Practice/T9999_Test/src/Main.java'
    code.write_text('class Main { int myAnswer = 3; }\n')
    second = lab_idea.publish(root, [unit()], record, cfg)
    assert second['preserved_files'] == ['OJ/Practice/T9999_Test/src/Main.java']
    assert 'myAnswer' in code.read_text()
    assert (root / 'settings.gradle').read_text().count("include 'OJ-Practice-T9999_Test'") == 1
    assert len(json.loads(cfg.read_text())['bindings']) == 1
    assert (root / 'OJ/Practice/lesson-info.yaml').read_text().count('T9999_Test') == 1


def test_changed_published_source_requires_review_and_does_not_overwrite(tmp_path):
    root, cfg = project(tmp_path), bridge(tmp_path)
    record = tmp_path / 'published.json'
    lab_idea.publish(root, [unit()], record, cfg)
    with pytest.raises(StageFailure, match='来源发生变化'):
        lab_idea.publish(root, [unit('class Main { int newContract; }')], record, cfg)
    assert (root / 'OJ/Practice/T9999_Test/src/Main.java').read_text() == 'class Main {}\n'


def test_source_revision_conflict_preserves_an_in_progress_answer(tmp_path):
    root, cfg = project(tmp_path), bridge(tmp_path)
    record = tmp_path / 'published.json'
    lab_idea.publish(root, [unit()], record, cfg)
    source = root / 'OJ/Practice/T9999_Test/src/Main.java'
    source.write_text('class Main { int myAnswer = 3; }\n')
    with pytest.raises(StageFailure, match='来源发生变化'):
        lab_idea.publish(root, [unit('class Main { int newContract; }')], record, cfg)
    assert source.read_text() == 'class Main { int myAnswer = 3; }\n'


def test_existing_binding_and_path_escape_fail_before_java_write(tmp_path):
    root, cfg = project(tmp_path), bridge(tmp_path)
    cfg.write_text(json.dumps({'bindings': [{'pid': 'T9999', 'project_root': str(root),
        'task_path': 'OJ/Practice/Other', 'source': 'src/Main.java', 'lang': 'java'}]}))
    with pytest.raises(StageFailure, match='绑定'):
        lab_idea.publish(root, [unit()], tmp_path / 'receipt', cfg)
    assert not (root / 'OJ/Practice/T9999_Test').exists()
    bad = unit();bad['files'][0]['path'] = 'src/../../outside.java'
    with pytest.raises(StageFailure, match='路径'):
        lab_idea.publish(root, [bad], tmp_path / 'receipt', cfg)
    assert not (tmp_path / 'outside.java').exists()


def test_ledger_deduplicates_and_connect_retry_does_not_regenerate(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    one = e.store.enqueue(assignment());two = e.store.enqueue(assignment())
    assert one['id'] == two['id']
    data = one['data'] | {'written': True}
    e.store.update(one['id'], stage='connect', data=data)
    calls = []
    def unavailable(job):
        calls.append(job['stage'])
        raise StageFailure('IDEA Windows 1455 页面文件不足', kind='retry')
    monkeypatch.setattr(e, 'connect', unavailable)
    assert e.step()
    result = e.store.get(one['id'])
    assert result['stage'] == 'connect' and result['state'] == 'retry_wait' and result['data']['written']
    assert calls == ['connect']
    assert not e.step()  # Backoff does not busy loop or start another generator.


def test_rejected_candidate_returns_to_generation_instead_of_editing(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    one = e.store.enqueue(assignment())
    e.store.update(one['id'], stage='verify')
    def wrong(job):
        raise StageFailure('源样例不一致', kind='regenerate', phase='generate')
    monkeypatch.setattr(e, 'verify', wrong)
    assert e.step()
    result = e.store.get(one['id'])
    assert result['stage'] == 'generate' and result['state'] == 'retry_wait'


def test_native_receipt_is_required_and_launch_is_rate_limited(tmp_path, monkeypatch):
    exe = tmp_path / 'idea64.exe';exe.touch()
    launched = []
    monkeypatch.setattr(lab_idea.subprocess, 'Popen', lambda *a, **k: launched.append(a[0]))
    monkeypatch.setattr(lab_idea, 'command', lambda *a, **k: None)
    stop = SimpleNamespace(wait=lambda seconds: False)
    config = {'project_root': str(tmp_path), 'idea_executable': str(exe)}
    for _ in range(2):
        with pytest.raises(StageFailure, match='加载回执'):
            lab_idea.connect(config, [], tmp_path, stop)
    assert len(launched) == 1
    scripts = list(tmp_path.glob('idea-*.groovy'))
    assert scripts and '__INPUT_JSON__' not in scripts[0].read_text()
    assert "Path.of('" + tmp_path.as_posix() in scripts[0].read_text()


def test_routes_guard_writes_and_distinguish_written_from_connected(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    one = e.store.enqueue(assignment())
    e.store.update(one['id'], state='retry_wait', stage='connect', data=one['data'] | {'written': True})
    app = Flask(__name__)
    def guard(write=False):
        if write and request.headers.get('X-CSRF-Token') != 'fixture':
            raise ValueError('CSRF')
    register_labs(app, e, guard, str)
    client = app.test_client()
    assert client.post('/api/labs/settings', json={'enabled': False}).status_code == 400
    record = client.get('/api/labs').json['jobs'][0]
    assert record['written'] and not record['connected'] and 'data' not in record
    assert client.post('/api/labs/settings', json={'enabled': False}, headers={'X-CSRF-Token': 'fixture'}).status_code == 200


def test_source_discovery_uses_week_cutoff_and_reuses_homework_download(tmp_path, monkeypatch):
    called = []
    class BB:
        def current_courses(self):return [{'id':'c','name':'CS109 Fixture'}]
        def results(self,path):
            if path.endswith('/contents'):return [{'id':'labs','title':'Labs'}]
            if path.endswith('/labs/children'):return [{'id':'eight','title':'Week 8'},{'id':'nine','title':'Week 9'},{'id':'future','title':'Week 10'}]
            return []
        def json(self,path):
            assert 'eight' not in path
            return {'title':'Week 9','modified':'revision','body':'<a href="https://bb.sustech.edu.cn/bbcswebdav/fixture?temporary=private">Exercise09.pdf</a>' if '/nine' in path else ''}
    pdf = tmp_path/'original.pdf';pdf.write_bytes(b'%PDF-fixture')
    def cached(bb,resource,root):
        called.append(resource)
        return {'path':str(pdf),'sha256':'original-sha','name':'Exercise09.pdf'}
    monkeypatch.setattr(lab_sources,'download',cached)
    monkeypatch.setattr(lab_sources,'PdfReader',lambda p:SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda:'Create a Circle class.')]))
    rows = lab_sources.collect(BB(),{'course_id':'c','min_week':9},tmp_path)
    assert len(rows)==1 and rows[0]['week']==9
    assert 'temporary' not in json.dumps(rows) and 'private' not in json.dumps(rows)
    assert called and rows[0]['fingerprint']


def test_launch_timestamp_is_persisted_even_when_receipt_never_arrives(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    cfg = e.configuration() | {'project_root':str(tmp_path),'idea_executable':str(tmp_path/'idea.exe')}
    e.store.set_setting('configuration',cfg)
    job=e.store.enqueue(assignment())
    data=job['data']|{'installation':{'registrations':[]},'written':True}
    job=e.store.update(job['id'],stage='connect',data=data)
    def connect(settings,*args):
        settings['_on_launch'](1234)
        raise StageFailure('still pending',kind='retry')
    monkeypatch.setattr(lab_idea,'connect',connect)
    with pytest.raises(StageFailure):e.connect(job)
    saved=e.configuration()
    assert saved['last_idea_launch']==1234 and '_on_launch' not in saved


def test_lab_discovery_never_renders_pdf_or_copies_booklet_tools(tmp_path, monkeypatch):
    from sustech_dashboard.homework_sources import file_sha
    from sustech_dashboard import lab_engine
    e=engine(tmp_path,monkeypatch)
    original=tmp_path/'source.pdf';original.write_bytes(b'%PDF fixture')
    item=assignment()|{'pdf':{'path':str(original),'sha256':file_sha(original)},
                      'requirement':'Complete two OJ problems.','oj':[],'fingerprint':'source-version'}
    job=e.store.enqueue(item)
    def no_subprocess(*a,**k):raise AssertionError('PDF discovery must not launch rendering tools')
    monkeypatch.setattr(lab_engine,'command',no_subprocess)
    e.discover(job)
    result=e.store.get(job['id'])
    assert result['stage']=='generate'
    assert list((e.workspace(result)/'workflow').iterdir())==[]
    assert not list(e.workspace(result).rglob('*.png'))
    assert set(result['data']['protected'])=={'source.pdf','input.json'}


def test_new_source_publish_never_replaces_a_racing_learner_file(tmp_path, monkeypatch):
    path=tmp_path/'Person.java'
    real_link=lab_idea.os.link
    def raced(source,target):
        Path(target).write_text('my in-progress answer')
        return real_link(source,target)
    monkeypatch.setattr(lab_idea.os,'link',raced)
    assert not lab_idea.create_source(path,b'generated starter')
    assert path.read_text()=='my in-progress answer'
    assert not list(tmp_path.glob('*.lab-pending-*'))


def test_upstream_signed_download_urls_never_reach_status(tmp_path, monkeypatch):
    import requests
    e=engine(tmp_path,monkeypatch)
    def failed(*args):raise requests.HTTPError('403 https://bb.sustech.edu.cn/file?ticket=secret-value')
    monkeypatch.setattr(lab_sources,'collect',failed)
    assert not e.step()
    message=e.store.setting('discovery_error')
    assert 'secret-value' not in message and 'https:' not in message and 'HTTPError' in message


def test_existing_sample_files_remain_registered_and_reset_template_is_not_answer(tmp_path):
    root,cfg=project(tmp_path),bridge(tmp_path)
    target=root/'OJ/Practice/T9999_Test';(target/'src').mkdir(parents=True)
    (target/'src/Main.java').write_text('class Main { int userAnswer = 7; }')
    (target/'src/Example1.java').write_text('class Example1 {}')
    (target/'task-info.yaml').write_text('type: edu\nfiles:\n  - name: src/Main.java\n  - name: src/Example1.java\nstatus: Unchecked\n')
    result=lab_idea.publish(root,[unit()],tmp_path/'receipt.json',cfg)
    registered=result['registrations'][0]
    assert registered['files']==['src/Main.java','src/Example1.java']
    assert registered['templates']['src/Main.java']=='class Main {}\n'
    assert 'userAnswer' not in json.dumps(registered['templates'])
