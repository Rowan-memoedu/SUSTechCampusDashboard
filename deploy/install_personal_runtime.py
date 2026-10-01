"""Migrate the existing owner's service to the verified shared Linux client."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

source = Path(sys.argv[1]).resolve()
assert source.name == "campus-client" and (source / "campus-client").is_file()
release = json.loads((source / "release.json").read_text())
assert release["platform"] == "linux-x86_64" and release["schema"] == 1
target = Path("/opt/sustech-campus-client")
data = Path("/var/lib/sustech-room-monitor/dashboard")
dropin = Path("/etc/systemd/system/sustech-campus-dashboard.service.d/client.conf")
backup = Path("/var/lib/sustech-room-monitor/upgrade-recovery") / release["version"]
backup.mkdir(parents=True, exist_ok=True)
os.chmod(backup.parent, 0o700)
os.chmod(backup, 0o700)

# Read-only readiness gate: never restart while a school write is active.
actions = data / "actions.sqlite3"
if actions.exists():
    with sqlite3.connect(f"file:{actions}?mode=ro", uri=True) as db:
        active = db.execute("SELECT COUNT(*) FROM actions WHERE state='sending'").fetchone()[0]
    if active:
        raise RuntimeError("School operation in progress or unresolved; inspect before deployment")

subprocess.run(["systemctl", "stop", "sustech-campus-dashboard"], check=True)
previous = dropin.read_text() if dropin.exists() else None
try:
    for path in data.iterdir():
        if path.is_file() and path.suffix in {".json", ".sqlite3"}:
            if path.suffix == ".sqlite3":
                with sqlite3.connect(path) as origin, sqlite3.connect(backup / path.name) as dest:
                    origin.backup(dest)
            else:
                shutil.copyfile(path, backup / path.name)
    if target.exists():
        raise RuntimeError("Initial client installation already exists; use signed updater")
    shutil.copytree(source, target, symlinks=False)
    for path in target.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.name == "campus-client" or path.stat().st_mode & 0o111:
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Service]\nExecStart=\nExecStart=/opt/sustech-campus-client/campus-client --no-browser --port 18771\nKillMode=control-group\nTimeoutStopSec=300\n")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "start", "sustech-campus-dashboard"], check=True)
    for _ in range(60):
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:18771/api/instance", headers={
                "Host": "124.221.144.155", "X-Forwarded-Proto": "https"})
            with urllib.request.urlopen(req, timeout=2) as response:
                state = json.load(response)
            if state["version"] == release["version"] and state["configured"]:
                print(json.dumps({"version": state["version"], "configured": True, "data_root": state["data_root"], "recovery": str(backup)}))
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("New runtime did not become ready")
except Exception:
    subprocess.run(["systemctl", "stop", "sustech-campus-dashboard"], check=False)
    if previous is None:
        dropin.unlink(missing_ok=True)
    else:
        dropin.write_text(previous)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "start", "sustech-campus-dashboard"], check=True)
    raise
