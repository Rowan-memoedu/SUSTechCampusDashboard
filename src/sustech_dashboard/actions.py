"""Durable one-shot user actions. Uncertain network writes are never replayed."""
import json
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from .core import DATA_ROOT

_sent=ContextVar('campus_action_sent',default=False)


def mark_sent():
    """After a write starts, even a parse error must be treated as uncertain."""
    _sent.set(True)


class Actions:
    def __init__(self, path=None):
        self.path = path or DATA_ROOT / 'actions.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, object TEXT, state TEXT, result TEXT, at REAL)')

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path, timeout=15) as db:
            yield db

    def run(self, token, target, work):
        if not isinstance(token,str) or len(token) != 36:
            raise ValueError('操作标识无效，请刷新页面')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state,result,object FROM actions WHERE id=?',(token,)).fetchone()
            if row:
                if row[2] != target: raise ValueError('操作标识已用于其他请求')
                return json.loads(row[1]) if row[1] else {'state':'needs_review','message':'请求已在处理，请查询官方记录，不能重复提交'}
            row=db.execute("SELECT state FROM actions WHERE object=? AND state IN ('sending','needs_review')",(target,)).fetchone()
            if row: raise ValueError('上次操作结果尚未确认，请先核对官方记录，不能重复提交')
            db.execute('INSERT INTO actions VALUES (?,?,?,NULL,?)',(token,target,'sending',time.time()))
        context_token=_sent.set(False)
        try:
            result=work()
        except ValueError as exc:
            result=({'state':'needs_review','message':'请求已发送，官方回执未能解析；请核对官方记录，暂不重复提交'}
                    if _sent.get() else {'state':'rejected','message':str(exc)})
        except Exception:
            result={'state':'needs_review','message':'网络或官方回执未能确认；请查看最新官方记录，避免重复提交'}
        finally:
            _sent.reset(context_token)
        with self.connect() as db:
            db.execute('UPDATE actions SET state=?,result=?,at=? WHERE id=?',(result['state'],json.dumps(result,ensure_ascii=False),time.time(),token))
        return result

    def confirm(self, target, result):
        with self.connect() as db:
            db.execute("UPDATE actions SET state='confirmed',result=? WHERE object=? AND state IN ('sending','needs_review')",
                       (json.dumps(result,ensure_ascii=False),target))

    def pending(self, target):
        with self.connect() as db:
            row=db.execute("SELECT state,result FROM actions WHERE object=? AND state IN ('sending','needs_review') ORDER BY at DESC LIMIT 1",(target,)).fetchone()
            return json.loads(row[1]) if row and row[1] else ({'state':'needs_review','message':'上次提交尚未取得确认，请核对 Blackboard'} if row else None)
