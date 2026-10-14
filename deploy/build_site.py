"""Produce a static page for the exact platforms in the frozen manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def build(installer, linux, output):
    installer, output = Path(installer), Path(output)
    linux = Path(linux) if linux else None
    record = json.loads((installer.parent / 'installer.json').read_text())
    manifest = json.loads((installer.parent / 'release-manifest.json').read_text())
    if record.get('source_commit') != manifest['source_commit'] or record['version'] != manifest['version']:
        raise ValueError('Site source identity mismatch')
    if installer.name != record['filename'] or hashlib.sha256(installer.read_bytes()).hexdigest() != record['sha256']:
        raise ValueError('Installer manifest mismatch')
    linux_record=next((a for a in manifest['assets'] if a['filename']=='linux-x86_64.zip'), None)
    if bool(linux) != bool(linux_record):
        raise ValueError('Site platform scope differs from manifest')
    linux_entry=''
    if linux:
        with zipfile.ZipFile(linux) as archive:
            release = json.loads(archive.read('release.json'))
        if (release['version'] != record['version'] or release['platform'] != 'linux-x86_64'
                or release.get('source_commit') != manifest['source_commit']
                or hashlib.sha256(linux.read_bytes()).hexdigest() != linux_record['sha256']):
            raise ValueError('Platform version mismatch')
        linux_entry='<div class="entry"><div><h3>自己的 Linux 服务器</h3><p>在自己的常开服务器运行，由自己管理访问和账号。</p></div><a href="/campus-updates/targets/'+linux_record['sha256']+'.linux-x86_64.zip" download>下载 Linux 分发包</a></div>'
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    replacements = {'__VERSION__': record['version'], '__INSTALLER__': installer.name,
                    '__INSTALLER_SHA256__': record['sha256'],
                    '__LINUX_ENTRY__': linux_entry}
    page = (repo / 'site/index.html').read_text(encoding='utf-8')
    for key, value in replacements.items():
        page = page.replace(key, value)
    (output / 'index.html').write_text(page, encoding='utf-8')
    for name in ('site.css', 'site.js'):
        shutil.copyfile(repo / 'site' / name, output / name)
    shutil.copyfile(repo / 'tokens.css', output / 'tokens.css')
    (output / 'downloads').mkdir()
    shutil.copyfile(installer, output / 'downloads' / installer.name)
    (output / 'release.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    shutil.copyfile(installer.parent / 'release-manifest.json', output / 'release-manifest.json')
    shutil.copyfile(installer.parent / 'release-manifest.json', output / 'downloads/release-manifest.json')
    print(json.dumps({'version': record['version'], 'output': str(output), 'static_only': True}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--installer', required=True, type=Path)
    p.add_argument('--linux', type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    build(a.installer, a.linux, a.output)
