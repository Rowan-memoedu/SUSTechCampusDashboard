"""Read-only first-run render in isolated headless Google Chrome."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--chrome", type=Path, required=True)
parser.add_argument("--executable", type=Path, required=True)
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--screenshot", type=Path, required=True)
args = parser.parse_args()
root = Path(tempfile.mkdtemp(prefix="ui-canary-", dir=args.cache))
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = dict(os.environ, SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_DOWNLOAD_ROOT=str(root / "files"))
for name in ("SUSTECH_CLOUD", "CREDENTIALS_DIRECTORY", "SUSTECH_PUBLIC_HOST"):
    env.pop(name, None)
process = subprocess.Popen([str(args.executable), "--no-browser", "--port", str(port)], env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{port}"
session = requests.Session()
session.trust_env = False
try:
    for _ in range(100):
        try:
            if session.get(base + "/", timeout=1).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(.2)
    token = (root / "browser-token").read_text()
    result = subprocess.run([str(args.chrome), "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-background-networking", "--disable-extensions",
        "--user-data-dir=" + str(root / "chrome"), "--window-size=1280,960", "--virtual-time-budget=5000",
        "--screenshot=" + str(args.screenshot), "--dump-dom", base + "/#access=" + token],
        capture_output=True, timeout=45)
    dom = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == 0 and 'id="owner-login"' in dom
    assert 'type="password"' in dom and '本机与更新' in dom
    assert args.screenshot.stat().st_size > 10000
    print("CHROME_RENDER_OK " + str(args.screenshot), flush=True)
finally:
    try:
        import re
        session.post(base + "/auth/unlock", json={"token": (root / "browser-token").read_text()}, headers={"Origin": base}, timeout=5)
        page = session.get(base + "/setup", timeout=5)
        csrf = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
        session.post(base + "/api/instance/stop", json={}, headers={"Origin": base, "X-CSRF-Token": csrf}, timeout=5)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
