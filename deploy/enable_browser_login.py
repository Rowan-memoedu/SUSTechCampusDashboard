"""Enable persistent login after the signed 0.2.2+ runtime is installed.

Run as root. Input is a pre-hashed website credential, never a CAS password.
Rollback keeps nginx's original Basic-auth protection in place.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request


def main():
    config = json.loads(sys.stdin.read())
    if not config.get("username") or not config.get("password_hash", "").startswith("scrypt:"):
        raise ValueError("Expected a hashed website credential")
    data = Path("/var/lib/sustech-room-monitor/dashboard")
    # The authentication rollout must not interrupt a school-side write.
    with sqlite3.connect(f"file:{data / 'actions.sqlite3'}?mode=ro", uri=True) as db:
        if db.execute("SELECT COUNT(*) FROM actions WHERE state='sending'").fetchone()[0]:
            raise RuntimeError("A school write needs to finish before deployment")
    nginx = Path("/etc/nginx/sites-available/graspmemoedu")
    dropin = Path("/etc/systemd/system/sustech-campus-dashboard.service.d/browser-login.conf")
    credential = Path("/etc/credstore/sustech-campus-web.json")
    recovery = data.parent / "upgrade-recovery/browser-login-0.2.2"
    recovery.mkdir(parents=True, mode=0o700, exist_ok=True)
    original = nginx.read_bytes()
    saved_dropin = dropin.read_bytes() if dropin.exists() else None
    (recovery / "nginx.conf").write_bytes(original)
    if saved_dropin is not None:
        (recovery / "browser-login.conf").write_bytes(saved_dropin)
    credential.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if credential.exists():
        (recovery / "website-credential.json").write_bytes(credential.read_bytes())
    descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(config, stream)
    credential.chmod(0o600)
    dropin.write_text("[Service]\nEnvironment=SUSTECH_BROWSER_LOGIN=1\n"
                      "LoadCredential=campus-web:/etc/credstore/sustech-campus-web.json\n")
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", "sustech-campus-dashboard"], check=True)
        for _ in range(45):
            try:
                req = urllib.request.Request("http://127.0.0.1:18771/auth/verify", headers={
                    "Host": "124.221.144.155", "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/campus",
                    "X-Campus-Original-URI": "/campus/auth/login"})
                with urllib.request.urlopen(req, timeout=2) as response:
                    if response.status == 204:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Authenticated runtime did not become ready")
        start, end = "# BEGIN SUSTech campus dashboard", "# END SUSTech campus dashboard"
        left, rest = original.decode().split(start, 1)
        _, right = rest.split(end, 1)
        block = Path(__file__).with_name("nginx-campus-browser.conf").read_text().strip()
        nginx.write_text(left + block + right)
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        print(json.dumps({"browser_login": True, "remember_days": 30, "recovery": str(recovery)}))
    except Exception:
        nginx.write_bytes(original)
        if saved_dropin is None:
            dropin.unlink(missing_ok=True)
        else:
            dropin.write_bytes(saved_dropin)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", "sustech-campus-dashboard"], check=True)
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        raise


if __name__ == "__main__":
    main()
