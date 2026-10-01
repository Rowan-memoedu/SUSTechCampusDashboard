"""Local synchronization and loopback dashboard."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import date, datetime
from typing import Any

from flask import Flask, jsonify, render_template, request, Response, stream_with_context, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from .core import CHINA_TZ, DASHBOARD_PORT, DATA_ROOT, DOWNLOAD_ROOT, attachment_key, load_json, save_json, sync_attachments
from .provider import Blackboard, read_bookings, read_tis
from .room_monitor import schedule, validate_target
from . import monitor_remote


SNAPSHOT_PATH = DATA_ROOT / "snapshot.json"
BASELINE_PATH = DATA_ROOT / "attachments.json"
SYNC_INTERVAL_SECONDS = 30 * 60
_sync_lock = threading.Lock()
_bb_lock = threading.Lock()
CLOUD = os.environ.get("SUSTECH_CLOUD") == "1"
MANIFEST_PATH = DATA_ROOT / "manifest.json"


def _public_error(exc: Exception) -> str:
    """Do not persist CAS tickets, URLs or account data from upstream errors."""
    if isinstance(exc, ValueError):
        return str(exc)
    message = str(exc).lower()
    if any(part in message for part in ("ssl", "network", "off-campus", "off campus", "unreachable")):
        return "校园系统连接失败，请检查网络后重试"
    if any(part in message for part in ("credential", "password", "authentication failed")):
        return "校园账号认证失败，请检查凭据"
    return f"{type(exc).__name__}，本轮查询失败"


def sync_all() -> dict[str, Any]:
    if not _sync_lock.acquire(blocking=False):
        return load_json(SNAPSHOT_PATH, {"errors": {"sync": "已有同步正在运行"}})
    try:
        previous = load_json(SNAPSHOT_PATH, {})
        snapshot: dict[str, Any] = {
            "updated_at": datetime.now(CHINA_TZ).isoformat(),
            "blackboard_courses": [], "assignments": [], "tis": {}, "bookings": {},
            "weather": {}, "downloads": {}, "errors": {}, "warnings": [],
            "source_updated_at": dict(previous.get("source_updated_at", {})),
        }
        for key in ("blackboard_courses", "assignments", "tis", "bookings", "downloads"):
            if key in previous:
                snapshot[key] = previous[key]
        try:
            with _bb_lock:
                bb = Blackboard()
                courses = bb.current_courses()
                assignments = bb.assignments()
            snapshot["blackboard_courses"] = courses
            snapshot["assignments"] = assignments
            snapshot["source_updated_at"]["blackboard"] = datetime.now(CHINA_TZ).isoformat()
            snapshot["warnings"].extend(bb.warnings)
        except Exception as exc:
            snapshot["errors"]["blackboard"] = _public_error(exc)
            logging.error("Blackboard sync failed: %s", type(exc).__name__)
            snapshot["warnings"].append("Blackboard 查询失败时保留上次成功数据；请核对各来源的成功同步时间")
        try:
            snapshot["tis"] = read_tis()
            snapshot["source_updated_at"]["tis"] = datetime.now(CHINA_TZ).isoformat()
            snapshot["warnings"].extend(snapshot["tis"].get("errors", []))
        except Exception as exc:
            snapshot["errors"]["tis"] = _public_error(exc)
            logging.error("TIS sync failed: %s", type(exc).__name__)
        try:
            snapshot["bookings"] = read_bookings()
            snapshot["source_updated_at"]["bookings"] = datetime.now(CHINA_TZ).isoformat()
            snapshot["warnings"].extend(snapshot["bookings"].get("errors", []))
        except Exception as exc:
            snapshot["errors"]["bookings"] = _public_error(exc)
            logging.error("Booking sync failed: %s", type(exc).__name__)
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
        # Publish academic data before the more expensive attachment scan.
        save_json(SNAPSHOT_PATH, snapshot)
        try:
            with _bb_lock:
                if CLOUD:
                    bb = Blackboard()
                    courses = bb.current_courses()
                    items = []
                    for course in courses:
                        items.extend(dict(item, course_id=course["id"], course_name=course["name"])
                                     for item in bb.attachments(course["id"]))
                    if bb.unclassified:
                        raise RuntimeError("附件所属课程尚未全部确认")
                    save_json(MANIFEST_PATH, {"updated_at": datetime.now(CHINA_TZ).isoformat(),
                                            "courses": courses, "items": items})
                    snapshot["downloads"] = load_json(DATA_ROOT / "download-status.json", {"mode": "agent"})
                    snapshot["warnings"].extend(bb.warnings)
                else:
                    snapshot["downloads"] = sync_attachments(Blackboard(), DOWNLOAD_ROOT, BASELINE_PATH)
            snapshot["source_updated_at"]["attachments"] = datetime.now(CHINA_TZ).isoformat()
        except Exception as exc:
            snapshot["errors"]["attachments"] = _public_error(exc)
        save_json(SNAPSHOT_PATH, snapshot)
        return snapshot
    finally:
        _sync_lock.release()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.ensure_ascii = False
    if CLOUD:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_prefix=1)
    csrf = secrets.token_urlsafe(32)

    def local_request(write: bool = False) -> None:
        if CLOUD:
            if request.host != os.environ.get("SUSTECH_PUBLIC_HOST") or not request.is_secure:
                raise ValueError("访问地址无效")
        elif request.host not in {"127.0.0.1", "localhost"} and not (
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
        local_request()
        return render_template("index.html", csrf_token=csrf, api_base=request.script_root)

    @app.get("/api/status")
    def status():
        local_request()
        snapshot = load_json(SNAPSHOT_PATH, {"updated_at": None, "errors": {"sync": "尚未完成首次同步"}})
        if CLOUD:
            snapshot["downloads"] = load_json(DATA_ROOT / "download-status.json", {"mode": "agent"})
        return jsonify(snapshot)

    @app.get("/api/attachments")
    def attachment_manifest():
        local_request()
        if not CLOUD:
            abort(404)
        manifest = load_json(MANIFEST_PATH, {})
        if not manifest:
            return jsonify({"error": "附件扫描尚未完成"}), 503
        # Do not present an old scan as a current list when upstream sync failed.
        errors = load_json(SNAPSHOT_PATH, {}).get("errors", {})
        if "attachments" in errors or "blackboard" in errors:
            return jsonify({"error": "本轮附件查询失败，暂缓下载"}), 503
        return jsonify(manifest)

    @app.get("/api/attachment")
    def attachment_download():
        local_request()
        if not CLOUD:
            abort(404)
        key = request.args.get("key", "")
        item = next((i for i in load_json(MANIFEST_PATH, {}).get("items", [])
                     if attachment_key(i["course_id"], i["content_id"], i["id"]) == key), None)
        if not item:
            abort(404)
        def chunks():
            with _bb_lock:
                bb = Blackboard()
                path = (f"/learn/api/public/v1/courses/{item['course_id']}/contents/{item['content_id']}"
                        f"/attachments/{item['id']}/download")
                with bb.get(path, stream=True) as response:
                    yield from response.iter_content(chunk_size=65536)
        return Response(stream_with_context(chunks()), mimetype="application/octet-stream")

    @app.post("/api/download-status")
    def download_status():
        local_request()
        if not CLOUD or request.headers.get("X-Campus-Agent") != "1" or request.headers.get("Origin"):
            abort(403)
        payload = request.get_json()
        if not isinstance(payload, dict) or payload.get("mode") not in {"baseline", "incremental", "error"}:
            abort(400)
        save_json(DATA_ROOT / "download-status.json", payload)
        return jsonify({"ok": True})

    @app.get("/api/discussion-rooms")
    def discussion_rooms():
        try:
            local_request()
            from sustech_survival.lib.booking.client import lib_booking
            day = date.fromisoformat(request.args["date"])
            return jsonify({"date": day.isoformat(), "rooms": schedule(lib_booking(), day)})
        except Exception as exc:
            return jsonify({"error": _public_error(exc)}), 400

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
            return jsonify({"error": _public_error(exc)}), 400

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
        except Exception as exc:
            logging.error("Campus sync failed: %s", type(exc).__name__)
        stop.wait(SYNC_INTERVAL_SECONDS)


def serve(port: int = DASHBOARD_PORT) -> None:
    stop = threading.Event()
    worker = threading.Thread(target=_sync_loop, args=(stop,), daemon=True, name="campus-sync")
    worker.start()
    try:
        print(f"本地页面：http://127.0.0.1:{port}")
        print("启动后立即同步，运行中每 30 分钟同步；按 Ctrl+C 停止。")
        create_app().run(host="127.0.0.1", port=port, use_reloader=False, threaded=True)
    finally:
        stop.set()
