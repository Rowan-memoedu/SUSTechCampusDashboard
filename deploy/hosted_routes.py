"""Register only known spaces in the existing TLS server, with reversible nginx reload."""
from pathlib import Path
import shutil
import subprocess
import time

from hosted_admin import CONFIG, REGISTRY, save
import json


def install():
    path = Path('/etc/nginx/sites-available/graspmemoedu')
    old = path.read_text()
    start, end = '# BEGIN Campus hosted spaces', '# END Campus hosted spaces'
    records = json.loads(REGISTRY.read_text())
    routes = '\n'.join((CONFIG/'routes'/(sid+'.conf')).read_text() for sid, value in records.items() if value['enabled'])
    public = Path('/var/www/campus-app')
    public.mkdir(parents=True, exist_ok=True)
    save(public/'index.html', '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>南科大校园面板</title><style>body{font:18px/1.7 system-ui;max-width:760px;margin:8vh auto;padding:24px;color:#183234}section{padding:24px;background:#f0f6f5;border-radius:16px}a{color:#006b62}</style><h1>南科大校园面板</h1><p>课程、作业、资料和校园服务。</p><section><h2>受邀托管试用</h2><p>使用维护者给你的独立空间邀请链接，设置面板密码后绑定自己的校园账号。已有空间请使用保存的个人空间地址登录。</p><p>你的个人查询由服务器上的独立实例执行；可共享元数据在权限核验后复用。运营者在技术上能够接触运行中的凭据和个人数据。</p><p>附件直接从学校下载到你的浏览器或本机，面板不保存或转发附件。学校登录状态与面板登录分别管理。</p><p>首批最多 5 人。校园打印等功能仍需要自己的校园网或 VPN 执行设备。</p></section><h2>本机运行</h2><p>现有独立客户端可在自己的设备运行。公网入口与本机页面的连接、迁移引导将在后续阶段提供。</p></html>''', 0o644)
    entry = '''location = /app { return 302 /app/; }
location ^~ /app/ {
    auth_basic off;
    alias /var/www/campus-app/;
    index index.html;
    autoindex off;
    access_log off;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'" always;
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
    print(json.dumps({'registered_spaces': sum(r['enabled'] for r in records.values()), 'recovery': str(recovery), 'owner_route_preserved': True}))


if __name__ == '__main__':
    install()
