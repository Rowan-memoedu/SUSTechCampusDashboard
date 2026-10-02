"""Deploy and verify remembered website login for the already paired owner."""
import argparse
import copy
import json
import re
import subprocess
import time
from urllib.parse import urlparse

import requests
from werkzeug.security import generate_password_hash

from sustech_dashboard import __version__
from sustech_dashboard.download_agent import cloud_session


def upgrade(base, session):
    page = session.get(base + "/", timeout=20)
    page.raise_for_status()
    csrf = re.search(r'const roomCsrf="([^"]+)"', page.text).group(1)
    headers = {"Origin": "https://" + urlparse(base).netloc, "X-CSRF-Token": csrf}
    current = session.get(base + "/api/instance", timeout=15).json()
    if current["version"] == __version__:
        print(json.dumps({"version": __version__, "already_installed": True}))
        return
    response = session.post(base + "/api/instance/updates/check", json={}, headers=headers, timeout=15)
    assert response.status_code == 202
    for _ in range(90):
        state = session.get(base + "/api/instance", timeout=15).json()
        update = state["update"]
        if update.get("state") == "error":
            raise RuntimeError("Signed update check failed")
        if update.get("available") and update.get("latest") == __version__:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Expected signed version is unavailable")
    assert session.post(base + "/api/instance/updates/install", json={}, headers=headers, timeout=15).status_code == 202
    for _ in range(300):
        try:
            response = session.get(base + "/api/instance", timeout=10)
            if response.ok and response.json().get("version") == __version__:
                assert response.json()["configured"]
                print(json.dumps({"from": current["version"], "version": __version__, "configured": True}))
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("Expected signed update did not become ready")


def enable(base, session):
    response = session.get(base + "/api/instance", timeout=15)
    response.raise_for_status()
    if response.json().get("version") != __version__:
        raise RuntimeError("Finish and verify the signed runtime upgrade before enabling browser login")
    username, password = session.auth
    payload = {"username": username, "password_hash": generate_password_hash(password, method="scrypt")}
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
        "ubuntu@124.221.144.155", "sudo -n python3 /var/tmp/campus-client-build/src/deploy/enable_browser_login.py"],
        input=json.dumps(payload), text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Enable browser login failed: " + result.stderr[-1800:])
    print(result.stdout.strip())


def verify(base, paired):
    anonymous = requests.Session()
    page = anonymous.get(base + "/", allow_redirects=False, timeout=15)
    assert page.status_code == 302 and page.headers["Location"].endswith("/campus/auth/login")
    assert anonymous.get(base + "/api/status", timeout=15).status_code == 401
    page = anonymous.get(base + "/auth/login", timeout=15)
    assert page.status_code == 200 and "WWW-Authenticate" not in page.headers
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    origin = "https://" + urlparse(base).netloc
    response = anonymous.post(base + "/auth/login", headers={"Origin": origin}, data={
        "username": paired.auth[0], "password": paired.auth[1], "remember": "on", "csrf": csrf},
        allow_redirects=False, timeout=20)
    assert response.status_code == 303
    header = response.headers["Set-Cookie"]
    assert all(flag in header for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Max-Age=2592000", "Path=/campus/"))
    saved = copy.deepcopy(anonymous.cookies)
    reopened = requests.Session()
    reopened.cookies.update(saved)
    page = reopened.get(base + "/", timeout=15)
    assert page.status_code == 200 and "退出登录" in page.text
    state = reopened.get(base + "/api/instance", timeout=15).json()
    assert state["version"] == __version__ and state["configured"]
    assert paired.get(base + "/api/status", timeout=15).status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    result = reopened.post(base + "/auth/logout", data={"csrf": csrf}, headers={"Origin": origin},
                           allow_redirects=False, timeout=15)
    assert result.status_code == 303
    replay = requests.Session()
    replay.cookies.update(saved)
    assert replay.get(base + "/api/status", timeout=15).status_code == 401
    print(json.dumps({"version": state["version"], "remember_days": 30, "cookie_secure": True,
        "new_browser_session_authenticated": True, "logout_revoked": True, "download_agent_auth": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["upgrade", "enable", "verify"])
    args = parser.parse_args()
    base, session = cloud_session()
    if args.action == "upgrade":
        upgrade(base, session)
    elif args.action == "enable":
        enable(base, session)
    else:
        verify(base, session)
