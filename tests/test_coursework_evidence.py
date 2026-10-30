from datetime import date
from types import SimpleNamespace

from sustech_dashboard.coursework import deadline, submission, collect


def column(due=None):
    return {'id': 'column-a', 'contentId': 'content-a', 'name': 'Homework', 'grading': {'due': due}}


def event(when, course='course-a', cid='column-a', end=None):
    return {'calendarId': course, 'itemSourceId': cid, 'startDate': when, 'endDate': end or when,
            'dynamicCalendarItemProps': {'handle': 'resource/x-bb-assignment'}}


def test_date_only_and_unzoned_times_never_create_a_precise_countdown():
    result = deadline(column('2026-10-20'), 'course-a')
    assert result['due'] is None and result['due_date_only'] == '2026-10-20'
    assert result['due_precision'] == 'date'
    assert deadline(column('2026-10-20T18:00:00'), 'course-a')['due'] is None
    assert deadline(column('2026-02-30'), 'course-a')['due_precision'] == 'unknown'


def test_calendar_fallback_requires_same_course_and_column_and_recognized_handler():
    valid = event('2026-10-20T10:00:00Z')
    for altered in (dict(valid, calendarId='other'), dict(valid, itemSourceId='other'),
                    dict(valid, dynamicCalendarItemProps={'handle': 'personal'})):
        assert deadline(column(), 'course-a', [altered])['due'] is None
    assert deadline(column(), 'course-a', [valid])['due_source'] == 'calendar'


def test_same_instant_and_day_upgrade_precision_without_false_conflict():
    result = deadline(column('2026-10-20T10:00:00Z'), 'course-a', [event('2026-10-20T18:00:00+08:00')])
    assert not result['due_conflict']
    result = deadline(column('2026-10-20'), 'course-a', [event('2026-10-20T18:00:00+08:00')])
    assert result['due_precision'] == 'time' and result['due_source'] == 'calendar'


def test_conflicting_calendar_does_not_overwrite_official_due():
    result = deadline(column('2026-10-20T10:00:00Z'), 'course-a', [event('2026-10-21T10:00:00Z')])
    assert result['due'] == '2026-10-20T10:00:00Z' and result['due_conflict']
    ranged = deadline(column(), 'course-a', [event('2026-10-20T10:00:00Z', end='2026-10-21T10:00:00Z')])
    assert ranged['due_conflict'] and ranged['due'] is None


def test_receipts_and_historical_submission_are_independent_of_latest_draft():
    attempts = [{'id': 'a1', 'status': 'Completed', 'created': '2026-10-18T10:00:00Z'},
                {'id': 'a2', 'status': 'InProgress', 'created': '2026-10-19T10:00:00Z'}]
    result = submission(attempts)
    assert result['status'] == 'submitted' and result['latest_attempt_state'] == 'draft'
    assert submission([{'id': 'a', 'score': 100}])['status'] == 'unknown'
    assert submission([{'id': 'a', 'attemptReceipt': {'receiptId': 'r', 'attemptId': 'a'}}])['submission_evidence'] == 'receipt'


def test_cross_course_or_mismatched_receipt_never_proves_submission():
    assert submission([{'status': 'Completed', 'courseId': 'other'}], 'course-a')['status'] == 'unknown'
    assert submission([{'id': 'a', 'attemptReceipt': {'receiptId': 'r', 'attemptId': 'other'}}])['status'] == 'unknown'
    assert submission(None)['status'] == 'unknown'
    assert submission([])['status'] == 'not_submitted'
    assert submission(['malformed'])['status'] == 'unknown'
    assert submission([{'created': {}, 'score': 100}])['status'] == 'unknown'
    assert submission([{'status': {}}])['status'] == 'unknown'
    assert submission([{'attemptReceipt': {'receiptId': {}}}])['status'] == 'unknown'
    assert deadline(column(), 'course-a', [dict(event('2026-10-20T10:00:00Z'), dynamicCalendarItemProps=[])])['due'] is None


def fixture(monkeypatch):
    from sustech_dashboard import academic_extras
    monkeypatch.setattr(academic_extras, 'user_grades', lambda bb, cid: {})
    scope = {'enrollment_start': date(2026, 8, 1), 'start': date(2026, 9, 1), 'end': date(2027, 1, 31)}
    bb = SimpleNamespace(warnings=[], _scope=scope, _coursework_calendar=([], True))
    bb.current_courses = lambda: [{'id': 'course-a', 'name': 'A'}, {'id': 'course-b', 'name': 'B'}]
    def columns(cid):
        if cid == 'course-b':
            raise RuntimeError('private response')
        return [(column('2026-10-20T10:00:00Z'), {})]
    bb.current_assignment_columns = columns
    bb.results = lambda path: []
    return bb, scope


def test_failed_course_retains_cache_and_successful_course_refreshes(monkeypatch):
    bb, scope = fixture(monkeypatch)
    old = [{'course': 'B', 'course_id': 'course-b', 'content_id': 'old', 'due': None, 'status': 'draft'}]
    result = collect(bb, old, {k: str(v) for k, v in scope.items()})
    assert len(result) == 2 and result[0]['course_id'] == 'course-a'
    assert result[1]['stale'] and 'stale' not in old[0]
    assert [c['state'] for c in bb.assignment_coverage] == ['complete', 'failed']
    assert 'private' not in str(bb.assignment_coverage)


def test_old_term_cache_is_never_retained_for_failed_course(monkeypatch):
    bb, scope = fixture(monkeypatch)
    result = collect(bb, [{'course_id': 'course-b', 'course': 'B'}], {'start': '2025-09-01'})
    assert len(result) == 1 and bb.assignment_coverage[1]['retained'] == 0


