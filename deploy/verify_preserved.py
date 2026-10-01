"""Compare migrated owner's ledger/baseline with the stopped-service snapshot."""
import json
import sqlite3
from pathlib import Path

root = Path("/var/lib/sustech-room-monitor")
result = {}
for label, directory in (("before", root / "upgrade-recovery/0.2.1"), ("after", root / "dashboard")):
    with sqlite3.connect(f"file:{directory / 'materials.sqlite3'}?mode=ro", uri=True) as db:
        seen = {r[0] for r in db.execute("SELECT key FROM seen")}
        jobs = {r[0] for r in db.execute("SELECT id FROM jobs")}
    with sqlite3.connect(f"file:{directory / 'actions.sqlite3'}?mode=ro", uri=True) as db:
        actions = {r[0] for r in db.execute("SELECT id FROM actions")}
    result[label] = {"seen": seen, "jobs": jobs, "actions": actions}
assert all(result["before"][key] <= result["after"][key] for key in ("seen", "jobs", "actions"))
print(json.dumps({label: {key: len(value) for key, value in items.items()} for label, items in result.items()}))
