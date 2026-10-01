"""Owner-only, read-only live acceptance. Never print account data or cookies."""
import json
from sustech_dashboard.authentication import load_owner_credentials
from sustech_dashboard.provider import Blackboard, read_tis, read_bookings

assert load_owner_credentials()
result = {}
for name, fn in (("blackboard", lambda: {"course_count": len(Blackboard().current_courses())}),
                 ("tis", lambda: {"query_completed": bool(read_tis())}),
                 ("bookings", lambda: {"query_completed": bool(read_bookings())})):
    try:
        result[name] = fn()
    except Exception as exc:
        result[name] = {"error": type(exc).__name__}
    print(json.dumps({name: result[name]}), flush=True)
assert all("error" not in value for value in result.values())
