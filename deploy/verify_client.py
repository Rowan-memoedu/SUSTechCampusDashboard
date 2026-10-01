"""Real executable, clean data directory, HTTP auth and packaging acceptance."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

import requests


def verify(executable, cache):
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="client-canary-", dir=cache))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_DOWNLOAD_ROOT=str(root / "files"))
    for key in ("SUSTECH_CLOUD", "SUSTECH_PUBLIC_HOST", "CREDENTIALS_DIRECTORY", "SUSTECH_MANAGED_RUNTIME"):
        env.pop(key, None)
    selftest = subprocess.run([str(executable), "--self-test"], env=env, capture_output=True, timeout=60)
    assert selftest.returncode == 0, selftest.stderr.decode(errors="replace")
    assert b"SELF_TEST_OK" in selftest.stdout
    log = (root / "runtime.log").open("wb")
    child = subprocess.Popen([str(executable), "--no-browser", "--port", str(port)], env=env,
                             stdout=log, stderr=log)
    session = requests.Session()
    session.trust_env = False
    base = f"http://127.0.0.1:{port}"
    headers = {"Origin": base}
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and child.poll() is None:
            try:
                page = session.get(base + "/", timeout=1)
                if page.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(.25)
        else:
            raise RuntimeError("Executable did not start; see " + str(root / "runtime.log"))
        assert session.get(base + "/api/instance").status_code == 401
        response = session.post(base + "/auth/unlock", headers=headers,
                                json={"token": (root / "browser-token").read_text()})
        assert response.status_code == 200
        page = session.get(base + "/")
        assert page.url.endswith("/setup") and "CAS" in page.text
        assert session.get(base + "/static/instance.js").status_code == 200
        csrf = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
        headers["X-CSRF-Token"] = csrf
        value = session.get(base + "/api/instance").json()
        assert value["configured"] is False
        assert Path(value["data_root"]) == root
        assert not (root / "attachments.json").exists()
        assert session.post(base + "/api/instance/stop", json={}, headers=headers).status_code == 202
        assert child.wait(timeout=20) == 0
        result = {"platform": os.name, "version": value["version"], "self_test": True,
                  "clean_first_run": True, "loopback_auth": True, "graceful_stop": True,
                  "data_directory": str(root)}
        print(json.dumps(result), flush=True)
        return result
    finally:
        if child.poll() is None:
            # Clean up only this canary's process tree, never a real campus instance.
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
