"""Private, restartable homework ledger. Never writes to Blackboard."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .homework_recovery import retry_delay, elapsed_since


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class HomeworkStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'homework.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen (target TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS courses (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    trusted INTEGER NOT NULL DEFAULT 0, recipe TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, target TEXT NOT NULL UNIQUE,
                    course_id TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL,
                    stage TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, updated TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def enable(self, enabled, snapshot):
        if type(enabled) is not bool:
            raise ValueError('启用状态无效')
        if enabled and not self.setting('baseline_at'):
            if not snapshot.get('source_updated_at', {}).get('blackboard') or snapshot.get('errors', {}).get('blackboard'):
                raise ValueError('请先成功同步 Blackboard，再启用作业制册')
            with self.db() as db:
                db.executemany('INSERT OR IGNORE INTO seen VALUES (?)',
                    [(self.target(x),) for x in snapshot.get('assignments', [])])
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('baseline_at', json.dumps(now())))
        self.set_setting('enabled', enabled)

    @staticmethod
    def target(assignment):
        return assignment['course_id'] + '/' + assignment['content_id']

    def enqueue(self, assignment, explicit=False):
        target = self.target(assignment)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE target=?', (target,)).fetchone()
            if old:
                job = self.decode(old)
                if explicit and job['state'] == 'baseline':
                    data = {'assignment': assignment, 'feedback': [], 'artifacts': [],
                            'attempt': job['data'].get('attempt', 0) + 1}
                    db.execute("UPDATE jobs SET state='queued',stage='discover',data=?,error='',revision=revision+1,updated=? WHERE id=?",
                        (json.dumps(data, ensure_ascii=False), now(), job['id']))
                    return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
                return job
            seen = db.execute('SELECT 1 FROM seen WHERE target=?', (target,)).fetchone()
            if seen and not explicit:
                return None
            db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (target,))
            jid = uuid.uuid4().hex
            ts = now()
            data = {'assignment': assignment, 'feedback': [], 'artifacts': []}
            db.execute('INSERT OR IGNORE INTO courses (id,name) VALUES (?,?)',
                       (assignment['course_id'], assignment['course']))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,1,?,?,?,?)',
                       (jid, target, assignment['course_id'], assignment['name'], 'queued', 'discover',
                        json.dumps(data, ensure_ascii=False), '', ts, ts))
        return self.get(jid)

    def ingest(self, snapshot):
        if not self.setting('enabled', False) or not self.setting('baseline_at'):
            return
        if not snapshot.get('source_updated_at', {}).get('blackboard') or snapshot.get('errors', {}).get('blackboard'):
            return
        for item in snapshot.get('assignments', []):
            self.enqueue(item)

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError('作业处理记录不存在')
        result = dict(row)
        result['data'] = json.loads(result['data'])
        return result

    def get(self, jid):
        with self.db() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())

    def list(self):
        with self.db() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC')]

    def course(self, cid):
        with self.db() as db:
            row = db.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone()
        if not row:
            return {'trusted': False, 'recipe': {}}
        return {**dict(row), 'recipe': json.loads(row['recipe'])}

    def update(self, jid, *, state=None, stage=None, data=None, error=''):
        old = self.get(jid)
        with self.db() as db:
            db.execute('UPDATE jobs SET state=?,stage=?,data=?,error=?,revision=revision+1,updated=? WHERE id=?',
                       (state or old['state'], stage or old['stage'],
                        json.dumps(data if data is not None else old['data'], ensure_ascii=False), error, now(), jid))
        return self.get(jid)

    def claim(self):
        adapter = self.unblock_configured()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Oldest-ready first, not oldest-created: a repeatedly failing job
            # cannot monopolize the worker. Delayed retries survive restarts.
            row = next((r for r in db.execute(
                "SELECT * FROM jobs WHERE state IN ('queued','retry_wait') ORDER BY updated,created")
                if not self.unblock_held(json.loads(r['data']), adapter)
                and (r['state'] == 'queued' or
                json.loads(r['data']).get('recovery', {}).get('next_retry_at', '') <= now())), None)
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running',revision=revision+1,updated=? WHERE id=?", (now(), row['id']))
        return self.get(row['id'])

    def recover(self):
        adapter = self.unblock_configured()
        with self.db() as db:
            db.execute("UPDATE jobs SET state='queued',error='',updated=? WHERE state='running'", (now(),))
            if not adapter:
                # A short-lived older build requested repairs unconditionally.
                # Unconfigured machines keep their original retry policy. Never
                # release a repair already owned by a real execution thread.
                for row in db.execute("SELECT id,data FROM jobs WHERE state='retry_wait'").fetchall():
                    data = json.loads(row['data'])
                    unblock = data.get('recovery', {}).get('unblock', {})
                    if unblock.get('state') == 'requested' and not unblock.get('owner'):
                        data['recovery']['unblock'] = {**unblock, 'state': 'unavailable', 'reason': 'no configured adapter'}
                        db.execute('UPDATE jobs SET data=?,revision=revision+1 WHERE id=?',
                            (json.dumps(data, ensure_ascii=False), row['id']))

    def unblock_configured(self):
        config = self.setting('unblock_adapter', {})
        return (isinstance(config, dict) and config.get('enabled') is True
                and config.get('provider') == 'decision-workbench')

    @staticmethod
    def unblock_held(data, adapter):
        hold = data.get('recovery', {}).get('unblock', {})
        return hold.get('state') == 'active' or (hold.get('state') == 'requested' and (adapter or bool(hold.get('owner'))))

    def schedule_recovery(self, jid, error, kind, phase, *, legacy=False):
        job = self.get(jid)
        data = job['data']
        if kind == 'blocked':
            if data.get('recovery'):
                data['recovery'].update(kind='blocked', phase=phase)
                if data['recovery'].get('unblock'):
                    data['recovery']['unblock'] = {**data['recovery']['unblock'], 'state': 'blocked'}
            return self.update(jid, state='needs_attention', error=error, data=data)
        if kind == 'regenerate' and job['stage'] == 'extract':
            phase = 'extract'
        old = data.get('recovery', {})
        streak = old.get('streak', 0) + 1 if old.get('phase') == phase else 1
        delay = retry_delay(streak)
        if legacy:
            delay = max(0, delay - elapsed_since(job['updated']))
        next_retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        history = old.get('history', []) + [{'at': now(), 'phase': phase, 'kind': kind, 'error': error}]
        if old.get('unblock'):
            data['unblock_history'] = (data.get('unblock_history', []) + [old['unblock']])[-20:]
        data['recovery'] = {'attempts': old.get('attempts', 0) + 1, 'streak': streak,
            'phase': phase, 'kind': kind, 'next_retry_at': next_retry, 'history': history[-20:]}
        if self.unblock_configured():
            data['recovery']['unblock'] = {'state': 'requested', 'at': now(), 'phase': phase}
        if kind == 'regenerate':
            data.setdefault('feedback', []).append({'stage': phase, 'text': error, 'at': now()})
            if phase == 'extract':
                data['retry_extraction'] = True
                stage = 'extract'
            else:
                data['active_phase'] = phase
                data['retry_production'] = True
                data['repair_phase_explicit'] = True
                stage = job['stage']
        else:
            stage = job['stage']
        return self.update(jid, state='retry_wait', stage=stage, data=data, error=error)

    def unblock(self, jid, revision, owner, *, receipt=None):
        """Bind or resume one repair; no review/confirmation checkpoint is granted.

        The caller must drain the worker before production or tool migration.
        A receipt names the actual validated workflow revision and local evidence.
        """
        if not owner or not isinstance(owner, str):
            raise ValueError('疏通归属不能为空')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
            if job['revision'] != revision or job['state'] not in {'retry_wait', 'failed', 'needs_attention'}:
                raise ValueError('处理状态已变化，请刷新后核对')
            data = job['data']
            recovery = data.setdefault('recovery', {})
            if recovery.get('kind') not in {'retry', 'regenerate'}:
                raise ValueError('此状态需要原有人工核对，不能通过流程疏通恢复')
            old = recovery.get('unblock', {})
            if old.get('owner') and old['owner'] != owner:
                raise ValueError('作业已有其他疏通归属')
            if receipt is None:
                recovery['unblock'] = {'state': 'active', 'owner': owner, 'at': now()}
                state = 'retry_wait'
            else:
                if old.get('state') != 'active' or old.get('owner') != owner:
                    raise ValueError('须先接管本轮疏通')
                evidence = Path(receipt['path'])
                if not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != receipt['sha256']:
                    raise ValueError('疏通验证回执不存在或已变化')
                recovery['unblock'] = {**old, 'state': 'resumed', 'resumed_at': now(), 'receipt': receipt}
                state = 'queued'
            db.execute('UPDATE jobs SET state=?,data=?,revision=revision+1,updated=? WHERE id=?',
                (state, json.dumps(data, ensure_ascii=False), now(), jid))
        return self.get(jid)

    def decide(self, jid, revision, action, feedback='', homework_copies=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
            if revision != job['revision']:
                raise ValueError('处理状态已变化，请刷新后核对')
            data = job['data']
            if data.get('recovery', {}).get('unblock', {}).get('state') in {'requested', 'active'}:
                raise ValueError('本作业正在自动疏通，已有检查点保留，请等待恢复')
            state, stage = job['state'], job['stage']
            if action == 'accept_source' and state == 'awaiting_source':
                data['source_approved'] = data['source_digest']
                data['homework_copies'] = homework_copies
                state, stage = 'queued', 'produce'
            elif action == 'accept_pdf' and state == 'awaiting_pdf':
                if not data.get('artifacts'):
                    raise ValueError('当前没有可确认的 PDF')
                state, stage = 'complete', 'done'
                recipe = {**data.get('plan', {}), 'homework_copies': data.get('homework_copies', False)}
                db.execute('UPDATE courses SET trusted=1,recipe=? WHERE id=?',
                           (json.dumps(recipe, ensure_ascii=False), job['course_id']))
            elif action in {'correct_source', 'reject_pdf'} and state in {'awaiting_source', 'awaiting_pdf', 'needs_attention', 'failed'}:
                feedback = str(feedback).strip()
                if not feedback:
                    raise ValueError('请填写教材、题号或 PDF 需要修正的地方')
                data['feedback'].append({'stage': stage, 'text': feedback[:5000], 'at': now()})
                data.pop('source_approved', None)
                data['force_confirm'] = True
                data['attempt'] = data.get('attempt', 0) + 1
                data.pop('pipeline', None)
                # A fresh candidate preserves the rejected PDFs and original responses.
                data['artifacts'] = []
                state, stage = 'queued', 'discover' if action == 'correct_source' else ('extract' if data.get('downloads') else 'discover')
            elif action == 'retry' and state in {'failed', 'needs_attention'}:
                if stage in {'extract', 'produce'}:
                    data['retry_extraction' if stage == 'extract' else 'retry_production'] = True
                    data.setdefault('feedback', []).append({'stage': data.get('active_phase', stage) if stage == 'produce' else stage,
                        'text': job['error'][:5000], 'at': now()})
                state = 'queued'
            else:
                raise ValueError('当前状态不支持此操作')
            db.execute('UPDATE jobs SET state=?,stage=?,data=?,error=?,revision=revision+1,updated=? WHERE id=?',
                       (state, stage, json.dumps(data, ensure_ascii=False), '', now(), jid))
        return self.get(jid)
