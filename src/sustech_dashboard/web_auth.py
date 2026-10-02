"""Persistent browser access for a single owner's HTTPS server."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from flask import abort, g, jsonify, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .paths import DATA_ROOT

REMEMBER_SECONDS = 30 * 24 * 3600
SESSION_SECONDS = 12 * 3600
COOKIE = "__Secure-campus_session"
SIGNED_OUT = "__Secure-campus_signed_out"


class BrowserSessions:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, account TEXT NOT NULL, expires REAL NOT NULL)")
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def issue(self, account, remember):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (
                hashlib.sha256(token.encode()).hexdigest(), account,
                now + (REMEMBER_SECONDS if remember else SESSION_SECONDS)))
        return token

    def valid(self, token, account):
        if not isinstance(token, str) or len(token) != 43:
            return False
        with self.connect() as db:
            row = db.execute("SELECT expires FROM sessions WHERE digest=? AND account=?", (
                hashlib.sha256(token.encode()).hexdigest(), account)).fetchone()
        return row is not None and row[0] > time.time()

    def revoke(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))


def register_web_auth(app, guard, csrf):
    credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "campus-web"
    config = json.loads(credential.read_text(encoding="utf-8"))
    username, password_hash = config["username"], config["password_hash"]
    if (not isinstance(username, str) or not username or not isinstance(password_hash, str)
            or not password_hash.startswith(("scrypt:", "pbkdf2:"))):
        raise ValueError("Website access credential is invalid")
    account = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    sessions = BrowserSessions(DATA_ROOT / "browser-sessions.sqlite3")
    attempts, attempt_lock = OrderedDict(), threading.Lock()

    def credentials_valid(user, password):
        peer = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")[:128]
        now = time.monotonic()
        with attempt_lock:
            recent = [at for at in attempts.get(peer, []) if now - at < 60]
            if len(recent) >= 8:
                abort(429, description="登录尝试过多，请一分钟后重试")
            attempts[peer] = recent + [now]
            attempts.move_to_end(peer)
            if len(attempts) > 4096:
                attempts.popitem(last=False)
        valid = (isinstance(user, str) and isinstance(password, str) and len(password) <= 1024
                 and check_password_hash(password_hash, password)
                 and secrets.compare_digest(user.encode(), username.encode()))
        if valid:
            with attempt_lock:
                attempts.pop(peer, None)
        return valid

    def check_form_csrf():
        if request.headers.get("Origin") != f"https://{request.host}":
            abort(403)
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
        if not secrets.compare_digest(token.encode(), csrf.encode()):
            abort(403)

    def set_session(response, token, remember):
        response.set_cookie(COOKIE, token, max_age=REMEMBER_SECONDS if remember else None,
            secure=True, httponly=True, samesite="Lax", path=request.script_root + "/")
        response.delete_cookie(SIGNED_OUT, path=request.script_root + "/", secure=True, httponly=True, samesite="Lax")

    @app.context_processor
    def browser_context():
        return {"browser_login": True}

    @app.before_request
    def website_access():
        if request.path == "/_health":
            return None  # The runtime's random health token still guards this path.
        try:
            guard()
        except ValueError:
            abort(400)
        checking = request.path == "/auth/verify"
        if checking:
            original = request.headers.get("X-Campus-Original-URI", "").split("?", 1)[0]
            if original in {request.script_root + "/auth/login", request.script_root + "/static/campus.css"}:
                return "", 204
        if request.path == "/auth/login" or request.path == "/static/campus.css":
            return None
        if sessions.valid(request.cookies.get(COOKIE, ""), account):
            return ("", 204) if checking else None
        auth = request.authorization
        if (request.cookies.get(SIGNED_OUT) != "1" and auth and auth.type == "basic"
                and credentials_valid(auth.username, auth.password)):
            # Download agents keep their existing Basic authentication. A browser
            # with cached Basic credentials can migrate without another prompt.
            if checking:
                return "", 204
            if request.method == "GET" and not request.path.startswith("/api/"):
                g.new_browser_session = sessions.issue(account, True)
            return None
        if checking or request.path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
            return jsonify({"error": "请登录校园面板", "login_url": url_for("web_login")}), 401
        return redirect(url_for("web_login"))

    @app.after_request
    def browser_headers(response):
        if getattr(g, "new_browser_session", None):
            set_session(response, g.new_browser_session, True)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.route("/auth/login", methods=["GET", "POST"])
    def web_login():
        error = None
        if request.method == "POST":
            if request.content_length and request.content_length > 8192:
                abort(413)
            check_form_csrf()
            if credentials_valid(request.form.get("username"), request.form.get("password")):
                remember = request.form.get("remember") == "on"
                response = redirect(request.script_root + "/", code=303)
                set_session(response, sessions.issue(account, remember), remember)
                return response
            error = "用户名或密码不正确"
        elif sessions.valid(request.cookies.get(COOKIE, ""), account):
            return redirect(request.script_root + "/")
        return render_template("web_login.html", csrf_token=csrf, error=error), 401 if error else 200

    @app.post("/auth/logout")
    def web_logout():
        check_form_csrf()
        sessions.revoke(request.cookies.get(COOKIE, ""))
        response = redirect(url_for("web_login"), code=303)
        response.delete_cookie(COOKIE, path=request.script_root + "/", secure=True, httponly=True, samesite="Lax")
        # Suppress re-login using a browser's old Basic-auth cache after logout.
        response.set_cookie(SIGNED_OUT, "1", max_age=REMEMBER_SECONDS, secure=True,
                            httponly=True, samesite="Lax", path=request.script_root + "/")
        return response
