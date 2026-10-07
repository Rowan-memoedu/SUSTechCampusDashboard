from datetime import date
import hashlib
import os

from sustech_dashboard.core import attachment_key, download_name
from sustech_dashboard.materials_local import LocalMaterials
from sustech_dashboard.materials_store import MaterialsStore
from sustech_dashboard.provider import Blackboard, term_matches


def test_readable_names_collisions_and_revisions_preserve_every_file(tmp_path):
    class School:
        content = b'first'
        def download_attachment(self, course, content, attachment, path):
            path.write_bytes(self.content)
    school = School()
    registry = LocalMaterials(tmp_path/'files', tmp_path/'receipts.json')
    item = dict(course_id='c', course_name='Math', content_id='i', id='a',
                file_name='%E8%AE%B2%E4%B9%89.pdf', source_version='1')
    first = registry.save(school, item)
    school.content = b'second'
    second = registry.save(school, dict(item, id='b'))
    third = registry.save(school, dict(item, source_version='2'))
    assert len({r['path'] for r in (first, second, third)}) == 3
    for receipt in (first, second, third):
        path = registry.root/receipt['path']
        assert path.name == download_name(item['file_name']) == '讲义.pdf'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt['sha256']
    assert download_name('讲义.pdf'.encode().decode('latin-1')) == '讲义.pdf'
    path = registry.root/third['path']
    path.write_bytes(b'edited')
    os.utime(path, ns=(third['mtime_ns'], third['mtime_ns']))
    assert registry.inventory()[0]['status'] == 'modified'
    path.unlink()
    assert registry.inventory()[0]['status'] == 'missing'


def test_old_jobs_and_updated_receipts_do_not_claim_current_files_saved(tmp_path):
    store = MaterialsStore(tmp_path/'jobs.sqlite3')
    item = dict(course_id='c', course_name='Math', content_id='i', id='a',
                file_name='讲义.pdf', source_version='2')
    key = attachment_key('c', 'i', 'a')
    manifest = {'items': [item]}
    store.enqueue(['old/course/file'], 'Previous semester')
    store.agent_poll({'agent_id': 'a'*32, 'claim': False, 'inventory': [
        {'key': key, 'status': 'saved', 'source_version': '1', 'path': '讲义.pdf'}]}, manifest)
    result = store.view(manifest)
    assert result['auto_enabled'] is False
    assert result['items'][0]['local_status'] == 'updated'
    assert result['jobs'] == []


def test_term_identity_wins_over_overlapping_availability():
    semester = {'XN': '2026-2027', 'XQ': '1'}
    term = {'name': '2025 Fall', 'availability': {'duration': {
        'start': '2025-01-01T00:00:00Z', 'end': '2028-01-01T00:00:00Z'}}}
    assert not term_matches(term, semester, date(2026, 10, 7))
    assert term_matches({'name': '2027 Spring'}, dict(semester, XQ='2'), date(2027, 3, 7))
    assert term_matches({'name': '2027 Summer'}, dict(semester, XQ='3'), date(2027, 7, 7))


def test_calendar_controls_cutoff_and_attachments_exclude_other_terms(monkeypatch):
    from sustech_dashboard import academic_calendar, execution
    from types import SimpleNamespace
    term = SimpleNamespace(xn='2026-2027', xq='1', sign_in=date(2026, 9, 4),
                           teaching_start=date(2026, 9, 7), final_end=date(2027, 1, 8))
    monkeypatch.setattr(academic_calendar, 'load_recent_calendar', lambda year: SimpleNamespace(fall=term, spring=None, summer=None))
    monkeypatch.setattr(academic_calendar, '_scope_cache', None)
    scope = academic_calendar.semester_scope({'XN': '2026-2027', 'XQ': '1'})
    assert execution.course_cutoff() == date(2026, 9, 4)
    bb = Blackboard.__new__(Blackboard)
    bb._scope = scope
    bb._contents = lambda _: iter([dict(id=key, created=created, contentHandler={'id':'resource/x-bb-assignment'})
        for key, created in [('old', '2025-10-07T00:00:00Z'), ('new', '2026-10-07T00:00:00Z'), ('future', '2027-09-07T00:00:00Z')]])
    def rows(path):
        if path.endswith('/gradebook/columns'):
            return [dict(id=k, contentId=k, grading={'type':'Attempts', 'due':d}) for k,d in [
                ('old','2025-10-07T00:00:00Z'), ('new','2026-10-07T00:00:00Z'), ('future','2027-09-07T00:00:00Z')]]
        return [{'id': 'a', 'fileName': '讲义.pdf'}]
    bb.results = rows
    bb.json = lambda _: {}
    assert [i['content_id'] for i in bb.attachments('c')] == ['new']
