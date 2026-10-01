"""Pure normalization and incremental attachment state."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_ROOT = Path(os.environ.get("SUSTECH_DASHBOARD_DATA_ROOT", r"D:\AppData\SUSTechCampusDashboard"))
DOWNLOAD_ROOT = Path(os.environ.get("SUSTECH_DOWNLOAD_ROOT", r"D:\download"))
DASHBOARD_PORT = 18765  # 8765 belongs to AnkiConnect.
CHINA_TZ = timezone(__import__("datetime").timedelta(hours=8))


def safe_name(value: str, fallback: str = "未命名") -> str:
    """One Windows path component; never permit separators or traversal."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip(" .")[:120]
    return value if value and value not in {".", ".."} else fallback


def unique_name(name: str, identity: str) -> str:
    stem = safe_name(Path(name).stem)
    suffix = Path(name).suffix[:16]
    suffix = re.sub(r'[^\w.]', '_', suffix)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return f"{stem}-{digest}{suffix}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        return dt.astimezone(CHINA_TZ)
    except ValueError:
        return None


def remaining_seconds(due: str | None, now: datetime | None = None) -> int | None:
    dt = parse_dt(due)
    if dt is None:
        return None
    now = now or datetime.now(CHINA_TZ)
    return int((dt - now).total_seconds())


SUBMITTED_STATES = {"NeedsGrading", "Completed", "NeedsGradingAgain", "InProgressAgain"}
DRAFT_STATES = {"InProgress"}


def submission_status(attempts: list[dict[str, Any]] | None) -> str:
    """None means request failure; an empty successful response means no attempt."""
    if attempts is None:
        return "unknown"
    statuses = {str(a.get("status", "")) for a in attempts}
    if statuses & SUBMITTED_STATES:
        return "submitted"
    if statuses & DRAFT_STATES:
        return "draft"
    if not attempts or statuses == {"NotAttempted"}:
        return "not_submitted"
    return "unknown"


def attachment_key(course_id: str, content_id: str, attachment_id: str) -> str:
    return f"{course_id}/{content_id}/{attachment_id}"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sync_attachments(provider: Any, root: Path, state_path: Path) -> dict[str, Any]:
    """Baseline on first complete scan; subsequent scans download unseen IDs.

    State is not advanced for failed downloads. A failed first scan creates no
    baseline, preventing old files from being mistaken for newly published ones.
    """
    courses = provider.current_courses()
    if not courses:
        raise RuntimeError("无法确认当前学期 Blackboard 课程，未建立下载基线")
    if getattr(provider, "unclassified", []):
        raise RuntimeError("有 Blackboard 课程无法确认所属学期，未建立下载基线")
    all_items: list[dict[str, str]] = []
    for course in courses:
        for item in provider.attachments(course["id"]):
            all_items.append({**item, "course_id": course["id"], "course_name": course["name"]})
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("附件基线文件损坏，已停止同步以免误下载旧资料") from exc
    else:
        state = {"baseline_at": None, "seen": []}
    seen = set(state.get("seen", []))
    found = {
        attachment_key(a["course_id"], a["content_id"], a["id"])
        for a in all_items
    }
    if not state.get("baseline_at"):
        state = {
            "baseline_at": datetime.now(CHINA_TZ).isoformat(),
            "seen": sorted(found),
        }
        save_json(state_path, state)
        return {"mode": "baseline", "seen": len(found), "downloaded": 0, "failed": []}

    downloaded = 0
    failed: list[str] = []
    for a in all_items:
        key = attachment_key(a["course_id"], a["content_id"], a["id"])
        if key in seen:
            continue
        course_dir = root / safe_name(a["course_name"])
        course_dir.mkdir(parents=True, exist_ok=True)
        target = course_dir / unique_name(a["file_name"], key)
        try:
            if target.exists():
                raise FileExistsError("目标文件已存在，避免覆盖")
            provider.download_attachment(a["course_id"], a["content_id"], a["id"], target)
            seen.add(key)
            downloaded += 1
        except Exception as exc:
            failed.append(f"{a['course_name']} / {a['file_name']}: {type(exc).__name__}")
    state["seen"] = sorted(seen)
    save_json(state_path, state)
    return {"mode": "incremental", "seen": len(found), "downloaded": downloaded, "failed": failed}
