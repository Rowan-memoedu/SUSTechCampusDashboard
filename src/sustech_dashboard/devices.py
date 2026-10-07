"""Short-lived browser grants and narrowly scoped execution-host credentials."""
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlencode

from flask import abort, jsonify, request

from .web_auth import BrowserSessions


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def agent_path(path):
    return path in {'/api/attachments', '/api/materials/agent', '/api/download-status',
                    '/api/printing/agent', '/api/devices/result'} or bool(
        re.fullmatch(r'/api/printing/agent/file/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', path))


class Devices(BrowserSessions):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS devices(digest TEXT PRIMARY KEY, account TEXT NOT NULL,
                    device TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL,
                    account TEXT NOT NULL, expires REAL NOT NULL, intent TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0, device TEXT, result TEXT);
            ''')

    def issue_grant(self, account, intent):
        token, gid = secrets.token_urlsafe(32), uuid.uuid4().hex
        with self.connect() as db:
            db.execute('DELETE FROM grants WHERE expires < ?', (time.time() - 600,))
            db.execute('INSERT INTO grants(id,digest,account,expires,intent) VALUES (?,?,?,?,?)',
                       (gid, digest(token), account, time.time() + 180, json.dumps(intent)))
        return gid, token

    def valid_device(self, token, account):
        if not isinstance(token, str) or len(token) != 43 or not account:
            return False
        with self.connect() as db:
            return db.execute('SELECT 1 FROM devices WHERE digest=? AND account=? AND expires>?',
                              (digest(token), account, time.time())).fetchone() is not None

    def redeem(self, token, account, device, previous=None):
        if not isinstance(token, str) or len(token) != 43 or not re.fullmatch(r'[a-f0-9]{32}', device or ''):
            raise ValueError('连接授权无效，请在已登录的网页重新点击连接')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,intent FROM grants WHERE digest=? AND account=? AND expires>? AND used=0',
                             (digest(token), account, time.time())).fetchone()
            if not row:
                raise ValueError('连接授权已使用或过期，请重新点击连接')
            existing = db.execute('SELECT 1 FROM devices WHERE digest=? AND account=? AND device=? AND expires>?',
                (digest(previous or ''), account, device, time.time())).fetchone()
            credential = previous if existing else secrets.token_urlsafe(32)
            db.execute('DELETE FROM devices WHERE device=?', (device,))
            db.execute('INSERT INTO devices VALUES (?,?,?,?)',
                       (digest(credential), account, device, time.time() + 365 * 86400))
            db.execute('UPDATE grants SET used=1,device=? WHERE id=?', (device, row[0]))
        return {'token': credential, 'id': row[0], 'intent': json.loads(row[1])}


def register_devices(app, guard, account, path):
    store = Devices(path)
    app.extensions['campus_devices'] = store

    @app.post('/api/devices/grant')
    def device_grant():
        guard(write=True)
        payload = request.get_json(silent=True) or {}
        kind, key = payload.get('kind', 'connect'), payload.get('key')
        if kind not in {'connect', 'root', 'file', 'folder'}:
            abort(400)
        if kind in {'file', 'folder'}:
            from . import app as dashboard
            from .core import attachment_key
            manifest = dashboard.load_json(dashboard.MANIFEST_PATH, {})
            if key not in {attachment_key(i['course_id'], i['content_id'], i['id']) for i in manifest.get('items', [])}:
                abort(400)
        gid, token = store.issue_grant(account(), {'kind': kind, 'key': key if kind in {'file', 'folder'} else None})
        server = 'https://' + request.host + request.script_root
        return jsonify(id=gid, uri='sustech-campus://connect#' + urlencode({'server': server, 'ticket': token}))

    @app.post('/api/devices/exchange')
    def device_exchange():
        guard()
        if request.headers.get('Origin') or request.headers.get('X-Campus-Agent') != '1':
            abort(403)
        if not account() or (request.content_length or 0) > 4096:
            abort(403)
        from .hosted import Space
        space = Space()
        payload = request.get_json(silent=True) or {}
        sid = payload.get('sid')
        if not isinstance(sid, str) or not 1 <= len(sid) <= 128:
            abort(400)
        identity = hmac.new(space.key_bytes(), sid.encode(), hashlib.sha256).hexdigest()
        with space.db() as db:
            bound = space.get(db, 'identity', '')
        if not bound or not secrets.compare_digest(bound, identity):
            return jsonify(error='本机校园账号与当前网页空间不一致，未连接'), 403
        try:
            return jsonify(store.redeem(payload.get('ticket'), account(), payload.get('device'), payload.get('previous')))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/devices/grants/<gid>')
    def device_grant_status(gid):
        with store.connect() as db:
            row = db.execute('SELECT used,result,expires FROM grants WHERE id=? AND account=?', (gid, account())).fetchone()
        if not row:
            abort(404)
        return jsonify(state=row[1] or ('expired' if row[2] < time.time() else 'connecting' if row[0] else 'waiting'))

    @app.post('/api/devices/result')
    def device_result():
        auth = request.authorization
        if request.headers.get('Origin') or not auth or auth.type != 'bearer':
            abort(403)
        payload = request.get_json(silent=True) or {}
        state = payload.get('state')
        if state not in {'connected', 'opened', 'open_failed'}:
            abort(400)
        with store.connect() as db:
            device = db.execute('SELECT device FROM devices WHERE digest=? AND account=? AND expires>?',
                                (digest(auth.token), account(), time.time())).fetchone()
            if not device:
                abort(403)
            db.execute('UPDATE grants SET result=? WHERE id=? AND account=? AND device=? AND used=1',
                       (state, payload.get('id'), account(), device[0]))
        return jsonify(ok=True)
