"""Print only stage counts and exception classes, never upstream URLs."""
import time
from sustech_dashboard.monitor_cli import _client
from sustech_dashboard.provider import Blackboard, read_tis, read_bookings

_client()
for name, action in (
    ("courses", lambda: Blackboard().current_courses()),
    ("assignments", lambda: Blackboard().assignments()),
    ("tis", read_tis),
    ("bookings", read_bookings),
):
    start = time.monotonic()
    print("start", name, flush=True)
    try:
        data = action()
        counts = len(data) if isinstance(data, list) else {
            k: len(v) for k, v in data.items() if isinstance(v, list)
        }
        print("ok", name, counts, "seconds", round(time.monotonic()-start), flush=True)
    except Exception as exc:
        print("failed", name, type(exc).__name__, "seconds", round(time.monotonic()-start), flush=True)
