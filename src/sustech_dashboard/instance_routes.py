"""Local browser authentication, first-run login and update controls."""
import os
import hashlib
import secrets
import threading
from pathlib import Path

from flask import abort, g, jsonify, redirect, render_template, request

from . import __version__
from .core import DATA_ROOT, DOWNLOAD_ROOT, load_json


def register_instance(app, runtime, guard, csrf):
    cloud = os.environ.get("SUSTECH_CLOUD") == "1"
    cookie = "campus_" + hashlib.sha256(str(DATA_ROOT).encode()).hexdigest()[:16]

    @app.before_request
    def protect_instance():
        if request.path == "/_health":
            if request.remote_addr not in {"127.0.0.1", "::1"} or not secrets.compare_digest(
                    request.headers.get("X-Instance-Health", ""), runtime.health_token):
                abort(404)
            return jsonify({"version": __version__, "ready": runtime.ready.is_set()})
        try:
            guard()
        except ValueError:
            abort(400)
        if not cloud and request.path.startswith("/static/"):
            return None
        if not cloud and request.path == "/auth/unlock":
            return None
        if not cloud and not secrets.compare_digest(request.cookies.get(cookie, ""), runtime.token):
            if request.path == "/":
                return render_template("unlock.html")
            abort(401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            with runtime.guard:
                if runtime.draining:
                    return jsonify({"error": "正在安装更新，请等待页面恢复"}), 503
                runtime.active_writes += 1
                g.campus_write = True
        if not runtime.configured.is_set() and request.endpoint not in {
                "setup_page", "configure_owner", "instance_status", "settings_page", "static", "check_update", "install_update", "stop_instance"}:
            if request.path.startswith("/api/"):
                return jsonify({"error": "请先在本机登录校园账号", "setup_required": True}), 401
            return redirect(request.script_root + "/setup")

    @app.teardown_request
    def finish_write(_error):
        if getattr(g, "campus_write", False):
            with runtime.guard:
                runtime.active_writes -= 1
                runtime.guard.notify_all()

    @app.after_request
    def privacy_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if response.mimetype == "text/html":
            response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.post("/auth/unlock")
    def unlock():
        if cloud or request.headers.get("Origin") != f"http://{request.host}":
            abort(403)
        payload = request.get_json(silent=True) or {}
        token = payload.get("token", "")
        if not isinstance(token, str) or not secrets.compare_digest(token, runtime.token):
            abort(403)
        response = jsonify({"ok": True})
        response.set_cookie(cookie, runtime.token, httponly=True, samesite="Strict", path="/")
        return response

    @app.get("/setup")
    def setup_page():
        return render_template("setup.html", csrf_token=csrf, api_base=request.script_root,
                               cloud=cloud, can_remember=os.name == "nt", configured=runtime.configured.is_set(),
                               credential_error=runtime.credential_error)

    @app.post("/api/instance/login")
    def configure_owner():
        try:
            guard(write=True)
            if cloud:
                abort(403)
            if not runtime.configure_lock.acquire(False):
                return jsonify({"error": "正在登录或安装更新"}), 409
            try:
                if runtime.configured.is_set():
                    return jsonify({"error": "该实例已有账号；其他用户应使用自己的独立实例"}), 409
                payload = request.get_json()
                from .authentication import configure_credentials
                configure_credentials(payload.get("sid"), payload.get("password"), payload.get("remember") is True)
                runtime.configured.set()
                runtime.credential_error = False
                return jsonify({"ok": True})
            finally:
                runtime.configure_lock.release()
        except Exception as exc:
            from .app import _public_error
            return jsonify({"error": _public_error(exc)}), 400

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html", csrf_token=csrf, api_base=request.script_root)

    @app.get("/api/instance")
    def instance_status():
        return jsonify({"version": __version__, "mode": "个人服务器" if cloud else "本机客户端",
            "configured": runtime.configured.is_set(), "data_root": str(DATA_ROOT), "download_root": str(DOWNLOAD_ROOT),
            "download_mode": "paired" if cloud and os.environ.get("SUSTECH_DOWNLOAD_MODE") != "local" else "local",
            "update": dict(runtime.update.state), "managed": bool(os.environ.get("SUSTECH_MANAGED_RUNTIME")),
            "last_update": load_json(DATA_ROOT / "updates/last-result.json", {}),
            "credential_error": runtime.credential_error})

    @app.post("/api/instance/updates/check")
    def check_update():
        try:
            guard(write=True)
            threading.Thread(target=runtime.update.check, daemon=True).start()
            return jsonify({"accepted": True}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/instance/updates/install")
    def install_update():
        try:
            guard(write=True)
            runtime.begin_install()
            return jsonify({"accepted": True}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/instance/stop")
    def stop_instance():
        if cloud:
            abort(403)
        try:
            guard(write=True)
            with runtime.guard:
                runtime.draining = True
            runtime.stop.set()
            def finish():
                for worker in runtime.workers:
                    worker.join()
                with runtime.guard:
                    runtime.guard.wait_for(lambda: runtime.active_writes == 0)
                runtime.shutdown = True
            threading.Thread(target=finish, daemon=True).start()
            return jsonify({"accepted": True}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
