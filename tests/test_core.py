import os
from datetime import date, datetime

import pytest

from sustech_dashboard.core import (
    CHINA_TZ, remaining_seconds, safe_name, submission_status, sync_attachments,
)
from sustech_dashboard.provider import Blackboard, term_matches
from sustech_dashboard.dpapi_store import protect_password, unprotect_password


class FakeAttachments:
    def __init__(self):
        self.items = [{"content_id": "_item_1", "id": "_old_1", "file_name": "课件.pdf"}]
        self.downloaded = []
        self.fail_scan = False

    def current_courses(self):
        return [{"id": "_course_1", "name": "数学/一班"}]

    def attachments(self, course_id):
        if self.fail_scan:
            raise RuntimeError("network down")
        return list(self.items)

    def download_attachment(self, course_id, content_id, attachment_id, target):
        self.downloaded.append(attachment_id)
        target.write_bytes(b"document")


def test_first_successful_scan_baselines_without_downloading_and_later_scan_is_incremental(tmp_path):
    fake = FakeAttachments()
    state = tmp_path / "state.json"
    fake.fail_scan = True
    with pytest.raises(RuntimeError):
        sync_attachments(fake, tmp_path / "download", state)
    assert not state.exists()
    fake.fail_scan = False
    first = sync_attachments(fake, tmp_path / "download", state)
    assert first["mode"] == "baseline" and first["downloaded"] == 0
    assert fake.downloaded == []
    fake.items.append({"content_id": "_item_2", "id": "_new_1", "file_name": "../新资料.pdf"})
    second = sync_attachments(fake, tmp_path / "download", state)
    assert second["downloaded"] == 1 and fake.downloaded == ["_new_1"]
    assert (tmp_path / "download" / "数学_一班").is_dir()
    assert len(list((tmp_path / "download" / "数学_一班").iterdir())) == 1
    assert sync_attachments(fake, tmp_path / "download", state)["downloaded"] == 0


def test_corrupt_baseline_or_existing_target_never_triggers_overwrite(tmp_path):
    fake = FakeAttachments()
    state = tmp_path / "state.json"
    state.write_text("broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="基线文件损坏"):
        sync_attachments(fake, tmp_path / "download", state)
    state.unlink()
    sync_attachments(fake, tmp_path / "download", state)
    fake.items.append({"content_id": "_item_2", "id": "_new_1", "file_name": "资料.pdf"})
    from sustech_dashboard.core import unique_name

    target_dir = tmp_path / "download" / "数学_一班"
    target_dir.mkdir(parents=True)
    target = target_dir / unique_name("资料.pdf", "_course_1/_item_2/_new_1")
    target.write_bytes(b"user file")
    result = sync_attachments(fake, tmp_path / "download", state)
    assert result["downloaded"] == 0 and result["failed"]
    assert target.read_bytes() == b"user file"


def test_attempt_status_never_conflates_draft_or_request_failure_with_submission():
    assert submission_status(None) == "unknown"
    assert submission_status([]) == "not_submitted"
    assert submission_status([{"status": "InProgress"}]) == "draft"
    assert submission_status([{"status": "NeedsGrading"}]) == "submitted"
    assert submission_status([{"status": "UnexpectedStatus"}]) == "unknown"


def test_due_time_uses_china_time_and_unknown_deadline_stays_unknown():
    now = datetime(2026, 9, 29, 9, 0, tzinfo=CHINA_TZ)
    assert remaining_seconds("2026-09-29T02:00:00Z", now) == 3600
    assert remaining_seconds(None, now) is None


def test_term_is_checked_against_live_semester():
    semester = {"XN": "2026-2027", "XQ": "1"}
    assert term_matches({"name": "2026 Fall"}, semester, date(2026, 9, 29))
    assert not term_matches({"name": "2026 Spring"}, semester, date(2026, 9, 29))


def test_untrusted_path_component_is_sanitized():
    assert safe_name("../math\\slides") == "_math_slides"


def test_blackboard_adapter_collects_file_document_and_assignment_attachments():
    bb = Blackboard.__new__(Blackboard)
    bb._contents = lambda course_id: iter([
        {"id": "_f_1", "title": "文件", "contentHandler": {"id": "resource/x-bb-file"}},
        {"id": "_d_1", "title": "讲义", "contentHandler": {"id": "resource/x-bb-document"}},
        {"id": "_a_1", "title": "作业", "contentHandler": {"id": "resource/x-bb-assignment"}},
    ])
    bb.results = lambda path: [{"id": path.split("/")[-2], "fileName": "附件.pdf"}]
    files = bb.attachments("_course_1")
    assert len(files) == 3
    assert {x["title"] for x in files} == {"文件", "讲义", "作业"}


def test_blackboard_adapter_preserves_unknown_when_attempt_api_fails():
    bb = Blackboard.__new__(Blackboard)
    bb.warnings = []
    bb.current_courses = lambda: [{"id": "_course_1", "name": "数学"}]

    def results(path):
        if path.endswith("/gradebook/columns"):
            return [{"id": "_column_1", "name": "作业", "contentId": "_item_1",
                     "grading": {"type": "Attempts", "due": "2026-09-30T10:00:00Z"}}]
        raise RuntimeError("failed")

    bb.results = results
    assert bb.assignments()[0]["status"] == "unknown"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_windows_dpapi_credential_roundtrip_without_plaintext_in_cipher():
    password = "测试密码-123"
    encrypted = protect_password(password)
    assert password not in encrypted
    assert unprotect_password(encrypted) == password
