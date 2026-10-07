"""Ephemeral login handoff: browser login authorizes, the local client polls once."""
import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from flask import abort, jsonify, request, session

from .web_auth import BrowserSessions


def digest(key):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
        raise ValueError('Invalid desktop handoff')
    return hashlib.sha256(key.encode()).hexdigest()


class Handoffs(BrowserSessions):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS handoffs(digest TEXT PRIMARY KEY,expires REAL NOT NULL,uri TEXT)')

    def issue(self):
        key = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM handoffs WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO handoffs VALUES (?,?,NULL)', (digest(key), time.time()+600))
        return key

    def publish(self, key, uri):
        with self.connect() as db:
            cursor = db.execute('UPDATE handoffs SET uri=? WHERE digest=? AND expires>? AND uri IS NULL',
                                (uri, digest(key), time.time()))
            return cursor.rowcount == 1

    def claim(self, key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT uri FROM handoffs WHERE digest=? AND expires>?', (digest(key), time.time())).fetchone()
            if row and row[0]:
                db.execute('DELETE FROM handoffs WHERE digest=?', (digest(key),))
                return {'uri': row[0]}
        return {'state': 'waiting' if row else 'expired'}


def register_handoff(app, root):
    store = Handoffs(root/'desktop-handoffs.sqlite3')

    @app.post('/desktop/poll')
    def desktop_poll():
        if request.headers.get('Origin') or request.headers.get('X-Campus-Agent') != '1':
            abort(403)
        try:
            return jsonify(store.claim((request.get_json(silent=True) or {}).get('key')))
        except ValueError:
            abort(400)

    @app.post('/desktop/authorize')
    def desktop_authorize():
        data = request.get_json(silent=True) or {}
        try:
            bound = session.get('desktop_login', {})
            if bound.get('digest') != digest(data.get('key')):
                abort(403)
            uri = urlsplit(data.get('uri', ''))
            values = parse_qs(uri.fragment, strict_parsing=True)
            expected = 'https://' + request.host + bound['prefix']
            if (uri.scheme != 'sustech-campus' or uri.netloc != 'connect' or uri.path not in {'', '/'} or uri.query
                    or set(values) != {'server', 'ticket'} or values['server'] != [expected]
                    or len(values['ticket']) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{43}', values['ticket'][0])):
                abort(400)
            if not store.publish(data['key'], data['uri']):
                return jsonify(error='自动连接已过期，请点击连接电脑重试'), 409
            session.pop('desktop_login', None)
            return jsonify(ok=True)
        except (ValueError, KeyError, TypeError):
            abort(400)

    return store
