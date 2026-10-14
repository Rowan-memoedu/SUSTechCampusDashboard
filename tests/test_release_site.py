import json
from pathlib import Path
import sys

from test_release_assets import bundle, release_assets

sys.path.insert(0,str(Path(__file__).parents[1]/'deploy'))
from build_site import build


def test_windows_page_launches_registered_app_and_excludes_new_linux_package(tmp_path):
    bundle(tmp_path/'windows-x86_64.zip')
    installer=tmp_path/'SUSTechCampusDashboard-1.2.3-windows-x86_64-setup.exe'
    installer.write_bytes(b'fixture')
    receipt={'version':'1.2.3','filename':installer.name,'size':7,'source_commit':'a'*40,
             'payload_sha256':release_assets.audit_archive(tmp_path/'windows-x86_64.zip','1.2.3','windows-x86_64')['payload_sha256'],
             'sha256':release_assets.digest(installer)}
    (tmp_path/'installer.json').write_text(json.dumps(receipt))
    release_assets.prepare(tmp_path,'1.2.3','a'*40,('windows-x86_64',))
    output=tmp_path/'site';build(installer,None,output)
    from bs4 import BeautifulSoup
    page=BeautifulSoup((output/'index.html').read_text(encoding='utf-8'),'html.parser')
    launch=page.select_one('#open-local')
    assert launch['href']=='sustech-campus://open' and 'target' not in launch.attrs
    assert not page.select('a[href*="linux-x86_64.zip"]')
    assert json.loads((output/'downloads/release-manifest.json').read_text())['source_commit']=='a'*40
