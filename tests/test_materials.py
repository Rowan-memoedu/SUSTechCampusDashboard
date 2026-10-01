import hashlib
import json
import time

import pytest
from requests import Response
from requests.exceptions import HTTPError

from sustech_dashboard.core import attachment_key, save_json
from sustech_dashboard.materials_local import LocalMaterials, material_path
from sustech_dashboard.materials_store import MaterialsStore

AGENT = "a" * 32


def manifest(ids=("old", "new")):
    return {"updated_at": "now", "courses": [{"id": "c1", "name": "First"}, {"id": "c2", "name": "Second"}],
            "items": [{"course_id": "c1" if index == 0 else "c2", "course_name": "First" if index == 0 else "Second",
                       "content_id": "content", "id": iid, "file_name": "same.pdf", "title": "Lecture", "folders": ["Week"]}
                      for index, iid in enumerate(ids)]}


def key(item):
    return attachment_key(item["course_id"], item["content_id"], item["id"])


def test_auto_uses_original_baseline_and_receipts_not_scan_counts(tmp_path):
    store = MaterialsStore(tmp_path / "jobs.sqlite3")
    m = manifest()
    store.scan_finished(m)
    assert store.view(m)["jobs"] == []
    reply = store.agent_poll({"agent_id": AGENT, "seen_keys": [key(m["items"][0])]}, m)
    job = reply["job"]
    assert job["keys"] == [key(m["items"][1])]
    assert store.view(m)["items"][0]["local_status"] == "not_downloaded"
    store.agent_poll({"agent_id": AGENT, "claim": False, "job_update": {
        "id": job["id"], "results": [{"key": job["keys"][0], "status": "saved", "size": 5,
                                         "sha256": "c" * 64, "path": "Second/Week/file.pdf"}], "finished": True}}, m)
    assert store.view(m)["jobs"][0]["state"] == "completed"
    assert store.agent_poll({"agent_id": AGENT}, m)["job"] is None


def test_manual_requests_include_old_files_deduplicate_and_retry_only_failures(tmp_path):
    store = MaterialsStore(tmp_path / "jobs.sqlite3")
    store.set_auto(False)
    m = manifest()
    keys = [key(i) for i in m["items"]]
    jid = store.enqueue(keys, "all")
    assert store.enqueue(keys, "repeat") == jid
    job = store.agent_poll({"agent_id": AGENT}, m)["job"]
    assert set(job["keys"]) == set(keys)
    with pytest.raises(ValueError):
        store.agent_poll({"agent_id": AGENT, "claim": False, "job_update": {
            "id": jid, "results": [{"key": keys[0], "status": "saved"}]}}, m)
    store.agent_poll({"agent_id": AGENT, "claim": False, "job_update": {
        "id": jid, "results": [{"key": keys[0], "status": "saved", "size": 5, "sha256": "d"*64},
                                {"key": keys[1], "status": "failed", "error": "network"}], "finished": True}}, m)
    assert store.view(m)["jobs"][0]["state"] == "partial"
    assert store.retry_keys(jid) == [keys[1]]


