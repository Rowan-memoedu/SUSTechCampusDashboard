"""Restricted online TUF renewal, persistent hourly retries and SMTP alerts.

Only snapshot and timestamp private keys are accepted. Package authorization
and root trust cannot be extended or changed by this process.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import smtplib
import ssl
import tempfile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Snapshot, Timestamp, MetaFile
from tuf.ngclient import Updater
from tuf.ngclient.config import UpdaterConfig

from feed_metadata import PLATFORMS, verified_feed, reject_rollback
from publish_feed import publish

FEED = "https://124.221.144.155/campus-updates"


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat()


def parse(value):
    return datetime.fromisoformat(value)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


def load_online_keys(path, bootstrap):
    payload = json.loads(path.read_text())
    if set(payload) != {"snapshot", "timestamp"}:
        raise ValueError("Server credential must contain only snapshot and timestamp keys")
    root = Metadata.from_bytes(bootstrap)
    signers = {}
    for role, pem in payload.items():
        signer = CryptoSigner(load_pem_private_key(pem.encode(), password=None))
        if signer.public_key.keyid not in root.signed.roles[role].keyids:
            raise ValueError("Online key is not authorized by the pinned trust root")
        # Online authority must not overlap offline root or package authority.
        if any(signer.public_key.keyid in root.signed.roles[r].keyids for r in ("root", "targets")):
            raise ValueError("Online key overlaps offline signing authority")
        signers[role] = signer
    return signers


def send_mail(config, subject, body):
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = config["recipient"]
    message["Subject"] = "[校园面板续签] " + subject
    message.set_content(body)
    with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30,
                          context=ssl.create_default_context()) as smtp:
        smtp.login(config["username"], config["password"])
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError("SMTP refused the alert recipient")


def client_check(bootstrap, cache, *, fetcher=None, feed=FEED, expected=None):
    client = Updater(str(cache / "metadata"), feed + "/metadata/",
                     str(cache / "downloads"), feed + "/targets/", bootstrap=bootstrap,
                     fetcher=fetcher, config=UpdaterConfig(max_root_rotations=32))
    client.refresh()
    if expected and hashlib.sha256((cache / "metadata" / "timestamp.json").read_bytes()).hexdigest() != expected:
        raise ValueError("Public timestamp differs from the committed feed")
    result = {}
    for name in sorted(PLATFORMS):
        target = client.get_targetinfo(name)
        if target is None:
            raise ValueError("Client cannot see a platform release")
        result[name] = target.unrecognized_fields["custom"]["version"]
    return result


class Renewal:
    def __init__(self, root, state, bootstrap, keys, mail, *, check=None, notify=None):
        self.root, self.state_dir, self.bootstrap = root, state, bootstrap
        self.keys, self.mail = keys, mail
        self.check = check or client_check
        self.notify = notify or send_mail
        self.state_path = state / "state.json"
        self.report = state / "status.json"

    def load(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"alerts": {}, "events": []}

    def queue(self, state, kind, subject, body, now):
        # Hourly transport retries do not generate a new message each hour.
        if any(e["kind"] == kind for e in state["events"]):
            return
        last = state["alerts"].get(kind)
        if last and now - parse(last) < timedelta(days=1):
            return
        state["events"].append({"kind": kind, "subject": subject, "body": body})

    def flush(self, state, now):
        for event in list(state["events"]):
            try:
                self.notify(self.mail, event["subject"], event["body"])
            except Exception as exc:
                # Credentials, recipients and server responses never enter logs.
                state["mail_error"] = type(exc).__name__
                break
            state["events"].remove(event)
            state["alerts"][event["kind"]] = stamp(now)
            state.pop("mail_error", None)

    def deadlines(self, state, roles, now):
        root = Metadata.from_bytes(self.bootstrap)
        for role, expiry in (("root", root.signed.expires), ("targets", roles["targets"].signed.expires)):
            if expiry <= now + timedelta(days=90):
                self.queue(state, "deadline-" + role, "离线元数据即将到期",
                           f"{role} 到期时间：{stamp(expiry)}。请在发布者本机重新签发/轮换，服务器没有该签名权限。", now)

    def remember(self, blobs):
        mirror = self.state_dir / "metadata"
        mirror.mkdir(parents=True, exist_ok=True)
        for name, blob in sorted(blobs.items(), key=lambda item: item[0] == "timestamp.json"):
            temporary = mirror / (name + ".new")
            temporary.write_bytes(blob)
            temporary.replace(mirror / name)

    def renew(self, blobs, roles, now):
        expiry = min(now + timedelta(days=30), roles["targets"].signed.expires,
                     Metadata.from_bytes(self.bootstrap).signed.expires)
        if expiry <= now:
            raise ValueError("Offline package authorization expired; maintainer action required")
        signers = load_online_keys(self.keys, self.bootstrap)
        # Interrupted staging may leave an immutable numbered snapshot behind.
        existing = [int(p.name.split(".")[0]) for p in (self.root / "metadata").glob("*.snapshot.json")
                    if p.name.split(".")[0].isdigit()]
        snapshot_version = max([roles["snapshot"].signed.version, *existing]) + 1
        snapshot = Metadata(Snapshot(version=snapshot_version, expires=expiry, meta={
            "targets.json": MetaFile.from_data(roles["targets"].signed.version,
                                               blobs["targets.json"], ["sha256"])}))
        snapshot.sign(signers["snapshot"])
        snapshot_bytes = snapshot.to_bytes()
        timestamp = Metadata(Timestamp(version=roles["timestamp"].signed.version + 1,
            expires=expiry, snapshot_meta=MetaFile.from_data(snapshot_version, snapshot_bytes, ["sha256"])))
        timestamp.sign(signers["timestamp"])
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix="stage-") as folder:
            stage = Path(folder)
            for name, blob in blobs.items():
                (stage / name).write_bytes(blob)
            (stage / "snapshot.json").write_bytes(snapshot_bytes)
            (stage / f"{snapshot_version}.snapshot.json").write_bytes(snapshot_bytes)
            (stage / "timestamp.json").write_bytes(timestamp.to_bytes())
            publish(stage, self.root, expected_timestamp=hashlib.sha256(blobs["timestamp.json"]).hexdigest(),
                    renew_only=True, bootstrap=self.bootstrap)
        return hashlib.sha256(timestamp.to_bytes()).hexdigest()

    def run(self, *, now=None, force=False, test_alert=False):
        now = now or utcnow()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = self.load()
        result = {"checked_at": stamp(now), "status": "error", "client_verified": False}
        try:
            blobs, roles = verified_feed(lambda n: (self.root / "metadata" / n).read_bytes(), self.bootstrap, now=now)
            state["online_expires"] = stamp(min(roles[r].signed.expires for r in ("snapshot", "timestamp")))
            self.deadlines(state, roles, now)
            if roles["targets"].signed.is_expired(now):
                raise ValueError("Offline package authorization expired; maintainer action required")
            mirror = self.state_dir / "metadata"
            if (mirror / "timestamp.json").exists():
                _, previous = verified_feed(lambda n: (mirror / n).read_bytes(), self.bootstrap, now=now)
                reject_rollback(roles, previous)
            current_hash = hashlib.sha256(blobs["timestamp.json"]).hexdigest()
            pending = state.get("pending")
            recovering = pending and pending["hash"] == current_hash
            due = (force or (parse(state["next_renewal"]) <= now if state.get("next_renewal") else
                            roles["timestamp"].signed.expires <= now + timedelta(days=10))
                   or min(roles[r].signed.expires for r in ("snapshot", "timestamp")) <= now + timedelta(days=10))
            if due and not recovering:
                # Persist intent before publication; readback failure must not
                # trigger another signature on each hourly retry.
                state["pending"] = {"hash": "", "at": stamp(now)}
                save_json(self.state_path, state)
                committed = self.renew(blobs, roles, now)
                state["pending"]["hash"] = committed
                save_json(self.state_path, state)
                blobs, roles = verified_feed(lambda n: (self.root / "metadata" / n).read_bytes(), self.bootstrap, now=now)
            # Each hourly watchdog run verifies the public chain using the same
            # strict TUF client. It detects TLS/publication failure independently
            # of successful local signing; no user/campus data is involved.
            with tempfile.TemporaryDirectory(dir=self.state_dir, prefix="client-") as folder:
                releases = self.check(self.bootstrap, Path(folder),
                                      expected=hashlib.sha256(blobs["timestamp.json"]).hexdigest())
            self.remember(blobs)
            if state.get("pending"):
                state["next_renewal"] = stamp(parse(state.pop("pending")["at"]) + timedelta(days=20))
                state["last_renewal"] = stamp(now)
            elif not state.get("next_renewal"):
                state["next_renewal"] = stamp(roles["timestamp"].signed.expires - timedelta(days=10))
            if state.pop("failure_since", None):
                state.pop("failure_count", None)
                state["events"] = [e for e in state["events"] if e["kind"] != "failure"]
                self.queue(state, "recovery", "续签／更新源检查已恢复",
                           f"公网 TUF 验证成功。下一次续签：{state['next_renewal']}。", now)
            if test_alert:
                # Test messages use the production delivery and outbox path.
                state["alerts"].pop("test", None)
                self.queue(state, "test", "服务器告警通道验收",
                           "服务器后台续签已部署。本邮件用于验证南科大邮箱接收；不包含密钥或校园资料。", now)
            result.update(status="renewed" if due or recovering else "healthy", client_verified=True,
                          metadata_version=roles["timestamp"].signed.version,
                          targets_version=roles["targets"].signed.version,
                          expires_at=stamp(roles["timestamp"].signed.expires),
                          next_renewal=state["next_renewal"], releases=releases)
        except Exception as exc:
            state.setdefault("failure_since", stamp(now))
            state["failure_count"] = state.get("failure_count", 0) + 1
            self.queue(state, "failure", "续签／更新源检查失败",
                       f"错误类型：{type(exc).__name__}。首次失败：{state['failure_since']}。"
                       f"最近在线元数据到期时间：{state.get('online_expires', '未知')}。"
                       "服务器将每小时重试；请检查续签服务日志。已安装程序继续运行。", now)
            if state.get("online_expires") and parse(state["online_expires"]) <= now + timedelta(days=3):
                self.queue(state, "urgent", "更新清单将在三天内到期或已经过期",
                           f"到期时间：{state['online_expires']}。自动重试尚未恢复，请尽快检查服务器续签服务。", now)
            result.update(error=type(exc).__name__, detail=str(exc)[:300], failure_since=state["failure_since"])
        self.flush(state, now)
        result["pending_alerts"] = len(state["events"])
        if state.get("mail_error"):
            result["mail_error"] = state["mail_error"]
        save_json(self.state_path, state)
        save_json(self.report, result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/www/sustech-campus-updates"))
    parser.add_argument("--state", type=Path, default=Path("/var/lib/sustech-campus-renewal"))
    parser.add_argument("--trust", type=Path, default=Path("/etc/sustech-campus-renewal/trust-root.json"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--test-alert", action="store_true")
    args = parser.parse_args()
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    renewal = Renewal(args.root, args.state, args.trust.read_bytes(), credentials / "online-keys",
                      json.loads((credentials / "mail").read_text()))
    # The same lock serializes background renewal and all offline publications.
    with (args.root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = renewal.run(force=args.force, test_alert=args.test_alert)
    return 1 if result["status"] == "error" or result["pending_alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
