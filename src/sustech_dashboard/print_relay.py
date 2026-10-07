"""Per-owner print requests for one explicitly selected paired campus computer."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .core import DATA_ROOT

OFFLINE = '执行主机离线或尚未连接校园网；队列状态未知，请启动所属主机客户端后重新连接'
UNCERTAIN = '打印操作结果尚未确认；请核对学校队列，暂不重复操作'


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('打印操作标识无效') from None


def agent_identifier(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('执行主机标识无效')
    return value


class PrintRelay:
    def __init__(self, root=None):
        self.root = Path(root or DATA_ROOT / 'print-relay')
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, at REAL, ready INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, kind TEXT, target TEXT,
                    payload TEXT, owner TEXT, state TEXT, at REAL, touched REAL, result TEXT);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / 'relay.sqlite3', timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def file(self, jid):
        return self.root / (identifier(jid) + '.document')

    def expire(self, db):
        for row in db.execute("SELECT id,kind,state FROM operations WHERE state IN ('queued','running') AND touched < ?",
                              (time.time() - 180,)).fetchall():
            state = 'needs_review' if row['state'] == 'running' and row['kind'] in {'upload', 'delete'} else 'rejected'
            result = {'state': state, 'message': UNCERTAIN if state == 'needs_review' else OFFLINE}
            db.execute('UPDATE operations SET state=?,result=? WHERE id=?', (state, json.dumps(result), row['id']))
            self.file(row['id']).unlink(missing_ok=True)

    def selected(self, db):
        row = db.execute("SELECT value FROM settings WHERE key='agent'").fetchone()
        return row[0] if row else None

    def status(self):
        with self.db() as db:
            self.expire(db)
            selected = self.selected(db)
            agents = []
            for row in db.execute('SELECT * FROM agents ORDER BY name'):
                online = time.time() - row['at'] < 45
                agents.append({'id': row['id'], 'name': row['name'], 'online': online,
                               'ready': online and bool(row['ready']), 'error': row['error'] if online else OFFLINE})
            online = [agent for agent in agents if agent['online']]
            if not selected and len(online) == 1 and not db.execute(
                    "SELECT 1 FROM operations WHERE kind IN ('upload','delete') AND state IN ('queued','running','needs_review')").fetchone():
                selected = online[0]['id']
                db.execute("INSERT OR REPLACE INTO settings VALUES ('agent',?)", (selected,))
            pending = [dict(id=r['id'], kind=r['kind'], state=r['state'], message=UNCERTAIN)
                       for r in db.execute("SELECT * FROM operations WHERE kind IN ('upload','delete') AND state IN ('queued','running','needs_review')")]
            return {'enabled': True, 'selected': selected, 'agents': agents, 'pending': pending}

    def pair(self, agent):
        agent = agent_identifier(agent)
        with self.db() as db:
            self.expire(db)
            if not db.execute('SELECT 1 FROM agents WHERE id=?', (agent,)).fetchone():
                raise ValueError('请先在执行主机启动客户端')
            if db.execute("SELECT 1 FROM operations WHERE state IN ('queued','running','needs_review') AND kind IN ('upload','delete')").fetchone():
                raise ValueError('已有打印操作待核对，暂不能更换执行主机')
            db.execute("INSERT OR REPLACE INTO settings VALUES ('agent',?)", (agent,))
        return {'message': '已选择执行主机'}

    def enqueue(self, kind, payload=None, token=None, target=None, content=None):
        if kind not in {'overview', 'history', 'upload', 'delete'}:
            raise ValueError('打印操作无效')
        payload = payload or {}
        jid = identifier(token) if token is not None else str(uuid.uuid4())
        target = target or kind + ':' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            self.expire(db)
            existing = db.execute('SELECT * FROM operations WHERE id=?', (jid,)).fetchone()
            if existing:
                if existing['kind'] != kind or existing['target'] != target:
                    raise ValueError('操作标识已用于其他请求')
                return self.response(existing)
            existing = db.execute("SELECT * FROM operations WHERE target=? AND state IN ('queued','running','needs_review')", (target,)).fetchone()
            if existing:
                if kind in {'upload', 'delete'} and existing['state'] == 'needs_review':
                    raise ValueError(UNCERTAIN)
                return self.response(existing)
            owner = self.selected(db)
            agent = db.execute('SELECT * FROM agents WHERE id=?', (owner,)).fetchone()
            if not owner:
                raise ValueError('请先选择一台属于本实例的执行主机')
            if not agent or time.time() - agent['at'] >= 45 or not agent['ready']:
                raise ValueError((agent['error'] if agent and time.time()-agent['at'] < 45 else '') or OFFLINE)
            if kind in {'upload', 'delete'} and db.execute("SELECT 1 FROM operations WHERE kind IN ('upload','delete') AND state IN ('queued','running')").fetchone():
                raise ValueError('另一项打印操作正在处理，请等待回执')
            if content is not None:
                if kind != 'upload' or not content or len(content) > 50*1024*1024:
                    raise ValueError('打印文件无效或超过 50 MB')
                payload = dict(payload, size=len(content), sha256=hashlib.sha256(content).hexdigest())
                with self.file(jid).open('xb') as handle:
                    handle.write(content)
            try:
                db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,NULL)',
                           (jid, kind, target, json.dumps(payload), owner, 'queued', time.time(), time.time()))
            except Exception:
                self.file(jid).unlink(missing_ok=True)
                raise
            return {'state': 'queued', 'operation_id': jid, 'message': '等待执行主机处理…'}

    @staticmethod
    def response(row):
        result = json.loads(row['result']) if row['result'] else {'state': row['state'], 'message': '执行主机正在处理…'}
        return dict(result, operation_id=row['id'])

    def operation(self, jid):
        with self.db() as db:
            self.expire(db)
            row = db.execute('SELECT * FROM operations WHERE id=?', (identifier(jid),)).fetchone()
            if not row:
                raise ValueError('打印操作不存在')
            return self.response(row)

    def poll(self, payload):
        agent = agent_identifier(payload.get('agent_id'))
        with self.db() as db:
            self.expire(db)
            db.execute('INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?)',
                       (agent, str(payload.get('name') or '本机客户端')[:80], time.time(),
                        payload.get('ready') is True, str(payload.get('error') or '')[:300]))
            result = payload.get('receipt')
            if result:
                row = db.execute('SELECT * FROM operations WHERE id=?', (identifier(result.get('id')),)).fetchone()
                if not row or row['owner'] != agent or row['state'] == 'queued':
                    raise ValueError('打印回执不属于该执行主机')
                if row['state'] in {'running', 'needs_review'}:
                    value = result.get('result')
                    if not isinstance(value, dict) or value.get('state') not in {'confirmed', 'rejected', 'needs_review'}:
                        raise ValueError('打印回执格式无效')
                    if len(json.dumps(value).encode()) > 2*1024*1024:
                        raise ValueError('打印回执过大')
                    db.execute('UPDATE operations SET state=?,result=?,touched=? WHERE id=?',
                               (value['state'], json.dumps(value), time.time(), row['id']))
                    self.file(row['id']).unlink(missing_ok=True)
            active = payload.get('active')
            if active:
                db.execute("UPDATE operations SET touched=? WHERE id=? AND owner=? AND state='running'", (time.time(), identifier(active), agent))
            if agent != self.selected(db) or not payload.get('claim'):
                return {'job': None}
            # Return an interrupted assigned write only for local-ledger recovery, never replay.
            row = db.execute("SELECT * FROM operations WHERE owner=? AND state='running' ORDER BY at LIMIT 1", (agent,)).fetchone()
            if row is None and payload.get('ready') is True:
                row = db.execute("SELECT * FROM operations WHERE owner=? AND state='queued' ORDER BY at LIMIT 1", (agent,)).fetchone()
                if row:
                    db.execute("UPDATE operations SET state='running',touched=? WHERE id=?", (time.time(), row['id']))
            if not row:
                return {'job': None}
            return {'job': {'id': row['id'], 'kind': row['kind'], 'target': row['target'],
                            'payload': json.loads(row['payload']), 'recovery_only': row['state'] != 'queued'}}

    def document(self, jid, agent):
        with self.db() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (identifier(jid),)).fetchone()
            if not row or row['owner'] != agent_identifier(agent) or row['kind'] != 'upload' or row['state'] != 'running':
                raise ValueError('打印文件不可访问')
        return self.file(jid)
