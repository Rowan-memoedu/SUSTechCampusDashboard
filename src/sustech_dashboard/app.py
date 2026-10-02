"""Local synchronization and loopback dashboard."""

from __future__ import annotations

import logging
import json
import os
import secrets
import threading
from urllib.parse import quote
from datetime import date, datetime
from typing import Any

from flask import Flask, jsonify, render_template, request, Response, stream_with_context, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from .core import CHINA_TZ, DASHBOARD_PORT, DATA_ROOT, DOWNLOAD_ROOT, attachment_key, load_json, save_json, sync_attachments, parse_dt, safe_name
from .provider import Blackboard, read_bookings, read_tis
from .room_monitor import schedule


SNAPSHOT_PATH = DATA_ROOT / "snapshot.json"
BASELINE_PATH = DATA_ROOT / "attachments.json"
SYNC_INTERVAL_SECONDS = 30 * 60
_sync_lock = threading.Lock()
_bb_lock = threading.Lock()
CLOUD = os.environ.get("SUSTECH_CLOUD") == "1"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
MATERIALS_DB = DATA_ROOT / "materials.sqlite3"
_scan_state = {"running": False, "error": None}
_scan_lock = threading.Lock()


def scan_materials() -> dict:
    if not _scan_lock.acquire(blocking=False):
        return load_json(MANIFEST_PATH, {})
    _scan_state.update(running=True, error=None)
    try:
        with _bb_lock:
            bb = Blackboard()
            courses = bb.current_courses()
            items = []
            for course in courses:
                items.extend(dict(item, course_id=course["id"], course_name=course["name"])
                             for item in bb.attachments(course["id"]))
            if bb.unclassified:
                raise RuntimeError("附件所属课程尚未全部确认")
            manifest = {"updated_at": datetime.now(CHINA_TZ).isoformat(), "courses": courses,
                        "items": items, "warnings": bb.warnings}
            save_json(MANIFEST_PATH, manifest)
        from .materials_store import MaterialsStore
        MaterialsStore(MATERIALS_DB).scan_finished(manifest)
        return manifest
    except Exception as exc:
        _scan_state["error"] = _public_error(exc)
        raise
    finally:
        _scan_state["running"] = False
        _scan_lock.release()


def _materials_loop(stop):
    while not stop.wait(5 * 60):
        try:
            scan_materials()
        except Exception as exc:
            logging.error("Attachment scan failed: %s", type(exc).__name__)


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
            manifest = scan_materials()
            status_path = "download-status.json" if CLOUD else "download-agent.json"
            snapshot["downloads"] = load_json(DATA_ROOT / status_path, {"mode": "agent"})
            snapshot["warnings"].extend(manifest.get("warnings", []))
            snapshot["source_updated_at"]["attachments"] = datetime.now(CHINA_TZ).isoformat()
        except Exception as exc:
            snapshot["errors"]["attachments"] = _public_error(exc)
        save_json(SNAPSHOT_PATH, snapshot)
        return snapshot
    finally:
        _sync_lock.release()


