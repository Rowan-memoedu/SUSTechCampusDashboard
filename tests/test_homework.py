import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask

from sustech_dashboard.homework_backend import ApiBackend, confined
from sustech_dashboard.homework_engine import HomeworkEngine
from sustech_dashboard.homework_routes import register_homework
from sustech_dashboard.homework_sources import collect, download, file_sha, local_reference, supplement_resources
from sustech_dashboard.homework_store import HomeworkStore, digest


def assignment(id='a', course='c'):
    return {'course_id': course, 'content_id': id, 'course': 'Fixture Course', 'name': 'Ch 2',
            'status': 'not_submitted', 'due': None}


def snapshot(*items):
    return {'assignments': list(items), 'source_updated_at': {'blackboard': '2026-10-12T00:00:00+08:00'}, 'errors': {}}


def test_baseline_excludes_existing_but_explicit_canary_and_future_are_processed(tmp_path):
    store = HomeworkStore(tmp_path)
    old, canary, future = assignment('old'), assignment('canary'), assignment('future')
    store.enable(True, snapshot(old, canary))
    store.ingest(snapshot(old, canary))
    assert not store.list()
    first = store.enqueue(canary, explicit=True)
    store.ingest(snapshot(old, canary, future))
    store.ingest(snapshot(old, canary, future))
    assert len(store.list()) == 2
    assert store.enqueue(canary, explicit=True)['id'] == first['id']
    assert not store.course('c')['trusted']


def test_failed_first_sync_cannot_create_baseline(tmp_path):
    store = HomeworkStore(tmp_path)
    with pytest.raises(ValueError):
        store.enable(True, {'assignments': [assignment()]})
    assert not store.setting('enabled')


def test_concurrent_enqueues_and_claims_are_idempotent(tmp_path):
    store = HomeworkStore(tmp_path)
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: store.enqueue(assignment(), explicit=True)['id'], range(8)))
    assert len(set(ids)) == 1
    with ThreadPoolExecutor(4) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(4)))
    assert len([x for x in claimed if x]) == 1
    HomeworkStore(tmp_path).recover()
    assert store.get(ids[0])['state'] == 'queued'


def test_two_confirmations_and_stale_click_guard(tmp_path):
    store = HomeworkStore(tmp_path)
    job = store.enqueue(assignment(), explicit=True)
    data = job['data'] | {'source_digest': 'bound', 'plan': {'ids': ['2.1']}}
    job = store.update(job['id'], state='awaiting_source', stage='produce', data=data)
    stale = job['revision']
    job = store.decide(job['id'], job['revision'], 'accept_source', homework_copies=True)
    assert not store.course('c')['trusted']
    with pytest.raises(ValueError, match='变化'):
        store.decide(job['id'], stale, 'accept_source')
    data = job['data'] | {'artifacts': [{'module': 'questions'}]}
    job = store.update(job['id'], state='awaiting_pdf', data=data)
    job = store.decide(job['id'], job['revision'], 'accept_pdf')
    assert job['state'] == 'complete' and store.course('c')['trusted']
    assert store.course('c')['recipe']['homework_copies'] is True
    assert not store.course('another')['trusted']


def test_rejection_preserves_old_attempt_and_resets_pipeline(tmp_path):
    store = HomeworkStore(tmp_path)
    job = store.enqueue(assignment(), explicit=True)
    data = job['data'] | {'pipeline': {'english': True}, 'downloads': ['immutable'], 'artifacts': ['old']}
    job = store.update(job['id'], state='awaiting_pdf', data=data)
    with pytest.raises(ValueError):
        store.decide(job['id'], job['revision'], 'reject_pdf')
    job = store.decide(job['id'], job['revision'], 'reject_pdf', '题目原图缺少一部分')
    assert job['data']['attempt'] == 1 and 'pipeline' not in job['data']
    assert job['stage'] == 'extract' and not store.course('c')['trusted']


