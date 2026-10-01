"""Read-only adapters for SUSTech systems and Blackboard REST."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from requests.exceptions import HTTPError

from .core import CHINA_TZ, parse_dt, submission_status


BB_BASE = "https://bb.sustech.edu.cn"
COURSE_CUTOFF = date(2026, 9, 1)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _api_path(path: str) -> str:
    """Follow Blackboard paging only on its own HTTPS host."""
    url = urljoin(BB_BASE, path)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "bb.sustech.edu.cn":
        raise ValueError("Blackboard 分页地址不属于学校域名")
    return url


def term_matches(term: dict[str, Any], semester: dict[str, Any], today: date) -> bool:
    duration = (term.get("availability") or {}).get("duration") or {}
    start = parse_dt(duration.get("start"))
    end = parse_dt(duration.get("end"))
    if start and end and start.date() <= today <= end.date():
        return True
    name = str(term.get("name", "")).lower()
    academic = str(semester.get("XN", ""))
    season = str(semester.get("XQ", ""))
    if not academic or not season:
        return False
    years = re.findall(r"20\d{2}", academic)
    first_year = years[0] if years else ""
    if first_year not in name and academic not in name.lower():
        return False
    return bool(re.search(r"秋|fall|autumn|第一|1st|semester\s*1", name)) if season == "1" else bool(
        re.search(r"春|spring|第二|2nd|semester\s*2", name)
    )


class Blackboard:
    def __init__(self) -> None:
        from sustech_survival.sso import BBAuth

        auth = BBAuth()
        ok, reason = auth.ensure()
        if not ok:
            raise RuntimeError(f"Blackboard 登录失败：{reason}")
        self.session = auth.session
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        self.session.mount(BB_BASE, HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=1, allowed_methods={"GET"},
            status_forcelist={429, 502, 503, 504},
        )))
        self.warnings: list[str] = []
        self.unclassified: list[str] = []
        self._courses: list[dict[str, str]] | None = None

    def get(self, path: str, *, stream: bool = False):
        response = self.session.get(_api_path(path), timeout=(10, 60), stream=stream)
        response.raise_for_status()
        return response

    def json(self, path: str) -> dict[str, Any]:
        return self.get(path).json()

    def results(self, path: str) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        next_path: str | None = path
        seen_pages: set[str] = set()
        while next_path:
            if next_path in seen_pages or len(seen_pages) >= 100:
                raise RuntimeError("Blackboard 分页循环或超过 100 页")
            seen_pages.add(next_path)
            body = self.json(next_path)
            all_rows.extend(body.get("results", []))
            next_ref = body.get("paging", {}).get("nextPage")
            next_path = urljoin(_api_path(next_path), next_ref) if next_ref else None
        return all_rows

    def current_courses(self) -> list[dict[str, str]]:
        if self._courses is not None:
            return self._courses
        from sustech_survival.tis.schedule import current_semester

        semester = current_semester()
        me = self.json("/learn/api/public/v1/users/me")
        enrollments = self.results(f"/learn/api/public/v1/users/{me['id']}/courses")
        selected: list[dict[str, str]] = []
        term_cache: dict[str, dict[str, Any]] = {}
        unknown: list[str] = []
        for enrollment in enrollments:
            cid = enrollment.get("courseId")
            if not cid:
                continue
            course = self.json(f"/learn/api/public/v1/courses/{cid}")
            name = course.get("name") or cid
            enrolled_at = parse_dt(enrollment.get("created"))
            if enrolled_at is None:
                unknown.append(name)
                continue
            if enrolled_at.date() < COURSE_CUTOFF:
                continue
            tid = course.get("termId")
            if tid:
                if tid not in term_cache:
                    try:
                        term_cache[tid] = self.json(f"/learn/api/public/v1/terms/{tid}")
                    except Exception:
                        term_cache[tid] = {"name": name}
                if term_matches(term_cache[tid], semester, datetime.now(CHINA_TZ).date()):
                    selected.append({"id": cid, "name": name})
                elif term_cache[tid] == {"name": name}:
                    unknown.append(name)
            else:
                if term_matches({"name": name}, semester, datetime.now(CHINA_TZ).date()):
                    selected.append({"id": cid, "name": name})
                else:
                    unknown.append(name)
        if unknown:
            self.unclassified = unknown
            self.warnings.append(f"{len(unknown)} 门 Blackboard 课程缺少学期标识，未计入当前学期")
        if not selected:
            raise RuntimeError("Blackboard 课程学期无法与 TIS 当前学期匹配；未建立下载基线")
        self._courses = selected
        return selected

    def _contents(self, course_id: str, parent_id: str | None = None):
        path = f"/learn/api/public/v1/courses/{course_id}/contents"
        if parent_id:
            path += f"/{parent_id}/children"
        for item in self.results(path):
            yield item
            if item.get("hasChildren"):
                try:
                    yield from self._contents(course_id, item["id"])
                except HTTPError as exc:
                    if exc.response is None or exc.response.status_code != 403:
                        raise
                    self._inaccessible_folders = getattr(self, "_inaccessible_folders", 0) + 1

    def attachments(self, course_id: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        before = getattr(self, "_inaccessible_folders", 0)
        inaccessible_attachments = 0
        supported = {"resource/x-bb-file", "resource/x-bb-document", "resource/x-bb-assignment"}
        for item in self._contents(course_id):
            if item.get("contentHandler", {}).get("id") not in supported:
                continue
            iid = item["id"]
            try:
                attachments = self.results(
                    f"/learn/api/public/v1/courses/{course_id}/contents/{iid}/attachments"
                )
            except HTTPError as exc:
                if exc.response is None or exc.response.status_code != 403:
                    raise
                inaccessible_attachments += 1
                continue
            for attachment in attachments:
                out.append({
                    "content_id": iid,
                    "id": attachment["id"],
                    "file_name": attachment.get("fileName") or "附件",
                    "title": item.get("title", ""),
                })
        skipped = getattr(self, "_inaccessible_folders", 0) - before + inaccessible_attachments
        if skipped:
            course_name = next(
                (course["name"] for course in getattr(self, "_courses", []) or [] if course["id"] == course_id),
                course_id,
            )
            self.warnings.append(f"{course_name} 有 {skipped} 处内容暂无访问权限，已按当前可见内容扫描")
        return out

    def download_attachment(self, course_id: str, content_id: str, attachment_id: str, target: Path) -> None:
        path = (f"/learn/api/public/v1/courses/{course_id}/contents/{content_id}"
                f"/attachments/{attachment_id}/download")
        tmp = target.with_name(target.name + ".part")
        try:
            with self.get(path, stream=True) as response, tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

    def assignments(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for course in self.current_courses():
            cid = course["id"]
            try:
                columns = self.results(f"/learn/api/public/v1/courses/{cid}/gradebook/columns")
            except Exception as exc:
                raise RuntimeError(f"作业列表读取失败 ({type(exc).__name__})") from None
            for col in columns:
                if col.get("grading", {}).get("type") != "Attempts" or not col.get("contentId"):
                    continue
                due = col.get("grading", {}).get("due")
                due_at = parse_dt(due)
                if due and due_at is None:
                    self.warnings.append(f"{course['name']}：有作业截止时间无法解析，已跳过")
                    continue
                if due_at and due_at.date() < COURSE_CUTOFF:
                    continue
                try:
                    content = self.json(f"/learn/api/public/v1/courses/{cid}/contents/{col['contentId']}")
                except HTTPError as exc:
                    if exc.response is None or exc.response.status_code not in {403, 404}:
                        raise
                    continue
                if not due_at:
                    created_at = parse_dt(content.get("created"))
                    if created_at is None or created_at.date() < COURSE_CUTOFF:
                        continue
                try:
                    attempts = self.results(
                        f"/learn/api/public/v1/courses/{cid}/gradebook/columns/{col['id']}/attempts"
                    )
                except Exception:
                    attempts = None
                output.append({
                    "course": course["name"], "course_id": cid,
                    "name": col.get("name") or "未命名作业",
                    "content_id": col["contentId"],
                    "due": due or None,
                    "status": submission_status(attempts),
                    "attempts": len(attempts) if attempts is not None else None,
                })
        output.sort(key=lambda a: (a["due"] is None, a["due"] or "", a["course"]))
        return output


def read_tis() -> dict[str, Any]:
    from sustech_survival.sso import TISAuth
    from sustech_survival.tis import schedule
    from sustech_survival.tis.exams import fetch_exams

    auth = TISAuth()
    ok, reason = auth.ensure()
    if not ok:
        raise RuntimeError(f"TIS 登录失败：{reason}")
    semester = schedule.current_semester()
    week = schedule.current_week()
    today = datetime.now(CHINA_TZ)
    classes = schedule.week_schedule(week)
    errors: list[str] = []
    try:
        exams = fetch_exams(auth)
    except Exception as exc:
        exams = []
        errors.append(f"考试安排读取失败：{type(exc).__name__}")
    try:
        evals = _pending_evals(auth, semester)
    except Exception as exc:
        evals = []
        errors.append(f"待评教读取失败：{type(exc).__name__}")
    return {
        "semester": semester.get("XNXQ") or semester.get("XN", ""),
        "week": week,
        "today_classes": [c for c in classes if str(c.get("KEY", "")).startswith(f"xq{today.isoweekday()}_")],
        "exams": [e for e in exams if str(e.get("KSRQ", "")) >= today.date().isoformat()],
        "pending_evals": evals,
        "errors": errors,
    }


def _pending_evals(auth: Any, semester: dict[str, Any]) -> list[dict[str, str]]:
    response = auth.get("/personnelEvaluation/listObtainPersonnelEvaluationTasks", params={
        "yhdm": auth.username, "rwmc": "", "sfyp": "0", "pageNum": "1", "pageSize": "100",
    }, timeout=15)
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) != "200":
        raise RuntimeError("TIS 待评教查询失败")
    out: list[dict[str, str]] = []
    for task in data.get("result", {}).get("list", []):
        response = auth.get("/personnelEvaluation/listEcaluationRalationshipEnriry", params={
            "pjrdm": auth.username, "wjid": task["firstwjid"], "bpmc": "", "sfyp": "0",
            "xnxq": semester.get("XNXQ", ""), "pageNum": "1", "pageSize": "100", "zc": "",
            "xqj": "", "jc": "", "skdd": "", "kkyxdm": "", "bpssyxdm": "",
            "kcmc": "", "sfcxqbwj": "0", "rwid": task["rwid"], "lsjgzt": "",
        }, timeout=15)
        response.raise_for_status()
        body = response.json()
        if str(body.get("code")) != "200":
            raise RuntimeError("TIS 待评教明细查询失败")
        for item in body.get("result", {}).get("list", []):
            status = str(item.get("lsjgzt", ""))
            if status not in {"2", "5"}:
                out.append({"course": item.get("kcmc") or item.get("kcmc_en") or "未命名课程",
                            "status": {"0": "待评价", "3": "已保存未提交", "4": "未结课"}.get(status, f"未知状态 {status}")})
    return out


def read_bookings() -> dict[str, Any]:
    from sustech_survival.booking import booking
    from sustech_survival.lib.booking import lib_booking

    today = datetime.now(CHINA_TZ).date()
    result: dict[str, Any] = {
        "library_idle": [], "library_mine": [], "ehall_available": [], "ehall_mine": [], "errors": [],
    }
    try:
        lib = lib_booking()
        result["library_idle"] = lib.home_summary()
        result["library_mine"] = lib.my_reservations(today, today + timedelta(days=30), page_size=100)
    except Exception as exc:
        result["errors"].append(f"图书馆预约读取失败：{type(exc).__name__}")
    try:
        ehall = booking()
        result["ehall_available"] = [room for room in ehall.rooms(page_size=100) if room.is_available]
        result["ehall_mine"] = ehall.my_meetings(page_size=100)
    except Exception as exc:
        result["errors"].append(f"E-Hall 场地读取失败：{type(exc).__name__}")
    return _jsonable(result)
