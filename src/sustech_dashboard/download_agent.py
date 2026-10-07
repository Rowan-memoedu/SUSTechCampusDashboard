"""Read private tasks from the dashboard; download bytes directly from school."""
import json
import os
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


def pair_config():
    if os.name == 'nt':
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        # Linux execution hosts use a private systemd credential, never a plaintext data file.
        directory = os.environ.get('CREDENTIALS_DIRECTORY')
        if not directory:raise ValueError('Linux 执行主机需配置 systemd campus-pair 凭据')
        config = json.loads((Path(directory) / 'campus-pair').read_text(encoding='utf-8'))
    from .retirement import operator_url, RETIRED_MESSAGE
    if operator_url(config.get('url')):
        raise ValueError(RETIRED_MESSAGE)
    return config


def personal_pair_available():
    try:
        return bool(pair_config().get('url'))
    except (OSError, ValueError, KeyError):
        return False


def cloud_session():
    config = pair_config()  # Reject retired endpoints before decrypting or connecting.
    password = (unprotect_password(config.get('token_dpapi') or config['password_dpapi'])
                if os.name == 'nt' else config['password'])
    url = config["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("个人服务器必须使用 HTTPS 地址")
    url = url.rstrip("/")
    session = requests.Session()
    if config.get('token_dpapi'):
        session.headers['Authorization'] = 'Bearer ' + password
    else:
        session.auth = (config["username"], password)
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
        from .authentication import load_owner_credentials
        from .provider import Blackboard
        if not load_owner_credentials():
            raise RuntimeError('请先在本机配置自己的校园账号，附件仅从学校直接下载')
        from .app import _bb_lock
        with _bb_lock:
            Blackboard().download_attachment(course_id, content_id, attachment_id, target)


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
    from .paths import prepare_private_directory
    prepare_private_directory()
    identity = DATA_ROOT / 'download-agent-id.txt'
    if not identity.exists():
        try:
            with identity.open('x',encoding='ascii') as handle:handle.write(uuid.uuid4().hex)
        except FileExistsError:pass
    url, session = cloud_session()
    def poll(payload):
        response = session.post(url + "/api/materials/agent", json=payload,
                    headers={"X-Campus-Agent": "1"}, timeout=(10, 30))
        response.raise_for_status()
        return response.json()
    stop = threading.Event()
    from .print_agent import run as print_agent
    # One paired client, with independent workers so downloads never delay print polling.
    worker = threading.Thread(target=print_agent, args=(stop,), name='campus-print-agent')
    worker.start()
    try:
        run_agent(CloudFiles, poll, stop)
    finally:
        stop.set()
        worker.join(timeout=300)


def run_agent(provider_factory, poll, stop, remote_factory=None):
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
        inventory_at = time.monotonic()

        def download_job(job):
            try:
                provider = (remote_factory if job.get('_remote') and remote_factory else provider_factory)()
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
            if not pending_inventory and time.monotonic() - inventory_at >= 30:
                pending_inventory = LocalMaterials(DOWNLOAD_ROOT, DATA_ROOT / 'downloaded-files.json').inventory()
                inventory_at = time.monotonic()
            with guard:
                active = progress["id"]
                payload = {"agent_id": agent_id, "claim": active is None and not stop.is_set(), "active_job": active,
                           "state": "downloading" if active else "idle", "current_file": progress["current_file"],
                           '_remote': progress.get('remote', False)}
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
                        progress.update(id=job["id"], results=[], finished=False, remote=bool(job.get('_remote')))
                        worker = threading.Thread(target=download_job, args=(job,), daemon=True)
                        worker.start()
                save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(), "connected": True})
            except Exception as exc:
                save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(),
                          "connected": False, "error": type(exc).__name__})
            time.sleep(5) if stop.is_set() else stop.wait(5)


class PairedPoll:
    """One file worker, two queues; acknowledgements always go to the owning queue."""
    def __init__(self, local_poll):
        self.local_poll = local_poll
        self.signature = None
        self.session = None
        self.inventory = []
        self.inventory_at = 0
        self.initialized = False

    @staticmethod
    def heartbeat(payload):
        return {k: v for k, v in dict(payload, claim=False, active_job=None).items()
                if k not in {'job_update', '_remote'}}

    def remote_poll(self, payload):
        from .materials_local import LocalMaterials
        signature = CONFIG.stat().st_mtime_ns
        if signature != self.signature:
            if self.session:
                self.session.close()
            self.url, self.session = cloud_session()
            self.signature, self.initialized, self.inventory_at = signature, False, 0
            self.inventory = []
        outgoing = {k: v for k, v in payload.items() if k not in {'inventory', 'seen_keys', '_remote'}}
        if not self.initialized:
            outgoing['seen_keys'] = load_json(DATA_ROOT / 'attachments.json', {}).get('seen', [])
        if not self.inventory and time.monotonic() - self.inventory_at >= 30:
            self.inventory = LocalMaterials(DOWNLOAD_ROOT, DATA_ROOT / 'downloaded-files.json').inventory()
            self.inventory_at = time.monotonic()
        outgoing['inventory'] = self.inventory[:200]
        response = self.session.post(self.url + '/api/materials/agent', json=outgoing,
                                    headers={'X-Campus-Agent': '1'}, timeout=(10, 30))
        response.raise_for_status()
        result = response.json()
        self.initialized = True
        self.inventory = self.inventory[200:]
        if result.get('job'):
            result['job']['_remote'] = True
        return result

    def __call__(self, payload):
        remote = bool(payload.get('active_job') and payload.get('_remote'))
        local = self.local_poll(self.heartbeat(payload) if remote else {k: v for k, v in payload.items() if k != '_remote'})
        if not personal_pair_available():
            if remote:
                raise RuntimeError('电脑配对信息不可用，下载回执等待连接恢复')
            return local
        try:
            outgoing = payload if remote or (not payload.get('active_job') and not local.get('job')) else self.heartbeat(payload)
            result = self.remote_poll(outgoing)
            save_json(DATA_ROOT / 'cloud-download-health.json', {'connected': True, 'at': time.time()})
            return result if remote or result.get('job') else local
        except Exception:
            save_json(DATA_ROOT / 'cloud-download-health.json', {'connected': False, 'at': time.time()})
            if remote:
                raise
            return local


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
    poll = PairedPoll(lambda payload: store.agent_poll(payload, load_json(app.MANIFEST_PATH, {})))
    while not stop.is_set():
        try:
            run_agent(DirectFiles, poll, stop, remote_factory=CloudFiles)
        except Exception as exc:
            save_json(DATA_ROOT / "download-agent-health.json", {"at": datetime.now(CHINA_TZ).isoformat(),
                "connected": False, "error": type(exc).__name__})
        stop.wait(15)


if __name__ == "__main__":
    main()
