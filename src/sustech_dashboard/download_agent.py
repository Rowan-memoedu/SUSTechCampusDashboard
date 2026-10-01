"""Download new Blackboard attachments through the private HTTPS dashboard."""
import json
import threading
import time
import uuid
from urllib.parse import urlparse
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .core import DATA_ROOT, DOWNLOAD_ROOT, CHINA_TZ, attachment_key, sync_attachments, save_json, load_json
from .dpapi_store import unprotect_password

CONFIG = DATA_ROOT / "cloud-access.dpapi.json"


def cloud_session():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = config["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("个人服务器必须使用 HTTPS 地址")
    url = url.rstrip("/")
    session = requests.Session()
    session.auth = (config["username"], unprotect_password(config["password_dpapi"]))
    session.mount(url, HTTPAdapter(max_retries=Retry(
        total=2, allowed_methods={"GET"}, backoff_factor=1, status_forcelist={502, 503, 504})))
    return url, session


def cloud_status():
    url, session = cloud_session()
    response = session.get(url + "/api/status", timeout=(10, 30))
    response.raise_for_status()
    return response.json()


class CloudFiles:
    def __init__(self):
        self.url, self.session = cloud_session()
        response = self.session.get(self.url + "/api/attachments", timeout=(10, 120))
        response.raise_for_status()
        self.manifest = response.json()

    def current_courses(self):
        return self.manifest["courses"]

    def attachments(self, course_id):
        return [i for i in self.manifest["items"] if i["course_id"] == course_id]

    def download_attachment(self, course_id, content_id, attachment_id, target: Path):
        tmp = target.with_name(target.name + ".part")
        try:
            with self.session.get(self.url + "/api/attachment", params={
                "key": attachment_key(course_id, content_id, attachment_id)
            }, timeout=(10, 240), stream=True) as response, tmp.open("wb") as handle:
                response.raise_for_status()
                for chunk in response.iter_content(65536):
                    if chunk:
                        handle.write(chunk)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)


class DownloadBusy(Exception):
    pass


@contextmanager
def download_lock():
    from .locking import exclusive_file, InstanceBusy
    try:
        with exclusive_file(DATA_ROOT / "download-agent.lock"):
            yield
    except InstanceBusy:
        raise DownloadBusy from None


def sync_once():
    try:
        with download_lock():
            return _sync_once()
    except DownloadBusy:
        return {"mode": "busy", "message": "本机附件同步正在运行"}


