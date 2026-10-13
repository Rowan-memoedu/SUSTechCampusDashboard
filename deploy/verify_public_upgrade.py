"""Fetch the retained old public artifact and upgrade it through the original TUF root."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import requests
from sustech_dashboard.updates import platform_tag, RELEASE_URL, extract_release
from verify_upgrade import verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--previous-sha256',required=True)
    p.add_argument('--cache',required=True,type=Path)
    a=p.parse_args(); a.cache.mkdir(parents=True,exist_ok=True)
    platform=platform_tag(); s=requests.Session(); s.trust_env=False
    def get(url):
        r=s.get(url,timeout=120); r.raise_for_status(); return r
    manifest=get('https://github.com/Rowan-memoedu/SUSTechCampusDashboard/releases/download/v'+a.version+'/release-manifest.json').json()
    assert manifest['source_commit']==a.commit
    asset=next(x for x in manifest['assets'] if x['filename']==platform+'.zip')
    data=get(RELEASE_URL+'/targets/'+a.previous_sha256+'.'+platform+'.zip').content
    assert hashlib.sha256(data).hexdigest()==a.previous_sha256
    archive=a.cache/'previous.zip'; archive.write_bytes(data)
    with zipfile.ZipFile(archive) as z: release=json.loads(z.read('release.json'))
    bundle=a.cache/'previous'
    extract_release(archive,bundle,release)
    verify(bundle/('campus-client.exe' if platform.startswith('windows') else 'campus-client'),
           a.cache/'instances',a.version,a.commit,asset['sha256'])


if __name__=='__main__': main()
