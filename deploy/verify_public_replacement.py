"""Read public artifacts and retired routes; no private-account requests."""
import argparse
import hashlib
import json
from pathlib import Path

import requests


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    base = 'https://124.221.144.155'
    session = requests.Session()
    page = session.get(base + '/app/', timeout=30)
    assert page.status_code == 200 and 'text/html' in page.headers['Content-Type']
    assert '0.4.0' in page.text and 'sustech-campus://open' in page.text
    assert 'frame-ancestors' in page.headers['Content-Security-Policy']
    assert session.post(base + '/app/', data={}, timeout=15).status_code == 410
    assert session.post(base + '/app', data={}, timeout=15, allow_redirects=False).status_code == 410
    for route in ('/app/api/status', '/app/login', '/app/desktop/poll', '/spaces/', '/spaces/retired/api/status', '/campus/api/status'):
        assert session.get(base + route, timeout=15).status_code == 410
    for name in ('site.css', 'site.js', 'tokens.css'):
        assert session.get(base + '/app/' + name, timeout=15).status_code == 200
    record = session.get(base + '/app/release.json', timeout=15).json()
    assert record['version'] == '0.4.0'
    digest, size = hashlib.sha256(), 0
    with session.get(base + '/app/downloads/' + record['filename'], stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(1024*1024):
            digest.update(chunk)
            size += len(chunk)
            if size % (4*1024*1024) == 0:
                print(json.dumps({'installer_download_bytes': size}), flush=True)
    assert digest.hexdigest() == record['sha256']
    result = {'verified': True, 'version': record['version'], 'static_page': True, 'retired_routes_410': True,
              'installer_download_sha256': digest.hexdigest(), 'installer_download_bytes': size,
              'private_data_requested': False}
    a.report.write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
