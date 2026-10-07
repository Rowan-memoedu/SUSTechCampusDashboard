import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('retire_hosted', Path(__file__).parents[1] / 'deploy/retire_hosted.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_nginx_retirement_keeps_shared_routes_exactly():
    tool = module()
    original = '''server {
    # shared TLS and other application routes
    location /api/ { proxy_pass http://127.0.0.1:1000; }
    # BEGIN Campus hosted spaces
    location /spaces/ { proxy_pass http://127.0.0.1:2000; }
    # END Campus hosted spaces
    # BEGIN SUSTech campus dashboard
    location /campus/ { proxy_pass http://127.0.0.1:3000; }
    # END SUSTech campus dashboard
    # BEGIN SUSTech public releases
    location /campus-updates/ { alias /var/www/sustech-campus-updates/; }
    # END SUSTech public releases
}
'''
    changed = tool.replace_blocks(original, '    location /spaces/ { return 410; }')
    assert tool.strip_blocks(changed) == tool.strip_blocks(original)
    assert '2000' not in changed and '3000' not in changed
    assert '1000' in changed and '/var/www/sustech-campus-updates/' in changed
    for invalid in (original.replace('# END Campus hosted spaces', '# absent'), original + original):
        with pytest.raises(ValueError):
            tool.replace_blocks(invalid, 'return 410;')


def test_real_nginx_static_page_and_retired_api(tmp_path):
    import shutil
    import socket
    import subprocess
    import time
    import requests
    nginx = shutil.which('nginx')
    if not nginx:
        pytest.skip('Real nginx required')
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'index.html').write_text('<!doctype html><title>Local release</title>')
    (site / 'site.css').write_text('body{color:black}')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = tmp_path / 'nginx.conf'
    config.write_text(f'pid {tmp_path}/nginx.pid; error_log {tmp_path}/error.log; events {{}} http {{ access_log off; server {{ listen 127.0.0.1:{port}; ' + module().static_routes(site) + ' } }')
    process = subprocess.Popen([nginx, '-c', str(config), '-g', 'daemon off;'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session = requests.Session()
    session.trust_env = False
    try:
        base = f'http://127.0.0.1:{port}'
        for _ in range(30):
            try:
                response = session.get(base + '/app/', timeout=1)
                break
            except requests.ConnectionError:
                time.sleep(.1)
        else:
            pytest.fail('nginx did not start')
        assert response.status_code == 200 and 'Local release' in response.text
        assert response.headers['Content-Type'].startswith('text/html')
        assert session.get(base + '/app/site.css').status_code == 200
        assert session.post(base + '/app/', data={}).status_code == 410
        assert session.post(base + '/app', data={}, allow_redirects=False).status_code == 410
        assert session.get(base + '/spaces/retired/api/status').status_code == 410
        assert session.get(base + '/app/api/status').status_code == 410
    finally:
        process.terminate()
        process.wait(timeout=10)