def test_failed_attempt_stays_unknown_without_erasing_other_courses(monkeypatch):
    bb, scope = fixture(monkeypatch)
    bb.results = lambda path: (_ for _ in ()).throw(RuntimeError('cookie=private'))
    result = collect(bb)
    assert result[0]['status'] == 'unknown'
    assert bb.assignment_coverage[0]['state'] == 'partial'
    assert 'private' not in str(result)


def test_revoked_course_permission_does_not_restore_private_cached_tasks(monkeypatch):
    from requests import Response
    from requests.exceptions import HTTPError
    bb, scope = fixture(monkeypatch)
    response = Response()
    response.status_code = 403
    bb.current_assignment_columns = lambda cid: (_ for _ in ()).throw(HTTPError(response=response))
    result = collect(bb, [{'course_id': 'course-b', 'course': 'B'}], {k: str(v) for k, v in scope.items()})
    assert result == []
    assert all(c['retained'] == 0 and c['errors'] == ['access_unavailable'] for c in bb.assignment_coverage)


def test_restricted_content_never_looks_like_complete_empty_course(monkeypatch):
    bb, _ = fixture(monkeypatch)
    bb._assignment_restrictions = {'course-a': 1}
    bb.current_assignment_columns = lambda cid: []
    assert collect(bb) == []
    assert bb.assignment_coverage[0]['state'] == 'partial'
    assert bb.assignment_coverage[0]['errors'] == ['restricted_content']


def test_single_content_failure_retains_only_that_record_and_refreshes_the_rest(monkeypatch):
    bb, scope = fixture(monkeypatch)
    bb.current_courses = lambda: [{'id': 'course-a', 'name': 'A'}]
    bb._assignment_content_failures = {'course-a': ['slow']}
    previous = [{'course_id': 'course-a', 'course': 'A', 'content_id': 'slow', 'status': 'draft'},
                {'course_id': 'course-a', 'course': 'A', 'content_id': 'removed', 'status': 'submitted'}]
    result = collect(bb, previous, {k: str(v) for k,v in scope.items()})
    assert len(result) == 2
    assert {x['content_id'] for x in result if x.get('stale')} == {'slow'}
    assert bb.assignment_coverage[0]['received'] == 1 and bb.assignment_coverage[0]['retained'] == 1
    assert bb.assignment_coverage[0]['state'] == 'partial'
    assert 'content_unavailable' in bb.assignment_coverage[0]['errors']
    assert len(collect(bb, previous, {'start': 'old term'})) == 1


def test_sync_preserves_course_coverage_scope_and_independent_scan_failure(monkeypatch, tmp_path):
    from sustech_dashboard import app, quotas
    from sustech_dashboard.provider import Blackboard
    import sustech_survival
    bb, scope = fixture(monkeypatch)
    bb.assignments = Blackboard.assignments.__get__(bb)
    monkeypatch.setattr(app, 'Blackboard', lambda: bb)
    monkeypatch.setattr(app, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(app, 'SNAPSHOT_PATH', tmp_path / 'snapshot.json')
    monkeypatch.setattr(app, 'read_tis', lambda: {})
    monkeypatch.setattr(app, 'read_bookings', lambda: {})
    monkeypatch.setattr(quotas, 'check_private_quota', lambda *args: None)
    monkeypatch.setattr(sustech_survival, 'Context', lambda **kw: SimpleNamespace(weather_cond='', temperature=None, aqi_value=None))
    monkeypatch.setattr(app, 'scan_materials', lambda: (_ for _ in ()).throw(RuntimeError('fixture scan failed')))
    old = {'semester_scope': {k: str(v) for k, v in scope.items()},
           'assignments': [{'course': 'B', 'course_id': 'course-b', 'content_id': 'old', 'status': 'draft'}]}
    app.save_json(app.SNAPSHOT_PATH, old)
    result = app.sync_all()
    assert result['assignment_coverage'][1]['retained'] == 1
    assert result['assignments'][1]['stale'] is True
    assert result['semester_scope'] == old['semester_scope']
    assert 'attachments' in result['errors'] and 'blackboard' not in result['errors']
    assert app.load_json(app.SNAPSHOT_PATH, {})['assignment_coverage'] == result['assignment_coverage']
    monkeypatch.setattr(app, 'Blackboard', lambda: (_ for _ in ()).throw(TimeoutError('private ticket')))
    failed = app.sync_all()
    assert all(a['stale'] for a in failed['assignments'])
    assert all(c['state'] == 'failed' for c in failed['assignment_coverage'])
    assert failed['semester_scope'] == old['semester_scope']
    assert 'private ticket' not in str(failed)


def test_detail_receipt_proof_does_not_broaden_operation_confirmation(monkeypatch):
    from sustech_dashboard import blackboard_work as work, academic_extras
    monkeypatch.setattr(academic_extras, 'user_grades', lambda *args: {})
    attempts = [{'id': 'a', 'status': 'NotAttempted', 'attemptReceipt': {'receiptId': 'r', 'attemptId': 'a'}}]
    col = column('2026-10-20')
    monkeypatch.setattr(work, 'assignment_record', lambda *args: ({'name': 'A'}, col, {'contentHandler': {'id': 'test'}}, attempts))
    bb = SimpleNamespace(_coursework_calendar=([event('2026-10-20T10:00:00Z')], True),
                         json=lambda path: {}, results=lambda path: [])
    result = work.detail(bb, 'course-a', 'content-a')
    assert result['status'] == 'submitted' and result['submission_evidence'] == 'receipt'
    assert result['action_status'] == 'not_submitted' and not result['can_submit']
    assert result['due_source'] == 'calendar' and result['due_precision'] == 'time'
