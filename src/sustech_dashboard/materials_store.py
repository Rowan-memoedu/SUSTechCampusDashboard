"""Durable material requests and verified local download receipts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .core import CHINA_TZ, attachment_key


def stamp():
    return datetime.now(CHINA_TZ).isoformat()


class MaterialsStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, signature TEXT, label TEXT, source TEXT, keys TEXT,
                    state TEXT, created TEXT, updated TEXT, owner TEXT, lease REAL,
                    results TEXT NOT NULL DEFAULT '{}'
                );
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('auto', 'true')")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def setting(db, key, default=None):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def set_auto(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("自动下载设置无效")
        with self.connect(True) as db:
            self.put(db, "auto", enabled)
            if not enabled:
                db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE source='auto' AND state='queued'", (stamp(),))

    def _enqueue(self, db, keys, label, source):
        keys = sorted(set(keys))
        if not keys:
            raise ValueError("没有可下载附件")
        signature = hashlib.sha256("\n".join(keys).encode()).hexdigest()
        row = db.execute("SELECT id FROM jobs WHERE signature=? AND state IN ('queued','running')", (signature,)).fetchone()
        if row:
            return row[0]
        jid = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,signature,label,source,keys,state,created,updated) VALUES(?,?,?,?,?,'queued',?,?)",
                   (jid, signature, label, source, json.dumps(keys), stamp(), stamp()))
        return jid

    def enqueue(self, keys, label, source="manual"):
        with self.connect(True) as db:
            return self._enqueue(db, keys, label, source)

    def retry_keys(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT keys,results FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                raise ValueError("下载任务不存在")
            results = json.loads(row["results"])
            return [k for k in json.loads(row["keys"]) if results.get(k, {}).get("status") not in {"saved", "existing"}]

    def _auto_jobs(self, db, manifest):
        if not self.setting(db, "auto") or not self.setting(db, "baseline_ready", False):
            return
        from .execution import hosted
        if hosted() and time.time() - self.setting(db, 'heartbeat', {}).get('epoch', 0) >= 90:
            return  # Pure Web discovers files; only a paired device accepts file tasks.
        seen = {r[0] for r in db.execute("SELECT key FROM seen")}
        active = set()
        for row in db.execute("SELECT keys FROM jobs WHERE state IN ('queued','running')"):
            active.update(json.loads(row[0]))
        available = {attachment_key(i["course_id"], i["content_id"], i["id"]) for i in manifest.get("items", [])}
        keys = available - seen - active
        # Failed automatic downloads retry on the next scan, not on every heartbeat.
        last = self.setting(db, "last_auto_attempt", 0)
        if time.time() - last < 300:
            failed = set()
            for row in db.execute("SELECT results FROM jobs WHERE source='auto' AND state IN ('partial','failed')"):
                failed.update(k for k, r in json.loads(row[0]).items() if r["status"] == "failed")
            keys -= failed
        if keys:
            self._enqueue(db, keys, "自动下载新资料", "auto")
            self.put(db, "last_auto_attempt", time.time())

    def scan_finished(self, manifest):
        with self.connect(True) as db:
            from .execution import hosted
            if hosted() and manifest.get('updated_at') and not self.setting(db, 'baseline_ready', False):
                keys = [attachment_key(i['course_id'], i['content_id'], i['id']) for i in manifest.get('items', [])]
                db.executemany('INSERT OR IGNORE INTO seen VALUES (?)', ((key,) for key in keys))
                self.put(db, 'baseline_ready', True)
            self._auto_jobs(db, manifest)

    def agent_poll(self, payload, manifest):
        agent = payload.get("agent_id", "")
        if not isinstance(agent, str) or len(agent) != 32 or any(c not in "0123456789abcdef" for c in agent):
            raise ValueError("下载代理标识无效")
        known = {attachment_key(i["course_id"], i["content_id"], i["id"]) for i in manifest.get("items", [])}
        with self.connect(True) as db:
            self.put(db, "heartbeat", {"at": stamp(), "epoch": time.time(), "state": payload.get("state", "idle"),
                    "current_file": str(payload.get("current_file", ""))[:300], "last_error": str(payload.get("last_error", ""))[:120]})
            if "seen_keys" in payload:
                values = payload["seen_keys"]
                if not isinstance(values, list) or len(values) > 20000:
                    raise ValueError("附件基线无效")
                db.executemany("INSERT OR IGNORE INTO seen VALUES (?)", ((k,) for k in values if isinstance(k, str)))
                self.put(db, "baseline_ready", True)
            for receipt in payload.get("inventory", [])[:200]:
                if isinstance(receipt, dict) and receipt.get("key") in known:
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,?)", (receipt["key"], json.dumps(receipt, ensure_ascii=False)))
            update = payload.get("job_update")
            if update:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (update.get("id"),)).fetchone()
                if not row or row["owner"] != agent:
                    raise ValueError("下载任务已失效，请重新查询")
                # A lost HTTP response may cause the agent to repeat its final acknowledgement.
                # Preserve the first committed result and acknowledge the identical owner.
                if row["state"] in {"completed", "partial", "failed"}:
                    update = None
            if update:
                results = json.loads(row["results"])
                requested = set(json.loads(row["keys"]))
                for receipt in update.get("results", []):
                    key = receipt.get("key")
                    if key not in requested or receipt.get("status") not in {"saved", "existing", "failed"}:
                        raise ValueError("下载回执无效")
                    if receipt.get("status") != "failed" and (not receipt.get("sha256") or receipt.get("size", 0) <= 0):
                        raise ValueError("未核验的文件不能标为已下载")
                    results[key] = receipt
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,?)", (key, json.dumps(receipt, ensure_ascii=False)))
                    if receipt["status"] in {"saved", "existing"}:
                        db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (key,))
                state = "running"
                if update.get("finished"):
                    for key in requested - set(results):
                        results[key] = {"key": key, "status": "failed", "error": "下载任务未完成"}
                    successes = sum(r["status"] != "failed" for r in results.values())
                    state = "completed" if successes == len(requested) else "partial" if successes else "failed"
                db.execute("UPDATE jobs SET state=?,results=?,updated=?,lease=? WHERE id=?",
                           (state, json.dumps(results, ensure_ascii=False), stamp(), time.time()+90, row["id"]))
            # Reclaim work only after a missing heartbeat; local registry prevents duplicate files.
            db.execute("UPDATE jobs SET state='queued',owner=NULL WHERE state='running' AND lease<?", (time.time(),))
            self._auto_jobs(db, manifest)
            job = None
            if payload.get("claim", True):
                row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY source='auto',created LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET state='running',owner=?,lease=?,updated=? WHERE id=?",
                               (agent, time.time()+90, stamp(), row["id"]))
                    job = {"id": row["id"], "keys": json.loads(row["keys"]), "source": row["source"]}
            elif payload.get("active_job"):
                db.execute("UPDATE jobs SET lease=?,updated=? WHERE id=? AND owner=? AND state='running'",
                           (time.time()+90, stamp(), payload["active_job"], agent))
            return {"auto_enabled": self.setting(db, "auto"), "job": job,
                    "manifest_version": manifest.get("updated_at")}

    def view(self, manifest):
        with self.connect() as db:
            heartbeat = self.setting(db, "heartbeat", {})
            files = {r["key"]: json.loads(r["receipt"]) for r in db.execute("SELECT * FROM files")}
            jobs = []
            active = {}
            for row in db.execute("SELECT keys,results,state FROM jobs WHERE state IN ('queued','running')"):
                results = json.loads(row["results"])
                active.update({k: row["state"] for k in json.loads(row["keys"]) if k not in results})
            for row in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 20"):
                keys, results = json.loads(row["keys"]), json.loads(row["results"])
                jobs.append({"id": row["id"], "label": row["label"], "source": row["source"], "state": row["state"],
                    "total": len(keys), "done": sum(r["status"] in {"saved", "existing"} for r in results.values()),
                    "failed": sum(r["status"] == "failed" for r in results.values()), "updated_at": row["updated"],
                    "errors": [r.get("error", "下载失败") for r in results.values() if r["status"] == "failed"][:3]})
            items = []
            for item in manifest.get("items", []):
                key = attachment_key(item["course_id"], item["content_id"], item["id"])
                receipt = files.get(key, {})
                items.append(dict(item, key=key, local_status=active.get(key, receipt.get("status", "not_downloaded")),
                    local_path=receipt.get("path"), size=receipt.get("size", item.get("size")), error=receipt.get("error")))
            return {**manifest, "items": items, "auto_enabled": self.setting(db, "auto"), "jobs": jobs,
                    "agent": {**heartbeat, "online": time.time() - heartbeat.get("epoch", 0) < 90},
                    "destination": r"D:\download", "baseline_ready": self.setting(db, "baseline_ready", False)}
