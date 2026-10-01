"""Private HTTPS reverse proxy deployment with one background sync worker."""
import threading
from pathlib import Path
import os
import json


def main():
    from sustech_survival.sso import cred_set
    credentials = json.loads((Path(os.environ["CREDENTIALS_DIRECTORY"]) / "sustech-cas").read_text())
    os.environ["SUSTECH_CREDENTIALS"] = "/nonexistent/sustech-credentials"
    cred_set(sid=credentials["sid"], pwd=credentials["password"])
    from .app import create_app, _sync_loop, _materials_loop
    from waitress import serve
    stop = threading.Event()
    threading.Thread(target=_sync_loop, args=(stop,), daemon=True, name="campus-sync").start()
    threading.Thread(target=_materials_loop, args=(stop,), daemon=True, name="materials-sync").start()
    try:
        serve(create_app(), host="127.0.0.1", port=18771, threads=6,
              clear_untrusted_proxy_headers=False, max_request_body_size=256*1024*1024)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
