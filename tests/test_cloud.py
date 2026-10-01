from pathlib import Path
from sustech_dashboard import app as module


def test_cloud_prefix_and_csrf(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "CLOUD", True)
    monkeypatch.setattr(module, "SNAPSHOT_PATH", tmp_path / "snapshot.json")
    monkeypatch.setenv("SUSTECH_PUBLIC_HOST", "124.221.144.155")
    client = module.create_app().test_client()
    headers = {"Host": "124.221.144.155", "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/campus"}
    page = client.get("/", headers=headers)
    assert page.status_code == 200
    assert b'const apiBase="/campus"' in page.data
    assert client.post("/api/room-watch", headers=headers, json={}).status_code == 400
    assert client.post("/api/download-status", headers=headers, json={"mode": "incremental"}).status_code == 403


def test_failed_blackboard_sync_retains_last_success(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "SNAPSHOT_PATH", tmp_path / "snapshot.json")
    module.save_json(module.SNAPSHOT_PATH, {
        "blackboard_courses": [{"id": "c", "name": "Course"}],
        "assignments": [{"name": "Assignment"}],
        "source_updated_at": {"blackboard": "2026-09-30T12:00:00+08:00"},
    })
    class Broken:
        def __init__(self):
            raise TimeoutError("secret CAS ticket")
    monkeypatch.setattr(module, "Blackboard", Broken)
    monkeypatch.setattr(module, "read_tis", lambda: {})
    monkeypatch.setattr(module, "read_bookings", lambda: {})
    import sustech_survival
    monkeypatch.setattr(sustech_survival, "Context", lambda **kwargs: type("Weather", (), {
        "weather_cond": "", "temperature": None, "aqi_value": None})())
    result = module.sync_all()
    assert result["assignments"] == [{"name": "Assignment"}]
    assert result["source_updated_at"]["blackboard"] == "2026-09-30T12:00:00+08:00"
    assert "blackboard" in result["errors"]
    assert "secret" not in str(result)