def test_api_file_tool_confines_paths_and_protects_sources(tmp_path):
    api = ApiBackend()
    with pytest.raises(ValueError):
        api.file_tool(tmp_path, {'action': 'read', 'path': '../private.txt'})
    with pytest.raises(ValueError):
        api.file_tool(tmp_path, {'action': 'write', 'path': 'workflow/verify.py', 'text': 'pass'})
    with pytest.raises(ValueError):
        api.file_tool(tmp_path, {'action': 'write', 'path': 'work/../workflow/verify.py', 'text': 'pass'})
    api.file_tool(tmp_path, {'action': 'write', 'path': 'work/draft.md', 'text': 'candidate'})
    assert api.file_tool(tmp_path, {'action': 'read', 'path': 'work/draft.md'})[0]['text'] == 'candidate'


def engine(tmp_path, rows=None):
    class BB:
        def json(self, path):
            return {'body': '<p>Chapter 2 problem 1.</p>'}
    return HomeworkEngine(tmp_path, lambda: snapshot(*(rows or [])), BB, threading.Lock())


def test_routes_have_write_guard_and_hide_workspace_paths(tmp_path):
    app = Flask(__name__)
    runner = engine(tmp_path, [assignment()])
    from flask import request
    def guard(write=False):
        if write and request.headers.get('X-CSRF-Token') != 'fixture':
            raise ValueError('页面令牌无效')
    register_homework(app, runner, guard, lambda _: 'query failed')
    client = app.test_client()
    assert client.post('/api/homework/enqueue', json=assignment()).status_code == 400
    one = client.post('/api/homework/enqueue', json=assignment(), headers={'X-CSRF-Token': 'fixture'}).json
    two = client.post('/api/homework/enqueue', json=assignment(), headers={'X-CSRF-Token': 'fixture'}).json
    assert one['id'] == two['id'] and 'data' not in one
    assert client.get('/api/homework/no-such/file/questions').status_code == 404
    assert client.get('/api/homework/' + one['id'] + '/file/english').status_code == 404


def test_deliver_never_overwrites_written_pdf(tmp_path):
    runner = engine(tmp_path / 'private')
    runner.store.set_setting('output_root', str(tmp_path / 'output'))
    work = tmp_path / 'candidate'
    (work / 'work').mkdir(parents=True)
    for name in ('题目.md', '答案册-英文.md', '答案册.md'):
        (work / 'work' / name).write_text('fixture', encoding='utf-8')
    source = work / 'q.pdf'
    source.write_bytes(b'new verified fixture bytes')
    original = tmp_path / 'output/Course/题目/Course_HW_题目册.pdf'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'user handwriting')
    job = {'id': 'a' * 32, 'data': {}}
    report = {'pdfs': [{'module': 'questions', 'verified_source': str(source),
                       'file': original.name, 'pages': 1, 'sha256': file_sha(source)}]}
    results = runner.deliver(work, job, {'subject': 'Course', 'label': 'HW'}, report)
    assert original.read_bytes() == b'user handwriting'
    assert results[0]['path'] != str(original)
    assert Path(results[0]['path']).read_bytes() == source.read_bytes()


def test_source_collection_includes_announcement_attachment_and_excludes_student_history(monkeypatch):
    from sustech_dashboard import academic_extras
    monkeypatch.setattr(academic_extras, 'announcements', lambda *args: {'items': [{
        'title': 'Reference book', 'body_html': '<a href="/api/blackboard-resource?path=%2Fbbcswebdav%2Fbook.pdf">Book</a>'}]})
    class BB:
        def current_courses(self): return [{'id': 'c'}]
        def json(self, path): return {'body': '<p>2.1, 2.4</p>', 'studentComments': 'private'}
        def attachments(self, cid): return []
        def _contents(self, cid): return []
    data, files = collect(BB(), assignment())
    assert len(files) == 1 and data['resources'][0]['origin'] == '公告'
    assert data['requirement'] == '2.1, 2.4'
    assert 'private' not in json.dumps(data) and 'path' not in data['resources'][0]


