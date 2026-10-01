"""Public TLS/authentication and real data acceptance, sanitized output only."""
import json
from collections import Counter
from datetime import datetime
import requests
from sustech_dashboard.download_agent import CONFIG
from sustech_dashboard.dpapi_store import unprotect_password

cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
url = cfg["url"]
session = requests.Session()
unauthenticated = session.get(url + "/", timeout=15)
assert unauthenticated.status_code == 401
session.auth = (cfg["username"], unprotect_password(cfg["password_dpapi"]))
page = session.get(url + "/", timeout=15)
assert page.status_code == 200 and 'const apiBase="/campus"' in page.text
status = session.get(url + "/api/status", timeout=15)
status.raise_for_status()
data = status.json()
print(json.dumps({
    "https_certificate_valid": True, "unauthenticated_status": 401, "authenticated_page_status": 200,
    "courses": len(data.get("blackboard_courses", [])), "assignments": len(data.get("assignments", [])),
    "assignment_states": dict(Counter(a["status"] for a in data.get("assignments", []))),
    "errors": data.get("errors"), "warnings": data.get("warnings"),
    "source_updated_at": data.get("source_updated_at"),
    "tis_errors": data.get("tis", {}).get("errors"),
    "booking_counts": {k: len(v) for k,v in data.get("bookings", {}).items() if isinstance(v,list)},
    "downloads": data.get("downloads"),
}, ensure_ascii=False), flush=True)
rooms = session.get(url + "/api/discussion-rooms", params={"date": datetime.now().date().isoformat()}, timeout=60)
rooms.raise_for_status()
print("discussion_rooms", len(rooms.json()["rooms"]))
watch = session.get(url + "/api/room-watch", timeout=15)
watch.raise_for_status()
print("monitor_status", watch.json()["status"])
manifest = session.get(url + "/api/attachments", timeout=15)
print("attachment_manifest_status", manifest.status_code)
if manifest.ok:
    print("manifest_items", len(manifest.json()["items"]))
