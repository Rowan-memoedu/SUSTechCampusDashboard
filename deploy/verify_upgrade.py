"""Real 0.2.0 executable -> signed public release, isolated ownerless instance."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import tempfile
import time

import requests


def verify(executable, cache):
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="upgrade-canary-", dir=cache))
    (root / "attachments.json").write_text(json.dumps({"baseline_at": "2026-09-29", "seen": ["course/content/original"]}))
    (root / "downloaded-files.json").write_text('{"fixture":"keep"}')
    with sqlite3.connect(root / "actions.sqlite3") as db:
        db.execute("CREATE TABLE actions(id TEXT PRIMARY KEY, object TEXT, state TEXT, result TEXT, at REAL)")
        db.execute("INSERT INTO actions VALUES ('fixture','assignment:fixture','needs_review',NULL,0)")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_DOWNLOAD_ROOT=str(root / "files"))
    for key in ("SUSTECH_CLOUD", "SUSTECH_PUBLIC_HOST", "CREDENTIALS_DIRECTORY", "SUSTECH_MANAGED_RUNTIME"):
        env.pop(key, None)
    log = (root / "runtime.log").open("wb")
    child = subprocess.Popen([str(executable), "--no-browser", "--port", str(port)], env=env, stdout=log, stderr=log)
    session = requests.Session()
    session.trust_env = False
    base = f"http://127.0.0.1:{port}"
    headers = {"Origin": base}
    def unlock():
        response = session.post(base + "/auth/unlock", headers=headers,
            json={"token": (root / "browser-token").read_text()}, timeout=5)
        assert response.ok
        page = session.get(base + "/setup", timeout=5)
        headers["X-CSRF-Token"] = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
    def await_value(check, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and child.poll() is None:
            try:
                value = check()
                if value:
                    return value
            except requests.RequestException:
                pass
            time.sleep(1)
        raise RuntimeError("Canary timed out: " + str(root))
    try:
        await_value(lambda: session.get(base + "/", timeout=1).ok, 60)
        unlock()
        original = session.get(base + "/api/instance").json()["version"]
        assert session.post(base + "/api/instance/updates/check", headers=headers, json={}).status_code == 202
        def available():
            status = session.get(base + "/api/instance", timeout=5).json()
            if status.get("update", {}).get("state") == "error":
                raise RuntimeError("Update check failed: " + str(status["update"]))
            return status if status.get("update", {}).get("available") else None
        status = await_value(available, 90)
        target = status["update"]["latest"]
        assert session.post(base + "/api/instance/updates/install", headers=headers, json={}).status_code == 202
        def installed():
            response = session.get(base + "/api/instance", timeout=5)
            if response.status_code == 401:
                unlock()
                return None
            status = response.json()
            if status.get("update", {}).get("state") == "error":
                raise RuntimeError("Update install failed: " + str(status["update"]))
            return status if status.get("version") == target else None
        value = await_value(installed, 900)
        assert value["configured"] is False
        assert {name: (root / name).read_bytes() for name in before} == before
        await_value(lambda: (root / "updates/current.json").exists(), 10)
        pointer = json.loads((root / "updates/current.json").read_text())
        assert pointer["version"] == target
        unlock()
        assert session.post(base + "/api/instance/stop", json={}, headers=headers).status_code == 202
        assert child.wait(timeout=25) == 0
        result = {"from": original, "to": target, "signed_update": True, "state_unchanged": True,
                  "actions_not_replayed": True, "data_directory": str(root)}
        print(json.dumps(result), flush=True)
        return result
    finally:
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
            else:
                child.terminate()
                child.wait(timeout=30)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    verify(args.executable, args.cache)