def test_frozen_source_digest_includes_original_question_figures(tmp_path):
    runner = engine(tmp_path / 'private')
    root = tmp_path / 'job'
    (root / 'work/figures').mkdir(parents=True)
    (root / 'work/题目.md').write_text('### Problem 2.1\n\n![figure](figures/figure.png)', encoding='utf-8')
    original = root / 'book.pdf'
    original.write_bytes(b'original textbook')
    figure = root / 'work/figures/figure.png'
    figure.write_bytes(b'original figure')
    data = {'input': {'requirement': '2.1'}, 'plan': {'source_pages': []}, 'downloads': [{'path': str(original)}]}
    approved = runner.source_digest(root, data)
    figure.write_bytes(b'changed figure')
    assert approved != runner.source_digest(root, data)


def test_trusted_course_auto_processes_new_job_but_never_auto_approves_correction(tmp_path, monkeypatch):
    runner = engine(tmp_path)
    first = runner.store.enqueue(assignment('first'), explicit=True)
    first = runner.store.update(first['id'], state='awaiting_pdf', data=first['data'] | {
        'plan': {'ids': ['2.1']}, 'artifacts': [{'module': 'questions'}]})
    runner.store.decide(first['id'], first['revision'], 'accept_pdf')
    assert runner.store.course('c')['trusted']
    later = runner.store.enqueue(assignment('later'), explicit=True)
    later = runner.store.update(later['id'], state='awaiting_source', stage='produce',
        data=later['data'] | {'source_digest': 'frozen'})
    monkeypatch.setattr(runner, 'source_digest', lambda *args: 'frozen')
    produced = []
    monkeypatch.setattr(runner, 'produce', lambda job: produced.append(job['id']))
    assert runner.step() and produced == [later['id']]
    later = runner.store.update(later['id'], state='needs_attention', error='reference needs correction')
    later = runner.store.decide(later['id'], later['revision'], 'correct_source', 'Use revised textbook')
    later = runner.store.update(later['id'], state='awaiting_source', stage='produce')
    assert not runner.step()
    assert runner.store.get(later['id'])['state'] == 'awaiting_source'


def test_changed_teacher_requirement_cannot_be_accepted(tmp_path):
    runner = engine(tmp_path)
    job = runner.store.enqueue(assignment(), explicit=True)
    job = runner.store.update(job['id'], state='awaiting_source', data=job['data'] | {
        'source_digest': 'frozen', 'requirement_digest': digest('old teacher requirement')})
    runner.source_digest = lambda *args: 'frozen'
    app = Flask(__name__)
    register_homework(app, runner, lambda **kw: None, lambda _: 'failed')
    result = app.test_client().post('/api/homework/' + job['id'] + '/decision', json={
        'revision': job['revision'], 'action': 'accept_source'})
    assert result.status_code == 400 and '修改作业要求' in result.json['error']
    assert runner.store.get(job['id'])['state'] == 'awaiting_source'


def test_retry_records_failure_and_restarts_only_production_stage(tmp_path):
    store = HomeworkStore(tmp_path)
    job = store.enqueue(assignment(), explicit=True)
    job = store.update(job['id'], state='failed', stage='produce', error='translation omitted a paragraph',
        data=job['data'] | {'active_phase': 'translation', 'pipeline': {'english': True, 'calculation': True}})
    retried = store.decide(job['id'], job['revision'], 'retry')
    assert retried['data']['retry_production'] is True
    assert retried['data']['pipeline'] == {'english': True, 'calculation': True}
    assert retried['data']['feedback'][-1]['text'] == 'translation omitted a paragraph'


def test_graceful_shutdown_requeues_current_stage(tmp_path, monkeypatch):
    runner = engine(tmp_path)
    job = runner.store.enqueue(assignment(), explicit=True)
    def interrupted(job):
        runner.stop.set()
        raise RuntimeError('cancelled child')
    monkeypatch.setattr(runner, 'discover', interrupted)
    assert runner.step()
    after = runner.store.get(job['id'])
    assert after['state'] == 'queued' and after['stage'] == 'discover'


