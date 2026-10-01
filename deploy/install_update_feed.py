"""Add a public static release directory without touching other site routes."""
from pathlib import Path
import subprocess

config = Path("/etc/nginx/sites-available/graspmemoedu")
old = config.read_text()
start = "# BEGIN SUSTech public releases"
end = "# END SUSTech public releases"
block = """# BEGIN SUSTech public releases
    location ^~ /campus-updates/ {
        alias /var/www/sustech-campus-updates/;
        auth_basic off;
        autoindex off;
        limit_except GET { deny all; }
        add_header X-Content-Type-Options nosniff always;
        add_header X-Robots-Tag noindex always;
        add_header Cache-Control "no-cache" always;
    }
    # END SUSTech public releases"""
if start in old:
    left, rest = old.split(start, 1)
    _, right = rest.split(end, 1)
    new = left + block + right
else:
    index = old.rfind("    location / { return 404; }")
    if index < 0 or "listen 443 ssl" not in old[:index]:
        raise RuntimeError("Cannot identify existing HTTPS server")
    new = old[:index] + block + "\n" + old[index:]
if new != old:
    config.write_text(new)
    try:
        subprocess.run(["nginx", "-t"], check=True)
    except Exception:
        config.write_text(old)
        raise
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
print("Static release route configured; campus authentication unchanged.")
