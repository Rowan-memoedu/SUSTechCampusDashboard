"""Pure normalization and incremental attachment state."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from .paths import DATA_ROOT, download_root

DOWNLOAD_ROOT = download_root()
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


def download_name(value: str) -> str:
    """The same readable, Windows-safe name for the UI and the actual file."""
    from urllib.parse import unquote
    value = str(value or '附件')
    if re.search(r'%[0-9a-fA-F]{2}', value):
        try:
            value = unquote(value, encoding='utf-8', errors='strict')
        except UnicodeError:
            pass
    # Recover UTF-8 names accidentally interpreted as Latin-1 by an upstream.
    try:
        decoded = value.encode('latin-1').decode('utf-8')
        if decoded != value:
            value = decoded
    except UnicodeError:
        pass
    name = Path(safe_name(value)).name
    suffix = Path(name).suffix[:16]
    stem = Path(name).stem[:100].rstrip(' .') or '附件'
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', stem):
        stem = '_' + stem
    return stem + suffix


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
    from .quotas import check_private_quota
    check_private_quota(len(json.dumps(value, ensure_ascii=False).encode()))
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
        try:
            from .materials_local import LocalMaterials
            LocalMaterials(root, state_path.with_name('downloaded-files.json')).save(provider, a)
            seen.add(key)
            downloaded += 1
        except Exception as exc:
            failed.append(f"{a['course_name']} / {a['file_name']}: {type(exc).__name__}")
    state["seen"] = sorted(seen)
    save_json(state_path, state)
    return {"mode": "incremental", "seen": len(found), "downloaded": downloaded, "failed": failed}