def test_translation_retry_starts_from_accepted_source_and_archives_failed_candidate(tmp_path, monkeypatch):
    runner = engine(tmp_path)
    job = runner.store.enqueue(assignment(), explicit=True)
    data = job['data'] | {'active_phase': 'translation', 'retry_production': True,
        'source_approved': 'bound', 'plan': {'ids': ['2.1']},
        'pipeline': {'english': True, 'calculation': True}}
    job = runner.store.update(job['id'], state='running', stage='produce', data=data)
    root = runner.workspace(job)
    (root / 'work').mkdir(parents=True)
    (root / 'work/答案册-英文.md').write_text('accepted source', encoding='utf-8')
    (root / 'work/答案册.md').write_text('failed candidate', encoding='utf-8')
    monkeypatch.setattr(runner, 'source_digest', lambda *args: 'bound')
    def observe(root, stage, *args, **kwargs):
        assert stage == 'translation'
        assert not (root / 'work/答案册.md').exists()
        assert (root / 'work/答案册-英文.md').read_text(encoding='utf-8') == 'accepted source'
        assert next((root / 'rejected').glob('*/答案册.md')).read_text(encoding='utf-8') == 'failed candidate'
        raise RuntimeError('observed before AI call')
    monkeypatch.setattr(runner, 'ai', observe)
    with pytest.raises(RuntimeError, match='observed before AI call'):
        runner.produce(job)


@pytest.mark.parametrize('review_passes', [True, False])
def test_formula_word_order_reaches_independent_review_without_bypassing_failure(tmp_path, monkeypatch, review_passes):
    import sustech_dashboard.homework_engine as module
    runner = engine(tmp_path)
    job = runner.store.enqueue(assignment(), explicit=True)
    job = runner.store.update(job['id'], stage='produce', data=job['data'] | {
        'source_approved': 'bound', 'plan': {'ids': ['1']},
        'pipeline': {'english': True, 'calculation': True}})
    root = runner.setup(job)
    english = '### Problem 1\nFor $x>0$, $x^2>0$.\n#### Solution\nPositive squares are positive.'
    (root / 'work/答案册-英文.md').write_text(english, encoding='utf-8')
    monkeypatch.setattr(runner, 'source_digest', lambda *args: 'bound')
    def translated(root, stage, *args):
        assert stage == 'translation'
        (root / 'work/答案册.md').write_text(
            '### Problem 1\n$x^2>0$，条件是 $x>0$。\n#### Solution\n正数的平方为正。', encoding='utf-8')
        return {'status': 'ready'}
    monkeypatch.setattr(runner, 'ai', translated)
    reviewed = []
    def review(root, stage, instruction, ids):
        reviewed.append(stage)
        comparison = json.loads((root / 'work/review/translation/formula-comparison.json').read_text(encoding='utf-8'))
        assert not comparison['exact_match']
        assert comparison['source'] == ['$x>0$', '$x^2>0$']
        assert comparison['translation'] == ['$x^2>0$', '$x>0$']
        if not review_passes:
            raise ValueError('independent scientific review failed')
        return {'status': 'PASS'}
    monkeypatch.setattr(runner, 'review', review)
    monkeypatch.setattr(module, 'toolchain', lambda _: {'tectonic': ''})
    with pytest.raises(ValueError, match='Tectonic' if review_passes else 'scientific review failed'):
        runner.produce(job)
    assert reviewed == ['translation-review']
    assert bool(runner.store.get(job['id'])['data']['pipeline'].get('translation')) == review_passes
    assert (root / 'work/review/translation/accepted.json').exists() == review_passes
    assert (root / 'work/答案册-英文.md').read_text(encoding='utf-8') == english


