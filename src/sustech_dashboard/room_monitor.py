"""Library room schedule and one-shot, persistent booking watch."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .core import CHINA_TZ

KINDS = (1, 9)  # group and 1–3 person discussion rooms
TERMINAL = {"booked", "expired", "cancelled", "needs_review"}


@contextmanager
def state_lock(path: Path):
    """Serialize worker and CLI state changes on the Linux host."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def now() -> datetime:
    return datetime.now(CHINA_TZ)


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CHINA_TZ).isoformat()


def schedule(client: Any, day: date) -> list[dict[str, Any]]:
    """Return only room metadata and busy times; omit other patrons' data."""
    if not now().date() <= day <= now().date() + timedelta(days=2):
        raise ValueError("只能查询今天至后天的讨论间")
    rooms = []
    for kind in KINDS:
        raw = client._call("GET", "/reserve", params={
            "sysKind": 1, "labId": "", "resvDates": f"{day:%Y%m%d},{day:%Y%m%d}",
            "page": 1, "kindIds": kind, "pageSize": 100,
        }) or []
        for item in raw:
            busy = [
                {"start": _stamp(r["startTime"]), "end": _stamp(r["endTime"])}
                for r in item.get("resvInfo") or []
                if datetime.fromtimestamp(r["startTime"] / 1000, CHINA_TZ).date() == day
            ]
            rooms.append({
                "id": int(item["devId"]), "name": str(item.get("devName") or "").strip(),
                "location": str(item.get("labName") or ""),
                "min_people": int(item.get("minUser") or 1),
                "max_people": int(item.get("maxUser") or 3),
                "open": item.get("openTimes") or [], "busy": busy,
            })
    return sorted(rooms, key=lambda x: (x["location"], x["name"]))


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("时间需要包含时区")
    return parsed.astimezone(CHINA_TZ)


def is_free(room: dict, begin: datetime, end: datetime) -> bool:
    if not room["open"]:
        return False
    open_ok = any(
        t["openStartTime"] <= begin.strftime("%H:%M")
        and end.strftime("%H:%M") <= t["openEndTime"]
        for t in room["open"]
    )
    return open_ok and all(
        not (begin < _dt(slot["end"]) and end > _dt(slot["start"]))
        for slot in room["busy"]
    )


def validate_target(payload: dict, room: dict) -> dict:
    begin, end = _dt(payload["begin"]), _dt(payload["end"])
    current = now()
    if begin.date() != end.date() or not current < begin < end:
        raise ValueError("请选择未来同一天的开始与结束时间")
    if begin.date() > current.date() + timedelta(days=2) or end - begin > timedelta(hours=2):
        raise ValueError("最多提前两天预约，每次最长两小时")
    if begin.minute % 15 or end.minute % 15 or begin.second or end.second:
        raise ValueError("时间须按 15 分钟刻度选择")
    title = str(payload.get("title") or "").strip()
    if not title or len(title) > 80:
        raise ValueError("请填写 1–80 字的预约主题")
    members = payload.get("members") or []
    if not isinstance(members, list) or len(members) > 9 or any(not str(m).isdigit() for m in members):
        raise ValueError("共同申请人须填写图书馆系统 accNo 数字")
    members = [int(m) for m in members]
    if len(set(members)) != len(members):
        raise ValueError("共同申请人不能重复")
    if room["min_people"] >= 3 and len(members) < 2:
        raise ValueError("该讨论间至少需要两位共同申请人")
    if len(members) + 1 > room["max_people"]:
        raise ValueError("人数超过讨论间上限")
    if not any(t["openStartTime"] <= begin.strftime("%H:%M") and end.strftime("%H:%M") <= t["openEndTime"] for t in room["open"]):
        raise ValueError("时段不在讨论间开放时间内")
    return {"room_id": room["id"], "room_name": room["name"], "begin": begin.isoformat(),
            "end": end.isoformat(), "title": title, "members": members}


def _atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"status": "idle"}
    return json.loads(path.read_text(encoding="utf-8"))


def public_state(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in {"members", "booking_raw"}}


def create_watch(path: Path, payload: dict) -> dict:
    existing = read_state(path)
    if existing.get("status") in {"watching", "attempting", "needs_review"}:
        raise ValueError("已有监控或待核查任务；请先处理它")
    if existing.get("status") == "booked" and all(existing.get(k) == payload.get(k) for k in ("room_id", "begin", "end")):
        raise ValueError("该房间和时段已经预约成功，不能重复监控")
    state = {**payload, "id": uuid.uuid4().hex, "status": "watching",
             "created_at": now().isoformat(), "checks": 0}
    _atomic(path, state)
    return public_state(state)


def stop_watch(path: Path) -> dict:
    state = read_state(path)
    if state.get("status") in {"watching", "needs_review"}:
        state["status"] = "cancelled"
        state["stopped_at"] = now().isoformat()
        _atomic(path, state)
    return public_state(state)


def check_once(path: Path, client: Any) -> dict:
    state = read_state(path)
    if state.get("status") != "watching":
        return state
    begin, end = _dt(state["begin"]), _dt(state["end"])
    if now() >= begin:
        state.update(status="expired", stopped_at=now().isoformat())
        _atomic(path, state)
        return state
    rooms = schedule(client, begin.date())
    room = next((r for r in rooms if r["id"] == state["room_id"]), None)
    if room is None:
        state.update(status="needs_review", error="目标讨论间已不在预约列表中")
        _atomic(path, state)
        return state
    state.update(checks=state["checks"] + 1, last_checked=now().isoformat(),
                 free=is_free(room, begin, end))
    if not state["free"]:
        _atomic(path, state)
        return state
    # Persist the attempt before a network write. An interrupted attempt is never replayed.
    state.update(status="attempting", attempted_at=now().isoformat())
    _atomic(path, state)
    try:
        me = client.whoami()
        members = [me.acc_no, *state["members"]] if state["members"] else None
        result = client.add_reservation(
            dev_id=state["room_id"], begin=begin, end=end, title=state["title"],
            member_kind=2 if state["members"] else 1, resv_member=members,
            dry_run=False, enforce_policy=True,
        )
        state.update(status="booked", booked_at=now().isoformat(),
                     reservation_id=result.get("resvId"))
    except Exception as exc:
        # POST may have succeeded despite a timeout. Require review; never retry blindly.
        state.update(status="needs_review", error=f"预约结果需核查：{type(exc).__name__}")
    _atomic(path, state)
    return state


def worker(path: Path, client: Any, interval: int = 30) -> None:
    with state_lock(path):
        state = read_state(path)
        if state.get("status") == "attempting":
            state.update(status="needs_review", error="预约提交时服务中断，请到图书馆系统核查")
            _atomic(path, state)
    while True:
        try:
            with state_lock(path):
                try:
                    check_once(path, client)
                except Exception as exc:
                    state = read_state(path)
                    if state.get("status") == "watching":
                        state.update(last_checked=now().isoformat(),
                                     last_error=f"查询失败：{type(exc).__name__}")
                        _atomic(path, state)
        except OSError:
            pass
        time.sleep(interval)
