"""Download new Blackboard attachments through the private HTTPS dashboard."""
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .core import DATA_ROOT, DOWNLOAD_ROOT, CHINA_TZ, attachment_key, sync_attachments, save_json
from .dpapi_store import unprotect_password

CONFIG = DATA_ROOT / "cloud-access.dpapi.json"


def cloud_session():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = config["url"]
    if url != "https://124.221.144.155/campus":
        raise ValueError("云端地址无效")
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
    import msvcrt
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with (DATA_ROOT / "download-agent.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise DownloadBusy from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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
    stop = threading.Event()
    while not stop.is_set():
        sync_once()
        stop.wait(30 * 60)


if __name__ == "__main__":
    main()
