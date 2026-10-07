"""Single-space invitation, revocation and encrypted, replaceable CAS storage."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet
from flask import abort, jsonify, redirect, render_template, request
from werkzeug.security import generate_password_hash, check_password_hash

from .paths import DATA_ROOT


def space_id():
    value = os.environ.get('SUSTECH_SPACE_ID', '')
    if not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Invalid hosted space ID')
    return value


class Space:
    def __init__(self, root=None, key=None):
        self.root = Path(root or DATA_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'space.sqlite3'
        self.key = key
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(at REAL NOT NULL);
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default=None):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def cipher(self):
        return Fernet(self.key_bytes())

    def key_bytes(self):
        return self.key or (Path(os.environ['CREDENTIALS_DIRECTORY']) / 'campus-vault').read_bytes().strip()

    def invite(self, ttl=86400):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            if self.get(db, 'web'):
                raise ValueError('Space has already been activated')
            self.put(db, 'invitation', {'digest': hashlib.sha256(token.encode()).hexdigest(), 'expires': time.time() + ttl})
            self.put(db, 'enabled', True)
        return token

    def redeem(self, token, password):
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError('面板密码需为 12–256 个字符')
        if not isinstance(token, str) or len(token) != 43:
            raise ValueError('邀请无效或已过期')
        self.throttle()
        with self.db() as db:
            invite = self.get(db, 'invitation', {})
            if (not self.get(db, 'enabled', False) or self.get(db, 'web') or
                    invite.get('expires', 0) < time.time() or not secrets.compare_digest(
                        invite.get('digest', ''), hashlib.sha256(token.encode()).hexdigest())):
                raise ValueError('邀请无效或已过期')
            self.put(db, 'web', {'username': 'member', 'password_hash': generate_password_hash(password)})
            db.execute("DELETE FROM settings WHERE key='invitation'")

    def web_config(self):
        with self.db() as db:
            return self.get(db, 'web') if self.get(db, 'enabled', False) else None

    def throttle(self):
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE at < ?', (time.time() - 60,))
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 5:
                raise ValueError('尝试过多，请一分钟后重试')
            db.execute('INSERT INTO attempts VALUES (?)', (time.time(),))

    def credentials(self):
        with self.db() as db:
            token = self.get(db, 'cas') if self.get(db, 'enabled', False) else None
        return json.loads(self.cipher().decrypt(token.encode())) if token else None

    def bind(self, sid, password, remember, verify):
        self.throttle()
        if not isinstance(sid, str) or not isinstance(password, str):
            raise ValueError('请输入学号和 CAS 密码')
        sid = sid.strip()
        if not sid or len(sid) > 128 or not password or len(password) > 1024 or any(c in sid + password for c in '\r\n\x00'):
            raise ValueError('账号或密码格式无效')
        cipher = self.cipher()
        # Identity remains bound after disconnect; reusing a space for another student is forbidden.
        identity = hmac.new(self.key_bytes(), sid.encode(), hashlib.sha256).hexdigest()
        with self.db() as db:
            if not self.get(db, 'web') or not self.get(db, 'enabled', False):
                raise ValueError('空间尚未开通或已撤销')
            previous = self.get(db, 'identity')
            if previous and not secrets.compare_digest(previous, identity):
                raise ValueError('此空间已绑定其他校园身份，请使用独立空间')
        verify(sid, password)
        with self.db() as db:
            self.put(db, 'identity', identity)
            if remember:
                self.put(db, 'cas', cipher.encrypt(json.dumps({'sid': sid, 'password': password}).encode()).decode())
            else:
                db.execute("DELETE FROM settings WHERE key='cas'")

    def disconnect(self):
        with self.db() as db:
            db.execute("DELETE FROM settings WHERE key='cas'")

    def change_password(self, previous, password):
        self.throttle()
        if not isinstance(previous, str) or len(previous) > 256 or not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError('密码格式无效，新密码需为 12–256 个字符')
        with self.db() as db:
            web = self.get(db, 'web')
            if not self.get(db, 'enabled', False) or not web or not check_password_hash(web['password_hash'], previous):
                raise ValueError('当前面板密码不正确')
            self.put(db, 'web', {'username': 'member', 'password_hash': generate_password_hash(password)})

    def revoke(self):
        with self.db() as db:
            self.put(db, 'enabled', False)
            db.execute("DELETE FROM settings WHERE key IN ('cas','invitation')")


def register_hosted(app, runtime, guard, csrf):
    space = Space()

    @app.context_processor
    def hosted_context():
        return {'hosted': True}

    @app.post('/api/instance/password')
    def hosted_password():
        guard(write=True)
        if (request.content_length or 0) > 4096:
            abort(413)
        try:
            payload = request.get_json()
            space.change_password(payload.get('previous'), payload.get('password'))
            return jsonify(ok=True, login_url=request.script_root+'/auth/login')
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.route('/invite', methods=['GET', 'POST'])
    def hosted_invite():
        error = None
        if request.method == 'POST':
            if (request.content_length or 0) > 8192:
                abort(413)
            if request.headers.get('Origin') != f'https://{request.host}' or not secrets.compare_digest(
                    request.form.get('csrf', '').encode(), csrf.encode()):
                abort(403)
            try:
                space.redeem(request.form.get('invitation'), request.form.get('password'))
                return redirect(request.script_root + '/auth/login', code=303)
            except ValueError as exc:
                error = str(exc)
        return render_template('hosted_invite.html', csrf_token=csrf, error=error), 400 if error else 200

    @app.post('/api/instance/disconnect')
    def hosted_disconnect():
        guard(write=True)
        if not runtime.configure_lock.acquire(False):
            return jsonify(error='正在更新学校账号'), 409
        try:
            with runtime.guard:
                runtime.draining = True
                if not runtime.guard.wait_for(lambda: runtime.active_writes <= 1, timeout=60):
                    return jsonify(error='校园操作尚未结束，请稍后重试'), 409
            runtime.configured.clear()
            from . import app as dashboard
            from .authentication import clear_credentials
            with dashboard._sync_lock, dashboard._scan_lock, dashboard._bb_lock:
                space.disconnect()
                clear_credentials()
                from .shared_metadata import configured_client
                client = configured_client()
                if client:
                    client.call('revoke')
            return jsonify(ok=True)
        finally:
            runtime.draining = False
            runtime.configure_lock.release()
