from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from sustech_dashboard import room_monitor as mon


TZ = timezone(timedelta(hours=8))
START = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
END = datetime(2026, 10, 1, 12, 0, tzinfo=TZ)
ROOM = {"id": 9, "name": "G301", "location": "琳恩", "min_people": 1,
        "max_people": 3, "open": [{"openStartTime": "08:00", "openEndTime": "21:59"}], "busy": []}


class Client:
    def __init__(self, busy=False, fail=False):
        self.calls = 0
        self.busy = busy
        self.fail = fail

    def _call(self, method, path, params):
        if params["kindIds"] == 1:
            return []
        return [{"devId": 9, "devName": "G301", "labName": "琳恩", "minUser": 1,
                 "maxUser": 3, "openTimes": ROOM["open"],
                 "resvInfo": [{"startTime": int(START.timestamp()*1000),
                               "endTime": int(END.timestamp()*1000)}] if self.busy else []}]

    def whoami(self):
        return SimpleNamespace(acc_no=123)

    def add_reservation(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise TimeoutError("unknown outcome")
        return {"resvId": 42}


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(mon, "now", lambda: datetime(2026, 9, 30, 9, tzinfo=TZ))


def target():
    return {"room_id": 9, "begin": START.isoformat(), "end": END.isoformat(),
            "title": "课程讨论", "members": []}


def test_exact_overlap_and_policy():
    room = dict(ROOM, busy=[{"start": START.isoformat(), "end": END.isoformat()}])
    assert not mon.is_free(room, START, END)
    assert mon.is_free(room, END, END + timedelta(hours=1))
    assert mon.validate_target(target(), ROOM)["room_id"] == 9
    with pytest.raises(ValueError):
        mon.validate_target(dict(target(), members=[]), dict(ROOM, min_people=3))


def test_success_commits_once(tmp_path):
    path = tmp_path / "watch.json"
    mon.create_watch(path, dict(target(), room_name="G301"))
    client = Client()
    assert mon.check_once(path, client)["status"] == "booked"
    assert mon.check_once(path, client)["status"] == "booked"
    assert client.calls == 1
    assert mon.public_state(mon.read_state(path)).get("members") is None
    with pytest.raises(ValueError):
        mon.create_watch(path, dict(target(), room_name="G301"))


def test_busy_then_uncertain_result_never_retries(tmp_path):
    path = tmp_path / "watch.json"
    mon.create_watch(path, dict(target(), room_name="G301"))
    client = Client(busy=True)
    assert mon.check_once(path, client)["status"] == "watching"
    client.busy = False
    client.fail = True
    assert mon.check_once(path, client)["status"] == "needs_review"
    mon.check_once(path, client)
    assert client.calls == 1
    with pytest.raises(ValueError):
        mon.create_watch(path, dict(target(), room_name="G301"))


def test_local_booking_endpoint_rejects_cross_origin():
    from sustech_dashboard.app import create_app

    client = create_app().test_client()
    response = client.post("/api/venues/book", json=target(), headers={"Origin": "https://example.org"})
    assert response.status_code == 400
    assert "来源" in response.json["error"]
