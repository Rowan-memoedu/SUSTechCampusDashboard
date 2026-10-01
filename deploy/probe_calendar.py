"""Read-only live holiday/makeup acceptance; print counts, never credentials."""
import json
from datetime import date

from sustech_dashboard.monitor_cli import _client
from sustech_dashboard.academic_calendar import read_daily_calendar
from sustech_survival.tis import schedule

_client()
semester = schedule.current_semester()
for iso in ("2026-10-01", "2026-10-07", "2026-10-08", "2026-10-10"):
    result = read_daily_calendar(semester, date.fromisoformat(iso))
    meta = result["calendar"]
    print(json.dumps({"date": iso, "kind": meta["kind"], "label": meta["label"],
                      "source_date": meta.get("source_date"), "source_week": meta.get("source_week"),
                      "class_count": len(result["today_classes"]), "cached_data": meta["cached_data"]},
                     ensure_ascii=False), flush=True)
    if iso in {"2026-10-01", "2026-10-07"}:
        assert not result["today_classes"] and meta["kind"] == "holiday"
    if iso == "2026-10-10":
        assert meta["kind"] == "makeup" and meta["source_date"] == "2026-10-07"
        assert meta["source_week"] % 2 == 1
