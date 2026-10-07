"""Repair only the campus auth subrequest upload limit; nginx reload is graceful."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    path = Path('/etc/nginx/sites-available/graspmemoedu')
    original = path.read_bytes()
    text = original.decode()
    start = 'location = /_sustech_campus_auth {'
    if text.count(start) != 1:
        raise RuntimeError('Expected exactly one campus authentication location')
    left, rest = text.split(start, 1)
    auth, right = rest.split('}', 1)
    if 'client_max_body_size' in auth:
        raise RuntimeError('Upload limit already configured; inspect before changing')
    if 'internal;' not in auth or 'proxy_pass_request_body off;' not in auth:
        raise RuntimeError('Unexpected authentication configuration')
    auth = auth.replace('    internal;', '    internal;\n    client_max_body_size 256m;', 1)
    updated = (left + start + auth + '}' + right).encode()
    recovery = Path('/var/lib/sustech-room-monitor/upgrade-recovery') / (
        'upload-proxy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    recovery.mkdir(mode=0o700)
    (recovery / 'nginx.conf').write_bytes(original)
    if path.read_bytes() != original:
        raise RuntimeError('Configuration changed concurrently')
    try:
        path.write_bytes(updated)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        path.write_bytes(original)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise
    print(json.dumps({'recovery': str(recovery), 'before_sha256': hashlib.sha256(original).hexdigest(),
                      'after_sha256': hashlib.sha256(updated).hexdigest(), 'nginx_reloaded': True}))


if __name__ == '__main__':
    main()