def _sync_once():
    provider = None
    try:
        provider = CloudFiles()
        result = sync_attachments(provider, DOWNLOAD_ROOT, DATA_ROOT / "attachments.json")
    except Exception as exc:
        result = {"mode": "error", "error": type(exc).__name__}
    result["updated_at"] = datetime.now(CHINA_TZ).isoformat()
    save_json(DATA_ROOT / "download-agent.json", result)
    if provider is not None:
        try:
            response = provider.session.post(provider.url + "/api/download-status", json=result,
                headers={"X-Campus-Agent": "1"}, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            result["report_error"] = type(exc).__name__
            save_json(DATA_ROOT / "download-agent.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    url, session = cloud_session()
    def poll(payload):
        response = session.post(url + "/api/materials/agent", json=payload,
                    headers={"X-Campus-Agent": "1"}, timeout=(10, 30))
        response.raise_for_status()
        return response.json()
    run_agent(CloudFiles, poll, threading.Event())


def run_agent(provider_factory, poll, stop):
    """Same baseline, queue and receipt protocol for direct and paired clients."""
    from .materials_local import LocalMaterials
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    identity = DATA_ROOT / "download-agent-id.txt"
    if not identity.exists():
        identity.write_text(uuid.uuid4().hex, encoding="ascii")
    agent_id = identity.read_text(encoding="ascii").strip()
    with download_lock():
        registry = LocalMaterials(DOWNLOAD_ROOT, DATA_ROOT / "downloaded-files.json")
        baseline = DATA_ROOT / "attachments.json"
        # A malformed existing baseline must never become an empty/new baseline.
        state = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else {"baseline_at": None, "seen": []}
        if not state.get("baseline_at"):
            manifest = provider_factory().manifest
            if not manifest.get("updated_at"):
                raise RuntimeError("等待首次完整附件清单")
            state = {"baseline_at": datetime.now(CHINA_TZ).isoformat(), "seen": sorted(
                attachment_key(i["course_id"], i["content_id"], i["id"]) for i in manifest["items"])}
            save_json(baseline, state)
        guard = threading.RLock()
        progress = {"id": None, "results": [], "finished": False, "current_file": "", "auto_enabled": True}
        worker = None
        inventory = registry.inventory()
        pending_inventory = list(inventory)
        initialized = False

        def download_job(job):
            try:
                provider = provider_factory()
                items = {attachment_key(i["course_id"], i["content_id"], i["id"]): i for i in provider.manifest["items"]}
                for key in job["keys"]:
                    with guard:
                        enabled = progress["auto_enabled"]
                    item = items.get(key)
                    with guard:
                        progress["current_file"] = item["file_name"] if item else "附件已不可见"
                    try:
                        if job["source"] == "auto" and not enabled:
                            raise RuntimeError("自动下载已暂停")
                        if not item:
                            raise RuntimeError("附件已不可见，请刷新清单")
                        receipt = registry.save(provider, item)
                        state["seen"] = sorted(set(state["seen"]) | {key})
                        save_json(baseline, state)
                    except Exception as exc:
                        message = str(exc) if isinstance(exc, (FileExistsError, RuntimeError)) else type(exc).__name__
                        receipt = {"key": key, "status": "failed", "error": message}
                    with guard:
                        progress["results"].append(receipt)
            except Exception as exc:
                with guard:
                    progress["results"] = [{"key": k, "status": "failed", "error": type(exc).__name__} for k in job["keys"]]
            finally:
                with guard:
                    progress["finished"] = True
                    progress["current_file"] = ""

        while not stop.is_set() or (worker is not None and worker.is_alive()) or progress["id"]:
            with guard:
                active = progress["id"]
                payload = {"agent_id": agent_id, "claim": active is None and not stop.is_set(), "active_job": active,
                           "state": "downloading" if active else "idle", "current_file": progress["current_file"]}
                if not initialized:
                    payload["seen_keys"] = state["seen"]
                if pending_inventory:
                    payload["inventory"] = pending_inventory[:200]
                if active:
                    payload["job_update"] = {"id": active, "results": list(progress["results"]), "finished": progress["finished"]}
            try:
                data = poll(payload)
                initialized = True
                pending_inventory = pending_inventory[200:]
                with guard:
                    progress["auto_enabled"] = data["auto_enabled"]
                    if active and payload["job_update"]["finished"]:
                        result = {"mode": "incremental", "updated_at": datetime.now(CHINA_TZ).isoformat(),
                            "seen": len(state["seen"]), "downloaded": sum(r["status"] == "saved" for r in progress["results"]),
                            "failed": [r["error"] for r in progress["results"] if r["status"] == "failed"], "job_id": active}
                        save_json(DATA_ROOT / "download-agent.json", result)
                        progress.update(id=None, results=[], finished=False)
                    if data.get("job"):
                        job = data["job"]
                        progress.update(id=job["id"], results=[], finished=False)
                        worker = threading.Thread(target=download_job, args=(job,), daemon=True)
                        worker.start()
                save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(), "connected": True})
            except Exception as exc:
                save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(),
                          "connected": False, "error": type(exc).__name__})
            time.sleep(5) if stop.is_set() else stop.wait(5)


def local_agent(stop):
    from . import app
    from .materials_store import MaterialsStore
    store = MaterialsStore(app.MATERIALS_DB)
    class DirectFiles:
        def __init__(self):
            self.manifest = load_json(app.MANIFEST_PATH, {})
            if not self.manifest.get("updated_at") or app._scan_state["error"]:
                raise RuntimeError("等待完整附件清单")
        def download_attachment(self, course_id, content_id, attachment_id, target):
            with app._bb_lock:
                app.Blackboard().download_attachment(course_id, content_id, attachment_id, target)
    def poll(payload):
        return store.agent_poll(payload, load_json(app.MANIFEST_PATH, {}))
    while not stop.is_set():
        try:
            run_agent(DirectFiles, poll, stop)
        except Exception as exc:
            save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(),
                "connected": False, "error": type(exc).__name__})
        stop.wait(15)


if __name__ == "__main__":
    main()
