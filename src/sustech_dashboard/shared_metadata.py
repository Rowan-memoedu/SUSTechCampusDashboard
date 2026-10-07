"""Authenticated metadata-only service, immutable references and fenced refresh leases.

Unknown Blackboard visibility is checked by each personal process. Publishing data
already read with that identity deduplicates storage, never grants another identity
access by hash. Refresh merging is allowed only for operator-verified scopes.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from flask import Flask, abort, jsonify, request
import requests

FIELDS = {
    'course': {'id', 'name', 'term_id'},
    'attachment': {'course_id', 'content_id', 'id', 'file_name', 'title', 'folders', 'size', 'source_version'},
    'assignment': {'course_id', 'content_id', 'name', 'kind', 'attempts_allowed'},
    'weather': {'condition', 'temperature', 'aqi'},
}


class MetadataError(ValueError):
    pass


class MetadataQuota(MetadataError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def normalize(kind, body):
    if kind not in FIELDS or not isinstance(body, dict) or set(body) - FIELDS[kind]:
        raise MetadataError('元数据字段不在允许范围')
    for key, value in body.items():
        if key == 'folders':
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(x, str) or len(x) > 1024 for x in value):
                raise MetadataError('目录元数据过大')
        elif value is not None and not isinstance(value, (str, int, float)):
            raise MetadataError('元数据类型无效')
        elif isinstance(value, str) and (len(value) > 4096 or '\x00' in value or value.startswith('data:')):
            raise MetadataError('元数据字段过大或含文件内容')
    encoded = canonical(body)
    if len(encoded.encode()) > 32768:
        raise MetadataQuota('单对象元数据超过配额')
    return encoded


class MetadataStore:
    def __init__(self, path, max_bytes=256*1024*1024, clock=time.time):
        self.path, self.max_bytes, self.clock = Path(path), max_bytes, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS blobs(digest TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS refs(owner TEXT,resource TEXT,digest TEXT,expires REAL,PRIMARY KEY(owner,resource,digest));
                CREATE TABLE IF NOT EXISTS resources(resource TEXT PRIMARY KEY,kind TEXT,scope TEXT,digest TEXT,expires REAL,etag TEXT,modified TEXT);
                CREATE TABLE IF NOT EXISTS leases(resource TEXT PRIMARY KEY,owner TEXT,fence TEXT,expires REAL,retry REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS metrics(name TEXT PRIMARY KEY,value INTEGER NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute(f'PRAGMA max_page_count={max(16, self.max_bytes // 4096)}')
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        except sqlite3.OperationalError as exc:
            if 'full' in str(exc).lower():
                raise MetadataQuota('共享元数据已达到容量上限') from None
            raise
        finally:
            db.close()

    @staticmethod
    def metric(db, name, count=1):
        db.execute('INSERT INTO metrics VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value', (name, count))

    def collect(self, db):
        now = self.clock()
        db.execute('DELETE FROM refs WHERE expires < ?', (now,))
        db.execute('DELETE FROM leases WHERE expires < ? AND retry < ?', (now-86400, now))
        db.execute('DELETE FROM resources WHERE expires < ? AND resource NOT IN (SELECT resource FROM refs)', (now-7*86400,))
        # Referenced versions remain readable until their authorization expires.
        db.execute('DELETE FROM blobs WHERE digest NOT IN (SELECT digest FROM refs) AND digest NOT IN (SELECT digest FROM resources)')

    def _publish(self, db, owner, resource, kind, body, ttl=3600):
        encoded = normalize(kind, body)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.collect(db)
        if db.execute('SELECT count(*) FROM refs WHERE owner=?', (owner,)).fetchone()[0] >= 20000:
            raise MetadataQuota('个人引用数量已达到上限')
        used = db.execute('SELECT coalesce(sum(length(body)),0) FROM blobs').fetchone()[0]
        if used + len(encoded.encode()) + 65536 > self.max_bytes:
            raise MetadataQuota('共享元数据已达到容量上限')
        db.execute('INSERT OR IGNORE INTO blobs VALUES (?,?,?)', (digest, encoded, self.clock()))
        db.execute('INSERT OR REPLACE INTO refs VALUES (?,?,?,?)', (owner, resource, digest, self.clock()+min(3600, max(1, ttl))))
        # At most two pinned versions per owner/resource; snapshots publish atomically.
        db.execute('DELETE FROM refs WHERE owner=? AND resource=? AND digest NOT IN (SELECT digest FROM refs WHERE owner=? AND resource=? ORDER BY expires DESC LIMIT 2)', (owner, resource, owner, resource))
        self.metric(db, 'published_references')
        return {'resource': resource, 'digest': digest, 'kind': kind}

    def publish(self, owner, entries):
        if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
            raise MetadataError('元数据批次无效')
        with self.db() as db:
            result = []
            for entry in entries:
                resource = entry.get('resource')
                if not isinstance(resource, str) or not 1 <= len(resource) <= 512:
                    raise MetadataError('资源标识无效')
                result.append(self._publish(db, owner, resource, entry['kind'], entry['body']))
                self.metric(db, 'authorized_objects')
            return result

    def resolve(self, owner, refs):
        if not isinstance(refs, list) or len(refs) > 200:
            raise MetadataError('引用批次无效')
        with self.db() as db:
            results = []
            for ref in refs:
                row = db.execute('SELECT body FROM refs JOIN blobs USING(digest) WHERE owner=? AND resource=? AND digest=? AND expires>?',
                                 (owner, ref['resource'], ref['digest'], self.clock())).fetchone()
                if not row:
                    raise MetadataError('元数据授权已过期，请刷新校园数据')
                results.append(json.loads(row[0]))
            return results

    def revoke(self, owner):
        with self.db() as db:
            db.execute('DELETE FROM refs WHERE owner=?', (owner,))
            db.execute('DELETE FROM leases WHERE owner=?', (owner,))
            self.collect(db)

    def acquire(self, owner, resource, kind, scope, allowed_scopes):
        if scope not in allowed_scopes or kind not in FIELDS or len(resource) > 512:
            raise MetadataError('此资源的跨用户可见范围尚未核验')
        if scope == 'public:weather' and (kind != 'weather' or resource != 'sustech-weather'):
            raise MetadataError('公共天气范围不适用于课程资源')
        key = canonical([kind, scope, resource])
        now = self.clock()
        with self.db() as db:
            row = db.execute('SELECT * FROM resources WHERE resource=?', (key,)).fetchone()
            if row and row['expires'] > now:
                body = json.loads(db.execute('SELECT body FROM blobs WHERE digest=?', (row['digest'],)).fetchone()[0])
                ref = self._publish(db, owner, key, kind, body)
                self.metric(db, 'cache_hits')
                return {'state': 'ready', 'body': body, 'ref': ref}
            lease = db.execute('SELECT * FROM leases WHERE resource=?', (key,)).fetchone()
            if lease and (lease['expires'] > now or lease['retry'] > now):
                self.metric(db, 'coalesced')
                return {'state': 'wait', 'retry_after': max(.1, min(2, max(lease['expires'], lease['retry']) - now))}
            fence = secrets.token_urlsafe(24)
            db.execute('INSERT OR REPLACE INTO leases VALUES (?,?,?,?,0)', (key, owner, fence, now+120))
            self.metric(db, 'refresh_rounds')
            return {'state': 'refresh', 'resource': key, 'fence': fence,
                    'etag': row['etag'] if row else None, 'modified': row['modified'] if row else None}

    def finish(self, owner, resource, fence, kind, body=None, not_modified=False, etag=None, modified=None, source_requests=1):
        with self.db() as db:
            lease = db.execute('SELECT * FROM leases WHERE resource=?', (resource,)).fetchone()
            if not lease or lease['owner'] != owner or lease['fence'] != fence or lease['expires'] <= self.clock():
                raise MetadataError('刷新租约已失效')
            key_kind, scope, _ = json.loads(resource)
            if key_kind != kind:
                raise MetadataError('刷新对象不匹配')
            if not_modified:
                row = db.execute('SELECT body,etag,modified FROM resources JOIN blobs USING(digest) WHERE resource=?', (resource,)).fetchone()
                if not row:
                    raise MetadataError('304 缺少原始版本')
                body = json.loads(row[0])
                etag = etag or row['etag']
                modified = modified or row['modified']
            ref = self._publish(db, owner, resource, kind, body)
            db.execute('INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?)',
                       (resource, kind, scope, ref['digest'], self.clock()+300, (etag or '')[:512], (modified or '')[:128]))
            db.execute('DELETE FROM leases WHERE resource=?', (resource,))
            self.metric(db, 'source_requests', min(1000, max(0, int(source_requests))))
            return ref

    def fail(self, owner, resource, fence):
        with self.db() as db:
            db.execute('UPDATE leases SET expires=0,retry=? WHERE resource=? AND owner=? AND fence=?', (self.clock()+10, resource, owner, fence))
            self.metric(db, 'refresh_failures')

    def invalidate(self, owner, resource, kind, scope, allowed_scopes):
        if scope not in allowed_scopes or (scope == 'public:weather' and (kind, resource) != ('weather', 'sustech-weather')):
            raise MetadataError('未经核验的资源范围')
        key = canonical([kind, scope, resource])
        with self.db() as db:
            db.execute('UPDATE resources SET expires=0 WHERE resource=?', (key,))
            db.execute('DELETE FROM leases WHERE resource=?', (key,))
            self.metric(db, 'invalidations')

    def stats(self):
        with self.db() as db:
            return {**dict(db.execute('SELECT name,value FROM metrics').fetchall()),
                    'objects': db.execute('SELECT count(*) FROM blobs').fetchone()[0],
                    'references': db.execute('SELECT count(*) FROM refs').fetchone()[0],
                    'bytes': self.path.stat().st_size, 'attachment_bytes': 0}


def service_app(store, registry_loader):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 1024*1024

    @app.post('/v1/metadata')
    def operate():
        digest = hashlib.sha256(request.headers.get('Authorization', '').removeprefix('Bearer ').encode()).hexdigest()
        registry = registry_loader()
        identity = next(((owner, entry) for owner, entry in registry.items()
                         if entry.get('enabled', True) and secrets.compare_digest(entry['digest'], digest)), None)
        if not identity:
            abort(401)
        owner, entry = identity
        data = request.get_json()
        operation = data.get('operation')
        try:
            if operation == 'publish':
                result = store.publish(owner, data['entries'])
            elif operation == 'resolve':
                result = store.resolve(owner, data['refs'])
            elif operation == 'revoke':
                store.revoke(owner)
                result = True
            elif operation == 'count':
                metrics = data.get('metrics', {})
                if set(metrics) - {'visibility_requests', 'personal_state_requests'}:
                    raise MetadataError('Unsupported counter')
                with store.db() as db:
                    for key, value in metrics.items():
                        store.metric(db, key, min(10000, max(0, int(value))))
                result = True
            elif operation == 'acquire':
                result = store.acquire(owner, data['resource'], data['kind'], data['scope'], entry.get('scopes', []))
            elif operation == 'finish':
                result = store.finish(owner, **{k: v for k, v in data.items() if k != 'operation'})
            elif operation == 'fail':
                store.fail(owner, data['resource'], data['fence'])
                result = True
            elif operation == 'invalidate':
                store.invalidate(owner, data['resource'], data['kind'], data['scope'], entry.get('scopes', []))
                result = True
            else:
                abort(400)
            return jsonify(result=result)
        except MetadataQuota as exc:
            return jsonify(error=str(exc)), 507
        except (MetadataError, KeyError, TypeError, ValueError):
            return jsonify(error='元数据请求或授权无效，请刷新后重试'), 400

    @app.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response
    return app


class MetadataClient:
    def __init__(self, url, token):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in {'', '/'} or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Shared metadata must use the registered loopback service')
        self.url, self.token = url.rstrip('/') + '/v1/metadata', token

    def call(self, operation, **payload):
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(self.url, headers={'Authorization': 'Bearer '+self.token},
                                    json={'operation': operation, **payload}, timeout=(3, 30), allow_redirects=False)
        if response.status_code != 200:
            raise MetadataError('共享索引暂不可用或已达到配额，请稍后刷新')
        return response.json()['result']

    def cached(self, resource, kind, scope, fetch):
        deadline = time.monotonic()+135
        while time.monotonic() < deadline:
            result = self.call('acquire', resource=resource, kind=kind, scope=scope)
            if result['state'] == 'ready':
                return result['body']
            if result['state'] == 'wait':
                time.sleep(result['retry_after'])
                continue
            try:
                fetched = fetch(result.get('etag'), result.get('modified'))
                self.call('finish', resource=result['resource'], fence=result['fence'], kind=kind, **fetched)
            except Exception:
                self.call('fail', resource=result['resource'], fence=result['fence'])
                raise
        raise MetadataError('共享刷新等待超时')


def configured_client():
    from .execution import hosted
    if not hosted():
        return None
    directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    return MetadataClient(os.environ['SUSTECH_METADATA_URL'], (directory/'campus-metadata').read_text().strip())


def pack_document(value, client):
    """Extract only approved shared fields; keep all personal differences local."""
    result = dict(value)
    for field, kind in [('courses', 'course'), ('blackboard_courses', 'course'), ('items', 'attachment'), ('assignments', 'assignment')]:
        if field not in result:
            continue
        rows = result[field]
        packed = []
        for start in range(0, len(rows), 100):
            batch = rows[start:start+100]
            entries = []
            for row in batch:
                body = {key: value for key, value in row.items() if key in FIELDS[kind]}
                resource = canonical(['sustech', row.get('term_id', 'official-course-id'), kind,
                                      row.get('course_id', ''), row.get('content_id', ''), row.get('id', '')])
                entries.append({'resource': resource, 'kind': kind, 'body': body})
            refs = client.call('publish', entries=entries)
            for row, ref in zip(batch, refs):
                packed.append({'_metadata_ref': ref, **{key: value for key, value in row.items() if key not in FIELDS[kind]}})
        result[field] = packed
    return result


def unpack_document(value, client):
    result = dict(value)
    for field in ('courses', 'blackboard_courses', 'items', 'assignments'):
        if field not in result:
            continue
        output = []
        for start in range(0, len(result[field]), 100):
            batch = result[field][start:start+100]
            refs = [row['_metadata_ref'] for row in batch if '_metadata_ref' in row]
            values = iter(client.call('resolve', refs=refs)) if refs else iter([])
            for row in batch:
                output.append({**next(values), **{k: v for k, v in row.items() if k != '_metadata_ref'}} if '_metadata_ref' in row else row)
        result[field] = output
    return result


def main():
    from waitress import serve
    root = Path(os.environ['SUSTECH_METADATA_ROOT'])
    store = MetadataStore(root/'metadata.sqlite3')
    import threading
    def housekeeping():
        while True:
            with store.db() as db:
                store.collect(db)
            time.sleep(3600)
    threading.Thread(target=housekeeping, daemon=True).start()
    def registry():
        return json.loads((Path(os.environ['SUSTECH_METADATA_REGISTRY'])).read_text())
    serve(service_app(store, registry), host='127.0.0.1', port=int(os.environ.get('SUSTECH_METADATA_PORT', '18800')), threads=8)


if __name__ == '__main__':
    main()
