"""Install the campus route without replacing other existing nginx routes."""
from pathlib import Path
import subprocess

root = Path("/opt/sustech-room-monitor")
conf = Path("/etc/nginx/sites-available/graspmemoedu")
old = conf.read_text()
start, end = "# BEGIN SUSTech campus dashboard", "# END SUSTech campus dashboard"
block = (root / "deploy/nginx-campus.conf").read_text().strip()
if start in old:
    left, rest = old.split(start, 1)
    _, right = rest.split(end, 1)
    new = left + block + right
else:
    pos = old.rfind("    location / { return 404; }")
    if pos < 0 or "listen 443 ssl" not in old[:pos]:
        raise RuntimeError("Cannot identify existing HTTPS server")
    new = old[:pos] + block + "\n" + old[pos:]
conf.write_text(new)
try:
    subprocess.run(["nginx", "-t"], check=True)
except Exception:
    conf.write_text(old)
    raise
subprocess.run(["install", "-m", "644", str(root / "deploy/sustech-campus-dashboard.service"),
                "/etc/systemd/system/sustech-campus-dashboard.service"], check=True)
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "enable", "--now", "sustech-campus-dashboard"], check=True)
subprocess.run(["systemctl", "reload", "nginx"], check=True)
print("Campus HTTPS route installed; existing routes preserved.")