@pytest.mark.parametrize('tamper', ['none', 'prose', 'page', 'candidate'])
@pytest.mark.parametrize('has_previous', [False, True])
def test_figure_crops_require_readonly_review_and_preserve_originals(tmp_path, monkeypatch, tamper, has_previous):
    import sustech_dashboard.homework_engine as module
    runner = engine(tmp_path / 'private')
    root = tmp_path / 'job'
    (root / 'work/figures').mkdir(parents=True)
    (root / 'work/review/source-pages').mkdir(parents=True)
    (root / 'work/题目.md').write_text('### Problem 2.1\n\n![figure](figures/one.png)', encoding='utf-8')
    english = root / 'work/答案册-英文.md'
    english.write_text('accepted English', encoding='utf-8')
    original = root / 'work/figures/one.png'
    original.write_bytes(b'previous derived crop')
    source_page = root / 'work/review/source-pages/one.png'
    source_page.write_bytes(b'immutable source page')
    book = root / 'book.pdf'
    book.write_bytes(b'immutable book')
    data = {'input': {}, 'plan': {'ids': ['2.1'], 'source_pages': [
        {'id': '2.1', 'images': ['work/review/source-pages/one.png']}]}, 'downloads': [{'path': str(book)}]}
    previous = 'work/review/figure-candidates/previous/one.png'
    if has_previous:
        (root / previous).parent.mkdir(parents=True)
        (root / previous).write_bytes(b'previous incomplete candidate')
        (root / 'work/review/figure-crops.json').write_text(json.dumps({'crops': [{
            'source': 'work/review/source-pages/one.png', 'target': previous,
            'original': 'figures/one.png', 'box': [3, 4, 8, 9]}]}), encoding='utf-8')
        (root / 'work/review/figure-review.json').write_text(json.dumps({
            'status': 'FAIL', 'findings': ['The upper vertex is cropped.']}), encoding='utf-8')
    def fake_crop(args, *a, **kw):
        manifest = json.loads(Path(args[-1]).read_text(encoding='utf-8'))
        target = root / manifest['crops'][0]['target']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'exact deterministic crop')
    monkeypatch.setattr(module, 'command', fake_crop)
    def ai(root, stage, instruction, schema, images=()):
        if stage == 'figure-boxes':
            if has_previous:
                context = json.loads((root / 'work/review/figure-input.json').read_text(encoding='utf-8'))
                assert context['previous']['review']['findings'] == ['The upper vertex is cropped.']
                assert context['previous']['crops']['crops'][0]['box'] == [3, 4, 8, 9]
                assert previous in images
            return {'status': 'ready', 'notes': 'fixture', 'figures': [{'path': 'figures/one.png',
                'source_page': 'work/review/source-pages/one.png', 'box': [1, 2, 9, 10], 'notes': 'fixture'}]}
        assert stage == 'figure-review'
        if tamper == 'prose': english.write_text('changed English', encoding='utf-8')
        if tamper == 'page': source_page.write_bytes(b'changed source page')
        if tamper == 'candidate': (root / images[-1]).write_bytes(b'reviewer changed crop')
        return {'status': 'PASS', 'notes': 'fixture', 'findings': [], 'reviewed_pages': list(images),
                'problems': [{'id': '2.1'}]}
    monkeypatch.setattr(runner, 'ai', ai)
    if tamper != 'none':
        with pytest.raises(ValueError): runner.prepare_figures(root, data)
        assert original.read_bytes() == b'previous derived crop'
    else:
        runner.prepare_figures(root, data)
        assert original.read_bytes() == b'exact deterministic crop'
        assert next((root / 'work/review/figure-originals').glob('*/*.png')).read_bytes() == b'previous derived crop'
        assert english.read_text(encoding='utf-8') == 'accepted English'
        assert source_page.read_bytes() == b'immutable source page'


def test_initial_enable_uses_fresh_school_list_not_stale_successful_cache(tmp_path):
    old = assignment('cached')
    missed = assignment('published-before-enable')
    class BB:
        unclassified = 0
        def assignments(self): return [old, missed]
    runner = HomeworkEngine(tmp_path, lambda: snapshot(old), BB, threading.Lock())
    runner.enable(True)
    runner.store.ingest(snapshot(old, missed))
    assert not runner.store.list()
    runner.store.ingest(snapshot(old, missed, assignment('new-after-enable')))
    assert len(runner.store.list()) == 1


