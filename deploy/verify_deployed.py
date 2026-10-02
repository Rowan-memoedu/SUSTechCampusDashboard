"""Read public HTTPS, authenticated live sync and readonly feature panels."""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from sustech_dashboard.download_agent import CONFIG
from sustech_dashboard.dpapi_store import unprotect_password

config = json.loads(CONFIG.read_text(encoding="utf-8"))
base = config["url"].rstrip("/")
anonymous = requests.get(base + "/", timeout=15, allow_redirects=False)
assert anonymous.status_code == 302 and anonymous.headers["Location"].endswith("/campus/auth/login")
assert requests.get(base + "/api/status", timeout=15).status_code == 401
session = requests.Session()
session.auth = (config["username"], unprotect_password(config["password_dpapi"]))
page = session.get(base + "/", timeout=15)
assert page.status_code == 200 and 'const apiBase="/campus"' in page.text
status = session.get(base + "/api/instance", timeout=15)
status.raise_for_status()
instance = status.json()
assert instance["configured"] and instance["managed"] and instance["mode"] == "个人服务器"
print(json.dumps({"version": instance["version"], "configured": instance["configured"],
    "unauthenticated": 302, "unauthenticated_api": 401, "authenticated": 200, "download_mode": instance["download_mode"],
    "update_state": instance["update"]["state"]}), flush=True)
for path in ("/settings", "/grades", "/printing", "/selection", "/venues/classroom/free", "/static/instance.js"):
    assert session.get(base + path, timeout=20).status_code == 200, path
assert session.get(base + "/api/room-watch", timeout=15).status_code == 410
deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    response = session.get(base + "/api/status", timeout=20)
    response.raise_for_status()
    value = response.json()
    times = value.get("source_updated_at", {})
    recent = all((datetime.now(timezone.utc) - datetime.fromisoformat(times.get(key, "2000-01-01T00:00:00+00:00"))).total_seconds() < 600
                 for key in ("blackboard", "tis", "bookings", "attachments"))
    if recent and not value.get("errors"):
        break
    time.sleep(5)
else:
    raise RuntimeError("New runtime sync not verified: " + json.dumps(value.get("errors")))
materials = session.get(base + "/api/materials", timeout=20).json()
print(json.dumps({"fresh_sync": True, "courses": len(value.get("blackboard_courses", [])),
    "assignment_count": len(value.get("assignments", [])), "sync_errors": value.get("errors"),
    "source_updated_at": times, "attachments": len(materials.get("items", [])),
    "baseline_ready": materials.get("baseline_ready"), "download_agent_online": materials.get("agent", {}).get("online"),
    "monitor_retired": True}), flush=True)