def test_offline_jobs_survive_restart_and_expired_lease_is_reclaimed(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    m = manifest(("a",))
    store = MaterialsStore(path)
    store.set_auto(False)
    jid = store.enqueue([key(m["items"][0])], "file")
    store = MaterialsStore(path)
    assert not store.view(m)["agent"]["online"]
    assert store.agent_poll({"agent_id": AGENT}, m)["job"]["id"] == jid
    with store.connect(True) as db:
        db.execute("UPDATE jobs SET lease=?", (time.time()-1,))
    assert store.agent_poll({"agent_id": "b"*32}, m)["job"]["id"] == jid


def test_organized_downloads_hash_verify_deduplicate_and_preserve_user_edits(tmp_path):
    class Provider:
        calls = 0
        def download_attachment(self, course_id, content_id, attachment_id, path):
            self.calls += 1
            path.write_bytes(b"original PDF bytes")
    item = manifest()["items"][0]
    provider = Provider()
    registry = LocalMaterials(tmp_path / "download", tmp_path / "receipts.json")
    first = registry.save(provider, item)
    path = registry.root / first["path"]
    assert path.parent.parts[-2:] == ("First", "Week")
    assert first["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert registry.save(provider, item)["status"] == "existing"
    assert provider.calls == 1
    assert path.suffix=='.pdf'
    path.write_bytes(b"user edit")
    with pytest.raises(FileExistsError):
        registry.save(provider, item)
    assert path.read_bytes() == b"user edit"
    hostile = dict(item, course_name="../outside", folders=["../../escape"], file_name="../file.pdf")
    assert material_path(registry.root, hostile).resolve().is_relative_to(registry.root.resolve())


def test_duplicate_terminal_ack_is_accepted_without_changing_receipt(tmp_path):
    store=MaterialsStore(tmp_path/'jobs.sqlite3');store.set_auto(False);m=manifest(('a',))
    iid=key(m['items'][0]);store.enqueue([iid],'one');job=store.agent_poll({'agent_id':AGENT},m)['job']
    update={'id':job['id'],'finished':True,'results':[{'key':iid,'status':'saved','path':'First/Week/a.pdf','size':5,'sha256':'a'*64}]}
    for _ in range(2):store.agent_poll({'agent_id':AGENT,'claim':False,'job_update':update},m)
    assert store.view(m)['jobs'][0]['state']=='completed'


def test_receipt_extension_migration_preserves_contents_and_user_edits(tmp_path):
    import hashlib
    item=manifest()["items"][0];k=key(item);root=tmp_path/'download';root.mkdir()
    broken=root/'1.oldpdf';broken.write_bytes(b'PDF')
    receipt={'key':k,'path':'1.oldpdf','size':3,'sha256':hashlib.sha256(b'PDF').hexdigest()}
    state=tmp_path/'receipts.json';save_json(state,{k:receipt})
    registry=LocalMaterials(root,state);saved=registry.save(None,item)
    assert (root/saved['path']).suffix=='.pdf' and (root/saved['path']).read_bytes()==b'PDF'
    assert not broken.exists()


def test_browser_download_rejects_upstream_failure_before_success_headers(monkeypatch, tmp_path):
    from sustech_dashboard import app
    monkeypatch.setattr(app, "CLOUD", True)
    monkeypatch.setattr(app, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(app, "MATERIALS_DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("SUSTECH_PUBLIC_HOST", "124.221.144.155")
    m = manifest(("a",))
    save_json(app.MANIFEST_PATH, m)
    class Broken:
        def get(self, *args, **kwargs):
            response = Response()
            response.status_code = 503
            raise HTTPError(response=response)
    monkeypatch.setattr(app, "Blackboard", Broken)
    client = app.create_app().test_client()
    headers = {"Host": "124.221.144.155", "X-Forwarded-Proto": "https"}
    response = client.get("/api/attachment", query_string={"key": key(m["items"][0])}, headers=headers)
    assert response.status_code == 502
    assert "Content-Disposition" not in response.headers
    assert app._bb_lock.acquire(blocking=False)
    app._bb_lock.release()


def test_api_scopes_csrf_and_agent_origin(monkeypatch, tmp_path):
    import re
    from sustech_dashboard import app
    monkeypatch.setattr(app, "CLOUD", True)
    monkeypatch.setattr(app, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(app, "MATERIALS_DB", tmp_path / "jobs.sqlite3")
    monkeypatch.setenv("SUSTECH_PUBLIC_HOST", "124.221.144.155")
    m = manifest()
    save_json(app.MANIFEST_PATH, m)
    client = app.create_app().test_client()
    headers = {"Host": "124.221.144.155", "X-Forwarded-Proto": "https", "Origin": "https://124.221.144.155"}
    page = client.get("/", headers=headers)
    token = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
    assert client.post("/api/materials/jobs", json={"scope": "all"}, headers=headers).status_code == 400
    headers["X-CSRF-Token"] = token
    store = MaterialsStore(app.MATERIALS_DB)
    store.set_auto(False)
    response = client.post("/api/materials/jobs", json={"scope": "course", "course_id": "c1"}, headers=headers)
    assert response.status_code == 202
    reply = store.agent_poll({"agent_id": AGENT}, m)
    assert reply["job"]["keys"] == [key(m["items"][0])]
    headers["X-Campus-Agent"] = "1"
    assert client.post("/api/materials/agent", json={"agent_id": AGENT}, headers=headers).status_code == 403