def test_a_baselined_record_can_only_be_reopened_explicitly(tmp_path):
    store = HomeworkStore(tmp_path)
    first = store.enqueue(assignment(), explicit=True)
    store.update(first['id'], state='baseline')
    assert store.enqueue(assignment())['state'] == 'baseline'
    reopened = store.enqueue(assignment(), explicit=True)
    assert reopened['state'] == 'queued' and reopened['stage'] == 'discover'
    assert reopened['id'] == first['id'] and reopened['data']['attempt'] == 1


def test_announcement_without_filename_extension_reuses_verified_original(tmp_path):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, *args): yield b'%PDF-1.4\nfixture source'
    class BB:
        calls = 0
        def get(self, *args, **kw):
            self.calls += 1
            return Response()
    bb = BB()
    source = {'name': 'Reference book', 'key': 'key', 'title': 'Announcement', 'path': '/bbcswebdav/book'}
    first = download(bb, source, tmp_path)
    assert Path(first['path']).suffix == '.pdf'
    assert download(bb, source, tmp_path) == first and bb.calls == 1


def test_local_textbook_is_staged_without_network_or_exposing_original_path(tmp_path):
    book = tmp_path / 'original.pdf'
    book.write_bytes(b'%PDF-1.4\nlocal textbook fixture')
    reference = local_reference(book, 'Fixture textbook')
    source, resources = supplement_resources({'resources': []}, {}, [reference])
    assert 'path' not in source['resources'][0] and 'kind' not in source['resources'][0]
    staged = download(None, resources[reference['key']], tmp_path / 'staged')
    assert Path(staged['path']) != book
    assert Path(staged['path']).read_bytes() == book.read_bytes()
    assert file_sha(book) == reference['sha256']
    assert download(None, reference, tmp_path / 'staged') == staged
    book.write_bytes(b'%PDF-1.4\nchanged textbook')
    with pytest.raises(ValueError, match='变化'):
        supplement_resources({'resources': []}, {}, [reference])
    with pytest.raises(ValueError, match='变化'):
        download(None, reference, tmp_path / 'staged')


def test_local_textbook_rejects_non_pdf_and_modified_staged_copy(tmp_path):
    book = tmp_path / 'original.pdf'
    book.write_text('not a PDF', encoding='utf-8')
    with pytest.raises(ValueError):
        local_reference(book)
    book.write_bytes(b'%PDF-1.4\ntextbook fixture')
    reference = local_reference(book)
    staged = download(None, reference, tmp_path / 'staged')
    Path(staged['path']).write_bytes(b'%PDF-1.4\nuser changes')
    with pytest.raises(ValueError, match='已保留'):
        download(None, reference, tmp_path / 'staged')


def test_discovery_uses_only_local_references_bound_to_its_course(tmp_path, monkeypatch):
    import sustech_dashboard.homework_engine as module
    runner = engine(tmp_path / 'private')
    references = []
    for course in ('c', 'other-course'):
        book = tmp_path / (course + '.pdf')
        book.write_bytes(b'%PDF-1.4\n' + course.encode())
        references.append(local_reference(book, course + ' textbook'))
        runner.store.set_setting('course_references:' + course, [references[-1]])
    monkeypatch.setattr(module, 'collect', lambda *args: ({'requirement': '2.1', 'resources': []}, {}))
    def discover(root, stage, *args):
        value = json.loads((root / 'input.json').read_text(encoding='utf-8'))
        assert [r['key'] for r in value['resources']] == [references[0]['key']]
        return {'resource_keys': [references[0]['key']], 'ids': ['2.1']}
    monkeypatch.setattr(runner, 'ai', discover)
    job = runner.store.enqueue(assignment(), explicit=True)
    runner.discover(job)
    updated = runner.store.get(job['id'])
    assert updated['stage'] == 'extract'
    assert updated['data']['downloads'][0]['sha256'] == references[0]['sha256']
