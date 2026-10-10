"""Stdlib-only OnFailure notifier, independent of TUF and its virtualenv.

Normal handled errors already queued mail and are skipped. Startup failures
share the persisted outbox/failure marker, so recovery is sent by the normal
watchdog and SMTP failure is retried on the next failed hourly invocation.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
import os
from pathlib import Path
import smtplib
import ssl


def alert(state_dir, mail, *, now=None, send=None):
    now = now or datetime.now(timezone.utc)
    status_path = state_dir / "status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text())
            checked = datetime.fromisoformat(status["checked_at"])
            if status["status"] == "error" and abs(now - checked) < timedelta(minutes=10):
                return {"already_handled": True}
        except (ValueError, KeyError):
            pass
    state_path = state_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"alerts": {}, "events": []}
    state.setdefault("failure_since", now.isoformat())
    last = state["alerts"].get("failure")
    if not any(e["kind"] == "failure" for e in state["events"]) and (
            not last or now - datetime.fromisoformat(last) >= timedelta(days=1)):
        state["events"].append({"kind": "failure", "subject": "后台续签任务启动失败",
            "body": "续签主服务未能正常启动。请检查 systemd 单元和日志；服务器每小时重试，已安装程序继续运行。"})

    def send_message(config, subject, body):
        message = EmailMessage()
        message["From"], message["To"] = config["sender"], config["recipient"]
        message["Subject"] = "[校园面板续签] " + subject
        message.set_content(body)
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(config["username"], config["password"])
            if smtp.send_message(message):
                raise RuntimeError("SMTP refused the alert recipient")

    failure = None
    for event in list(state["events"]):
        try:
            (send or send_message)(mail, event["subject"], event["body"])
        except Exception as exc:
            failure = type(exc).__name__
            state["mail_error"] = failure
            break
        state["events"].remove(event)
        state["alerts"][event["kind"]] = now.isoformat()
        state.pop("mail_error", None)
    temporary = state_path.with_suffix(".new")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(state_path)
    result = {"startup_failure": True, "pending_alerts": len(state["events"]), "mail_error": failure}
    (state_dir / "startup-alert-status.json").write_text(json.dumps(result))
    return result


if __name__ == "__main__":
    import fcntl
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    with Path("/var/www/sustech-campus-updates/.publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(alert(Path("/var/lib/sustech-campus-renewal"),
                               json.loads((credentials / "mail").read_text()))))