def create_app(runtime=None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config['MAX_CONTENT_LENGTH'] = 256 * 1024 * 1024
    app.json.ensure_ascii = False
    if CLOUD:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_prefix=1)
    csrf = secrets.token_urlsafe(32)
    material_store = None

    def materials():
        nonlocal material_store
        if material_store is None:
            from .materials_store import MaterialsStore
            material_store = MaterialsStore(MATERIALS_DB)
        return material_store

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

    if CLOUD and os.environ.get("SUSTECH_BROWSER_LOGIN") == "1":
        from .web_auth import register_web_auth
        register_web_auth(app, local_request, csrf)

    @app.get("/")
    def index():
        local_request()
        return render_template("index.html", csrf_token=csrf, api_base=request.script_root,
                               download_root=str(DOWNLOAD_ROOT) if not CLOUD else "已配对电脑的下载目录")

    @app.get('/venues/<system>/<category>')
    def venue_page(system,category):
        local_request()
        if system not in {'library','ehall'}:abort(404)
        return render_template('venue.html', csrf_token=csrf, api_base=request.script_root, system=system, category=category)

    @app.get('/api/assignment/<cid>/<iid>')
    def assignment_detail(cid,iid):
        try:
            local_request()
            from .blackboard_work import detail
            from .actions import Actions
            with _bb_lock:result=detail(Blackboard(),cid,iid,request.script_root)
            target=f'assignment:{cid}:{iid}'
            if result['status']=='submitted':Actions().confirm(target,{'state':'confirmed','status':'submitted'})
            result['operation']=Actions().pending(target)
            return jsonify(result)
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.post('/api/assignment/<cid>/<iid>/submit')
    def assignment_submit(cid,iid):
        try:
            local_request(write=True)
            from .blackboard_work import submit
            from .actions import Actions
            token=request.form.get('operation_id')
            files=request.files.getlist('files')
            text=request.form.get('text','');comments=request.form.get('comments','')
            def work():
                with _bb_lock:return submit(Blackboard(),cid,iid,files,text,comments)
            result=Actions().run(token,f'assignment:{cid}:{iid}',work)
            if result.get('status')=='submitted':
                # Keep the overview consistent immediately, without racing its background sync.
                with _sync_lock:
                    snapshot=load_json(SNAPSHOT_PATH,{})
                    for a in snapshot.get('assignments',[]):
                        if a['course_id']==cid and a['content_id']==iid:a['status']='submitted'
                    save_json(SNAPSHOT_PATH,snapshot)
            return jsonify(result),200 if result['state']=='confirmed' else 409
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/blackboard-resource')
    def blackboard_resource():
        try:
            local_request()
            from .blackboard_work import resource_path
            path=resource_path(request.args.get('path',''))
            if not _bb_lock.acquire(timeout=15):return jsonify({'error':'Blackboard 正在读取，请稍后重试'}),503
            try:upstream=Blackboard().get(path,stream=True)
            except Exception:
                _bb_lock.release();raise
            closed=False
            def close_upstream():
                nonlocal closed
                if not closed:
                    closed=True
                    upstream.close();_bb_lock.release()
            def chunks():
                try:yield from upstream.iter_content(65536)
                finally:close_upstream()
            mime=upstream.headers.get('Content-Type','application/octet-stream')
            response=Response(stream_with_context(chunks()),mimetype=mime)
            disposition=upstream.headers.get('Content-Disposition','attachment')
            response.headers['Content-Disposition']=disposition if disposition.lower().startswith('attachment') or mime.startswith(('image/png','image/jpeg','image/gif','image/webp')) else 'attachment'
            if request.args.get('name'):
                response.headers['Content-Disposition']="attachment; filename*=UTF-8''"+quote(safe_name(request.args['name']),safe='')
            response.headers['Content-Security-Policy']="sandbox; default-src 'none'"
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Cache-Control']='private, no-store'
            response.call_on_close(close_upstream)
            return response
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/venues')
    def venues_catalog():
        local_request()
        from .venues import catalog
        return jsonify(catalog())

    @app.get('/api/venues/schedule')
    def venue_schedule():
        try:
            local_request()
            from .venues import schedules,interval
            system=request.args['system'];category=request.args['category'];day=date.fromisoformat(request.args['date'])
            begin=end=None
            if request.args.get('begin'):
                begin,end=interval(request.args)
                if begin.date()!=day:raise ValueError('时段与查询日期不一致')
            return jsonify(schedules(system,category,day,begin,end))
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/venues/mine')
    def venue_mine():
        try:
            local_request()
            from .venues import library_mine,eh_mine
            start=date.fromisoformat(request.args['start']);end=date.fromisoformat(request.args['end'])
            if not start<=end or (end-start).days>400:raise ValueError('预约记录查询范围无效')
            system=request.args['system']
            if system not in {'library','ehall'}:raise ValueError('预约系统无效')
            records=library_mine(start,end,request.args.get('status')) if system=='library' else eh_mine(start,end)
            from .actions import Actions
            for record in records:
                if (system=='library' and int(record.get('resvStatus',0))&128) or record.get('IsCancel') or record.get('CancelDateTime'):continue
                b,e=parse_dt(record.get('begin')),parse_dt(record.get('end'))
                if not b or not e:continue
                rooms=[d.get('devId') for d in record.get('resvDevInfoList') or []] if system=='library' else [record.get('MeetingRoomID')]
                for room in rooms:
                    if room is not None:Actions().confirm(f'book:{system}:{room}:{b.isoformat()}:{e.isoformat()}',{'state':'confirmed','reservation':record,'message':'已从官方我的预约记录核对成功'})
            return jsonify({'records':records})
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/venues/members')
    def venue_members():
        try:
            local_request()
            from .venues import members
            return jsonify({'members':members(request.args['system'],request.args.get('q',''))})
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.post('/api/venues/book')
    @app.post('/api/venues/action')
    def venue_write():
        try:
            local_request(write=True)
            from .venues import book,reservation_action
            from .actions import Actions
            payload=json.loads(request.form['payload']) if 'payload' in request.form else request.get_json()
            if not isinstance(payload,dict):raise ValueError('请求格式无效')
            system=payload.get('system')
            if request.path.endswith('/book'):
                from .venues import interval
                begin,end=interval(payload)
                target=f'book:{system}:{payload.get("room_id")}:{begin.isoformat()}:{end.isoformat()}'
                work=lambda:book(payload,request.files.getlist('files'))
            else:
                target=f'reservation:{system}:{payload.get("reservation_id")}'
                work=lambda:reservation_action(payload)
            result=Actions().run(payload.get('operation_id'),target,work)
            return jsonify(result),200 if result['state']=='confirmed' else 409
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/venues/extend-options')
    def venue_extensions():
        try:
            local_request()
            from .venues import extension_options
            return jsonify({'durations':extension_options(request.args['reservation_id'])})
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

    @app.get('/api/venues/reservation')
    def venue_reservation():
        try:
            local_request()
            from .venues import reservation_detail
            return jsonify(reservation_detail(request.args['system'],request.args['reservation_id']))
        except Exception as exc:return jsonify({'error':_public_error(exc)}),400

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
        manifest = load_json(MANIFEST_PATH, {})
        if not manifest:
            return jsonify({"error": "附件扫描尚未完成"}), 503
        # Do not present an old scan as a current list when upstream sync failed.
        if _scan_state["error"]:
            return jsonify({"error": "本轮附件查询失败，暂缓下载"}), 503
        return jsonify(manifest)

    @app.get("/api/attachment")
    def attachment_download():
        local_request()
        key = request.args.get("key", "")
        item = next((i for i in load_json(MANIFEST_PATH, {}).get("items", [])
                     if attachment_key(i["course_id"], i["content_id"], i["id"]) == key), None)
        if not item:
            abort(404)
        # Resolve upstream errors before sending 200 or download headers.
        if not _bb_lock.acquire(timeout=15):
            return jsonify({"error": "资料扫描或下载正在进行，请稍后重试"}), 503
        try:
            bb = Blackboard()
            path = (f"/learn/api/public/v1/courses/{item['course_id']}/contents/{item['content_id']}"
                    f"/attachments/{item['id']}/download")
            upstream = bb.get(path, stream=True)
        except Exception as exc:
            _bb_lock.release()
            return jsonify({"error": _public_error(exc)}), 502
        closed = False
        def close_upstream():
            nonlocal closed
            if not closed:
                closed = True
                upstream.close()
                _bb_lock.release()
        def chunks():
            try:
                yield from upstream.iter_content(chunk_size=65536)
            finally:
                close_upstream()
        from .core import safe_name
        name = safe_name(item["file_name"])
        response = Response(stream_with_context(chunks()), mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = f"attachment; filename=attachment; filename*=UTF-8''{quote(name)}"
        if upstream.headers.get("Content-Length", "").isdigit():
            response.headers["Content-Length"] = upstream.headers["Content-Length"]
        response.headers["Cache-Control"] = "private, no-store"
        response.call_on_close(close_upstream)
        return response

    @app.get("/api/materials")
    def material_list():
        local_request()
        manifest = load_json(MANIFEST_PATH, {})
        result = materials().view(manifest)
        result["scan"] = dict(_scan_state)
        result["destination"] = str(DOWNLOAD_ROOT) if not CLOUD else "已配对电脑的下载目录"
        return jsonify(result)

    @app.post("/api/materials/refresh")
    def refresh_materials():
        try:
            local_request(write=True)
            materials()
            if not _scan_state["running"]:
                def run_scan():
                    try:
                        scan_materials()
                    except Exception:
                        pass  # Sanitized failure is shown in the materials panel.
                threading.Thread(target=run_scan, daemon=True).start()
            return jsonify({"accepted": True}), 202
        except Exception as exc:
            return jsonify({"error": _public_error(exc)}), 400

    @app.post("/api/materials/auto")
    def material_auto():
        try:
            local_request(write=True)
            materials().set_auto(request.get_json()["enabled"])
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": _public_error(exc)}), 400

    @app.post("/api/materials/jobs")
    def material_job():
        try:
            local_request(write=True)
            payload = request.get_json()
            manifest = load_json(MANIFEST_PATH, {})
            scope = payload.get("scope")
            items = manifest.get("items", [])
            if scope == "file":
                items = [i for i in items if attachment_key(i["course_id"], i["content_id"], i["id"]) == payload.get("key")]
                label = items[0]["file_name"] if items else "附件"
            elif scope == "course":
                items = [i for i in items if i["course_id"] == payload.get("course_id")]
                label = "课程全部下载：" + (items[0]["course_name"] if items else "课程")
            elif scope == "all":
                label = "全部课程资料下载"
            elif scope == "retry":
                keys = set(materials().retry_keys(payload.get("job_id")))
                items = [i for i in items if attachment_key(i["course_id"], i["content_id"], i["id"]) in keys]
                label = "重试未完成附件"
            else:
                raise ValueError("下载范围无效")
            jid = materials().enqueue([attachment_key(i["course_id"], i["content_id"], i["id"]) for i in items], label)
            return jsonify({"job_id": jid, "accepted": True}), 202
        except Exception as exc:
            return jsonify({"error": _public_error(exc)}), 400

    @app.post("/api/materials/agent")
    def material_agent():
        local_request()
        if not CLOUD or request.headers.get("X-Campus-Agent") != "1" or request.headers.get("Origin"):
            abort(403)
        try:
            payload = request.get_json()
            return jsonify(materials().agent_poll(payload, load_json(MANIFEST_PATH, {})))
        except Exception as exc:
            return jsonify({"error": _public_error(exc)}), 400

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
        return jsonify({'error':'讨论间后台监控已撤销，请使用场地与预约'}),410

    @app.post("/api/room-watch")
    def create_room_watch():
        return jsonify({'error':'讨论间后台监控已撤销，请使用场地与预约'}),410

    @app.post("/api/room-watch/stop")
    def stop_room_watch():
        return jsonify({'error':'讨论间后台监控已撤销，请使用场地与预约'}),410

    from .services_routes import register_services
    register_services(app, local_request, csrf, _public_error)
    if runtime is not None:
        from .instance_routes import register_instance
        register_instance(app, runtime, local_request, csrf)
    return app


def _sync_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            sync_all()
        except Exception as exc:
            logging.error("Campus sync failed: %s", type(exc).__name__)
        stop.wait(SYNC_INTERVAL_SECONDS)


def serve(port: int = DASHBOARD_PORT) -> None:
    from .runtime import backend
    backend(port=port)
