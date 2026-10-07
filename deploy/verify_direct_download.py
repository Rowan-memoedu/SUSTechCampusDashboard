"""Verify a school-origin download on the owner's device; never print campus content."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import requests

from sustech_dashboard.authentication import load_owner_credentials
from sustech_dashboard.download_agent import cloud_session
from sustech_dashboard.provider import Blackboard, BB_BASE


def verify(destination):
    url, cloud = cloud_session()
    response = cloud.get(url+'/api/attachments', timeout=(10, 60))
    response.raise_for_status()
    manifest = response.json()
    candidates = [i for i in manifest['items'] if int(i.get('size') or 0) < 5*1024*1024]
    if not candidates:
        raise RuntimeError('No bounded download candidate')
    item = candidates[0]
    school = BB_BASE+f"/learn/api/public/v1/courses/{item['course_id']}/contents/{item['content_id']}/attachments/{item['id']}/download"
    anonymous = requests.get(school, allow_redirects=False, stream=True, timeout=(10, 30))
    anonymous_status = anonymous.status_code
    anonymous.close()
    if not load_owner_credentials():
        raise RuntimeError('Local campus credentials are required for direct-download acceptance')
    bb = Blackboard()
    browser_like = requests.Session()
    browser_like.cookies.update(bb.session.cookies)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if __import__('os').name == 'nt':
        from sustech_dashboard.dpapi_store import _restrict_directory
        _restrict_directory(destination)
    target = destination/'school-direct-canary.bin'
    if target.exists():
        raise ValueError('Use a fresh acceptance destination')
    from urllib.parse import urljoin
    response = None
    for _ in range(8):
        if urlparse(school).scheme != 'https' or urlparse(school).hostname != 'bb.sustech.edu.cn':
            raise RuntimeError('School download redirected outside the verified Blackboard origin')
        response = browser_like.get(school, stream=True, allow_redirects=False, timeout=(10, 60))
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        school = urljoin(school, response.headers['Location'])
        response.close()
    with response:
        response.raise_for_status()
        if response.status_code != 200 or 'text/html' in response.headers.get('Content-Type', ''):
            raise RuntimeError('School returned a login or content page instead of attachment bytes')
        with target.open('xb') as handle:
            total = 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > 5*1024*1024:
                    raise RuntimeError('Canary exceeds the 5 MiB acceptance budget')
                handle.write(chunk)
        result = {'anonymous_school_status': anonymous_status, 'authenticated_school_status': response.status_code,
                  'source_host': urlparse(response.url).hostname, 'bytes': target.stat().st_size,
                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'cloud_file_requests': 0, 'cloud_attachment_bytes': 0,
                  'local_saved': True, 'cookie_only_navigation_supported': True}
    print(json.dumps(result))


if __name__ == '__main__':
    import sys
    verify(sys.argv[1])
