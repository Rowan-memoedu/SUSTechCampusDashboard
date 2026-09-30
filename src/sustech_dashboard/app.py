"""Local synchronization and loopback dashboard."""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import date, datetime
from typing import Any

from flask import Flask, jsonify, render_template, request

from .core import CHINA_TZ, DATA_ROOT, DOWNLOAD_ROOT, load_json, save_json, sync_attachments
from .provider import Blackboard, read_bookings, read_tis
from .room_monitor import schedule, validate_target
from . import monitor_remote


SNAPSHOT_PATH = DATA_ROOT / "snapshot.json"
BASELINE_PATH = DATA_ROOT / "attachments.json"
SYNC_INTERVAL_SECONDS = 30 * 60
_sync_lock = threading.Lock()


def sync_all() -> dict[str, Any]:
    if not _sync_lock.acquire(blocking=False):
        return load_json(SNAPSHOT_PATH, {"errors": {"sync": "已有同步正在运行"}})
    try:
        snapshot: dict[str, Any] = {
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
            "blackboard_courses": [], "assignments": [], "tis": {}, "bookings": {},
            "weather": {}, "downloads": {}, "errors": {}, "warnings": [],
        }
        try:
            bb = Blackboard()
            snapshot["blackboard_courses"] = bb.current_courses()
            snapshot["assignments"] = bb.assignments()
            snapshot["downloads"] = sync_attachments(bb, DOWNLOAD_ROOT, BASELINE_PATH)
            snapshot["warnings"].extend(bb.warnings)
        except Exception as exc:
            snapshot["errors"]["blackboard"] = f"{type(exc).__name__}: {exc}"
        try:
            snapshot["tis"] = read_tis()
            snapshot["warnings"].extend(snapshot["tis"].get("errors", []))
        except Exception as exc:
            snapshot["errors"]["tis"] = f"{type(exc).__name__}: {exc}"
        try:
            snapshot["bookings"] = read_bookings()
            snapshot["warnings"].extend(snapshot["bookings"].get("errors", []))
        except Exception as exc:
            snapshot["errors"]["bookings"] = f"{type(exc).__name__}: {exc}"
        try:
            from sustech_survival import Context

            context = Context(level="terse")
            snapshot["weather"] = {
                "condition": context.weather_cond,
                "temperature": context.temperature,
                "aqi": context.aqi_value,
            }
        except Exception as exc:
            snapshot["warnings"].append(f"天气不可用：{type(exc).__name__}")
        save_json(SNAPSHOT_PATH, snapshot)
        return snapshot
    finally:
        _sync_lock.release()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.ensure_ascii = False
    csrf = secrets.token_urlsafe(32)

    def local_request(write: bool = False) -> None:
        if request.host not in {"127.0.0.1", "localhost"} and not (
            request.host.startswith("127.0.0.1:") or request.host.startswith("localhost:")
        ):
            raise ValueError("仅允许本机访问")
        if write:
            origin = request.headers.get("Origin", "")
            if origin not in {f"http://{request.host}", f"https://{request.host}"}:
                raise ValueError("请求来源不匹配")
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), csrf):
                raise ValueError("页面令牌无效，请刷新页面")

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=csrf)

    @app.get("/api/status")
    def status():
        return jsonify(load_json(SNAPSHOT_PATH, {"updated_at": None, "errors": {"sync": "尚未完成首次同步"}}))

    @app.get("/api/discussion-rooms")
    def discussion_rooms():
        try:
            local_request()
            from sustech_survival.lib.booking.client import lib_booking
            day = date.fromisoformat(request.args["date"])
            return jsonify({"date": day.isoformat(), "rooms": schedule(lib_booking(), day)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/room-watch")
    def room_watch():
        try:
            local_request()
            return jsonify(monitor_remote.call("status"))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503

    @app.post("/api/room-watch")
    def create_room_watch():
        try:
            local_request(write=True)
            payload = request.get_json()
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            from sustech_survival.lib.booking.client import lib_booking
            day = date.fromisoformat(payload["begin"][:10])
            rooms = schedule(lib_booking(), day)
            room = next((r for r in rooms if r["id"] == int(payload["room_id"])), None)
            if room is None:
                raise ValueError("目标讨论间不可用")
            target = validate_target(payload, room)
            return jsonify(monitor_remote.call("create", target))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/room-watch/stop")
    def stop_room_watch():
        try:
            local_request(write=True)
            return jsonify(monitor_remote.call("stop"))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    return app


def _sync_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            sync_all()
        except Exception:
            logging.exception("Campus sync failed")
        stop.wait(SYNC_INTERVAL_SECONDS)


def serve(port: int = 8765) -> None:
    stop = threading.Event()
    worker = threading.Thread(target=_sync_loop, args=(stop,), daemon=True, name="campus-sync")
    worker.start()
    try:
        print(f"本地页面：http://127.0.0.1:{port}")
        print("启动后立即同步，运行中每 30 分钟同步；按 Ctrl+C 停止。")
        create_app().run(host="127.0.0.1", port=port, use_reloader=False, threaded=True)
    finally:
        stop.set()
