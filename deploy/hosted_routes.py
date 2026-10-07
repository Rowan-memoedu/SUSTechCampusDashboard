"""Register only known spaces in the existing TLS server, with reversible nginx reload."""
from pathlib import Path
import shutil
import subprocess
import time

from hosted_admin import CONFIG, REGISTRY, save, render_nginx
import json


def owner_route(owner, enabled):
    if not enabled:
        return '''
location = /campus { return 302 /app/?login=1; }
location ^~ /campus/ {
    auth_basic off;
    access_log off;
    default_type application/json;
    if ($request_uri ~ "^/campus/api/") {
        return 410 '{"error":"旧个人实例已断开，请从公共入口重新开通","login_url":"/app/?login=1"}';
    }
    return 302 /app/?login=1;
}
'''
    if 'location = /campus/auth/login' not in owner:
        owner = '\nlocation = /campus/auth/login { return 302 /app/?login=1; }\n' + owner
    return owner.replace('/campus/auth/login"', '/app/?login=1"').replace('return 302 /campus/auth/login;', 'return 302 /app/?login=1;')


def install():
    path = Path('/etc/nginx/sites-available/graspmemoedu')
    old = path.read_text()
    start, end = '# BEGIN Campus hosted spaces', '# END Campus hosted spaces'
    records = json.loads(REGISTRY.read_text())
    for sid, value in records.items():
        if value['enabled']:
            save(CONFIG/'routes'/(sid+'.conf'), render_nginx(sid, value['port'], value['host']), 0o644)
    routes = '\n'.join((CONFIG/'routes'/(sid+'.conf')).read_text() for sid, value in records.items() if value['enabled'])
    from entry_admin import refresh, ENTRY_CONFIG
    refresh()
    owner_enabled = any(r['prefix'] == '/campus' for r in json.loads(ENTRY_CONFIG.read_text())['routes'])
    entry = '''location = /app { return 302 /app/; }
location ^~ /app/ {
    auth_basic off;
    proxy_pass http://127.0.0.1:18801/;
    proxy_set_header Host 124.221.144.155;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Prefix /app;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_connect_timeout 5s;
    proxy_read_timeout 45s;
    client_max_body_size 8k;
    access_log off;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
}
'''
    block = start+'\n'+entry+routes+'\n'+end
    if start in old:
        before, rest = old.split(start, 1)
        _, after = rest.split(end, 1)
        updated = before+block+after
    else:
        anchor = '# BEGIN SUSTech campus dashboard'
        if old.count(anchor) != 1 or 'listen 443 ssl' not in old[:old.index(anchor)]:
            raise RuntimeError('Existing HTTPS campus route must be verified before registration')
        updated = old.replace(anchor, block+'\n'+anchor, 1)
    # Keep the existing owner proxy and its upload limits; only converge login.
    owner_start, owner_end = '# BEGIN SUSTech campus dashboard', '# END SUSTech campus dashboard'
    before, rest = updated.split(owner_start, 1)
    owner, after = rest.split(owner_end, 1)
    owner = owner_route(owner, owner_enabled)
    updated = before + owner_start + owner + owner_end + after
    recovery = Path('/var/lib/campus-hosted-recovery')/str(time.time_ns())
    recovery.mkdir(parents=True, mode=0o700)
    (recovery/'nginx.conf').write_text(old)
    try:
        save(path, updated, 0o644)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        save(path, old, 0o644)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise
    print(json.dumps({'registered_spaces': sum(r['enabled'] for r in records.values()), 'recovery': str(recovery),
                      'owner_route_preserved': owner_enabled, 'owner_retired': not owner_enabled}))


if __name__ == '__main__':
    install()
