from types import SimpleNamespace
import pytest
from sustech_dashboard.provider import Blackboard


def test_attachment_scan_retains_long_budget_without_changing_interactive_reads():
    bb = Blackboard.__new__(Blackboard)
    budgets = []
    response = SimpleNamespace(status_code=200, url='https://bb.sustech.edu.cn/', raise_for_status=lambda: None)
    bb.session = SimpleNamespace(get=lambda *a, **k: (budgets.append(k['timeout']) or response))
    def scan(course):
        bb.get('/learn/api/public/v1/courses/fixture/contents')
        return []
    bb._attachments = scan
    bb.get('/learn/api/public/v1/users/me')
    assert bb.attachments('fixture') == []
    bb.get('/learn/api/public/v1/users/me')
    assert budgets == [(10,20), (10,60), (10,20)]


def test_failed_scan_restores_interactive_budget():
    bb = Blackboard.__new__(Blackboard)
    def scan(course):
        assert bb._read_timeout == 60
        raise ValueError('fixture interrupted scan')
    bb._attachments = scan
    with pytest.raises(ValueError):
        bb.attachments('fixture')
    assert bb._read_timeout == 20
