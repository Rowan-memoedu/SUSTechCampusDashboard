"""Read-only coursework evidence and course-scoped refresh results.

Keep the strict material scanner unchanged: partial display results must never
become an attachment baseline. School records and local UI annotations are
independent; no completion flag here writes to Blackboard.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import re
from typing import Any
from urllib.parse import urlencode

from requests.exceptions import HTTPError

from .core import CHINA_TZ, parse_dt, submission_status


def _date(value: Any) -> dict:
    if not isinstance(value, str) or not value:
        return {'value': None, 'precision': 'unknown'}
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            date.fromisoformat(value)
            return {'value': value, 'precision': 'date'}
        except ValueError:
            pass
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})', value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return {'value': value, 'precision': 'time'}
        except ValueError:
            pass
    return {'value': None, 'precision': 'unknown'}


def _same_date(left: dict, right: dict) -> bool:
    if left['precision'] == right['precision'] == 'time':
        return parse_dt(left['value']) == parse_dt(right['value'])
    if left['precision'] == right['precision'] == 'date':
        return left['value'] == right['value']
    precise, day = (left, right) if left['precision'] == 'time' else (right, left)
    return precise['precision'] == 'time' and day['precision'] == 'date' and parse_dt(precise['value']).date().isoformat() == day['value']


def deadline(column: dict, course_id: str, events: list[dict] = ()) -> dict:
    """Only combine the investigated SUSTech course/column calendar binding."""
    standard = _date((column.get('grading') or {}).get('due'))
    candidates = []
    if standard['value']:
        candidates.append(dict(standard, source='gradebook'))
    for event in events:
        props = event.get('dynamicCalendarItemProps') or {}
        if (not isinstance(props, dict) or not course_id or not column.get('id')
                or event.get('calendarId') != course_id or event.get('itemSourceId') != column.get('id')
                or props.get('handle') not in {'resource/x-bb-assignment', 'resource/x-bb-asmt-test-link'}):
            continue
        start, end = _date(event.get('startDate')), _date(event.get('endDate'))
        # A time range is not a single proven due time. Preserve the ambiguity.
        for candidate in (start, end):
            if candidate['value'] and not any(_same_date(candidate, old) and candidate['precision'] == old['precision'] for old in candidates):
                candidates.append(dict(candidate, source='calendar'))
    conflict = any(not _same_date(a, b) for a in candidates for b in candidates)
    chosen = dict(standard, source='gradebook') if standard['value'] else next(iter(candidates), {'value': None, 'precision': 'unknown', 'source': 'unknown'})
    # If the official field only states a day, a matching calendar instant can
    # supply the missing precision. Conflicts never silently change that field.
    if not conflict and chosen['precision'] == 'date':
        chosen = next((c for c in candidates if c['precision'] == 'time'), chosen)
    if conflict and not standard['value']:
        chosen = {'value': None, 'precision': 'unknown', 'source': 'unknown'}
    return {'due': chosen['value'] if chosen['precision'] == 'time' else None,
            'due_date_only': chosen['value'] if chosen['precision'] == 'date' else None,
            'due_precision': chosen['precision'], 'due_source': chosen['source'],
            'due_conflict': conflict, 'due_candidates': candidates,
            'standard_due': standard['value']}


def submission(attempts: list[dict] | None, course_id: str = '', column_id: str = '') -> dict:
    """A known attempt status or matching receipt proves submission, never a grade."""
    if not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts):
        return {'status': 'unknown', 'submission_evidence': 'unavailable', 'latest_attempt_state': 'unknown',
                'submitted_at': None, 'attempts_complete': False}
    receipt_times = []
    has_receipt = False
    for attempt in attempts:
        if attempt.get('status') is not None and not isinstance(attempt['status'], str):
            return submission(None)
        for key, expected in (('courseId', course_id), ('columnId', column_id)):
            if expected and attempt.get(key) and attempt[key] != expected:
                return submission(None)
        receipt = attempt.get('attemptReceipt')
        if not isinstance(receipt, dict):
            continue
        if any(receipt.get(k) and expected and receipt[k] != expected
               for k, expected in (('courseId', course_id), ('columnId', column_id), ('attemptId', attempt.get('id')))):
            return submission(None)
        when = _date(receipt.get('submissionDate'))
        receipt_id = receipt.get('receiptId')
        if (isinstance(receipt_id, str) and receipt_id.strip()) or when['precision'] == 'time':
            has_receipt = True
            if when['precision'] == 'time':
                receipt_times.append(when['value'])
    state = submission_status(attempts)
    if has_receipt:
        state = 'submitted'
    dated = [(parse_dt(a.get('created')) if _date(a.get('created'))['precision'] == 'time' else None, a) for a in attempts]
    latest = max(dated, key=lambda item: item[0])[1] if dated and all(t for t, _ in dated) else attempts[0] if len(attempts) == 1 else None
    latest_state = {'InProgress': 'draft', 'InProgressAgain': 'draft', 'NeedsGrading': 'needs_grading',
                    'NeedsGradingAgain': 'needs_grading', 'Completed': 'completed', 'NotAttempted': 'not_attempted'}.get((latest or {}).get('status'), 'unknown')
    evidence = 'receipt' if has_receipt else 'attempt_status' if state in {'submitted', 'draft'} else 'complete_attempt_list' if state == 'not_submitted' else 'unknown'
    return {'status': state, 'submission_evidence': evidence, 'latest_attempt_state': latest_state,
            'submitted_at': max(receipt_times, key=parse_dt) if receipt_times else None, 'attempts_complete': True}


def calendar_events(bb) -> tuple[list[dict], bool]:
    if hasattr(bb, '_coursework_calendar'):
        return bb._coursework_calendar
    now = datetime.now(CHINA_TZ)
    query = urlencode({'since': (now-timedelta(days=183)).date().isoformat(),
                       'until': (now+timedelta(days=183)).date().isoformat()})
    try:
        events = bb.results('/learn/api/v1/calendars/calendarItems?' + query)
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError('Invalid calendar envelope')
        result = events, True
    except Exception:
        result = [], False
    bb._coursework_calendar = result
    return result


def _scope_equal(current: dict, previous: dict | None) -> bool:
    keys = ('enrollment_start', 'start', 'end')
    return bool(previous and current and all(str(current.get(k, '')) == str(previous.get(k, '')) for k in keys))


def collect(bb, previous: list[dict] = (), previous_scope: dict | None = None) -> list[dict]:
    """Refresh successful courses; retain failed courses only in the same term."""
    from .academic_extras import user_grades, exempt
    courses = bb.current_courses()
    checked_at = datetime.now(CHINA_TZ).isoformat()
    events, calendar_ok = calendar_events(bb)
    output, coverage = [], []
    same_scope = _scope_equal(getattr(bb, '_scope', {}), previous_scope)
    for course in courses:
        cid = course['id']
        start = len(output)
        errors = []
        try:
            columns = bb.current_assignment_columns(cid)
        except Exception as exc:
            access_denied = isinstance(exc, HTTPError) and exc.response is not None and exc.response.status_code in {401, 403, 404}
            cached = [dict(deepcopy(item), stale=True) for item in previous if same_scope and not access_denied and item.get('course_id') == cid]
            output.extend(cached)
            coverage.append({'course_id': cid, 'course': course['name'], 'state': 'failed',
                             'checked_at': checked_at, 'received': 0, 'retained': len(cached),
                             'errors': ['access_unavailable' if access_denied else 'columns_unavailable']})
            bb.warnings.append(f'{course["name"]} 的作业列表未读取成功，已保留同学期缓存' if cached else f'{course["name"]} 的作业列表未读取成功')
            continue
        try:
            own_grades = user_grades(bb, cid)
            exemption_checked = True
        except Exception:
            own_grades, exemption_checked = {}, False
            errors.append('exemptions_unavailable')
        if getattr(bb, '_assignment_restrictions', {}).get(cid, 0):
            errors.append('restricted_content')
        for col, content in columns:
            try:
                attempts = bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/columns/{col["id"]}/attempts')
                if not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts):
                    raise ValueError('Invalid attempt envelope')
            except Exception:
                attempts = None
                errors.append('attempts_unavailable')
            evidence = submission(attempts, cid, col['id'])
            if evidence['status'] == 'unknown':
                errors.append('submission_unconfirmed')
            if exempt(own_grades.get(col['id'], {})):
                evidence['status'] = 'exempt'
            output.append({'course': course['name'], 'course_id': cid, 'name': col.get('name') or '未命名作业',
                           'content_id': col['contentId'], 'column_id': col['id'],
                           'kind': (content.get('contentHandler') or {}).get('id'),
                           'attempts_allowed': (col.get('grading') or {}).get('attemptsAllowed'),
                           'attempts': len(attempts) if attempts is not None else None,
                           'exemption_checked': exemption_checked, 'fetched_at': checked_at, 'stale': False,
                           **deadline(col, cid, events), **evidence})
        coverage.append({'course_id': cid, 'course': course['name'], 'state': 'partial' if errors else 'complete',
                         'checked_at': checked_at, 'received': len(output)-start, 'retained': 0,
                         'errors': sorted(set(errors)), 'calendar_checked': calendar_ok})
    bb.assignment_coverage = coverage
    return sorted(output, key=lambda a: (not (a.get('due') or a.get('due_date_only')),
                                         a.get('due') or a.get('due_date_only') or '', a['course']))
