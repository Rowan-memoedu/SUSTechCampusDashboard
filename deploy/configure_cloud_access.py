"""Create a separate private dashboard login, never reuse the CAS password."""
import json
import secrets
import subprocess
from pathlib import Path

from sustech_dashboard.core import DATA_ROOT
from sustech_dashboard.dpapi_store import protect_password, unprotect_password, _restrict_directory

config = DATA_ROOT / "cloud-access.dpapi.json"
if config.exists():
    existing = json.loads(config.read_text(encoding="utf-8"))
    password = unprotect_password(existing["password_dpapi"])
else:
    password = secrets.token_urlsafe(24)
digest = subprocess.run(["ssh", "ubuntu@124.221.144.155", "openssl passwd -6 -stdin"],
                        input=(password + "\n").encode(), capture_output=True, check=True).stdout.decode().strip()
if not digest.startswith("$6$"):
    raise RuntimeError("Password hashing failed")
subprocess.run(["ssh", "ubuntu@124.221.144.155",
    "sudo -n sh -c 'umask 027; cat > /etc/nginx/sustech-campus.htpasswd; chown root:www-data /etc/nginx/sustech-campus.htpasswd'"],
    input=("campus:" + digest + "\n").encode(), capture_output=True, check=True)
DATA_ROOT.mkdir(parents=True, exist_ok=True)
_restrict_directory(DATA_ROOT)
if not config.exists():
    config.write_text(json.dumps({"url": "https://124.221.144.155/campus", "username": "campus",
        "password_dpapi": protect_password(password)}, ensure_ascii=False), encoding="utf-8")
(DATA_ROOT / "校园面板登录.txt").write_text(
    "网站：https://124.221.144.155/campus/\n用户名：campus\n密码：" + password +
    "\n这是独立的面板密码，不是学校 CAS 密码。请勿分享此文件。\n", encoding="utf-8")
print("Separate dashboard login installed; local download credential protected with DPAPI.")
