"""Produce a static release directory only after both packages agree on a version."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def build(installer, linux, output):
    installer, linux, output = Path(installer), Path(linux), Path(output)
    record = json.loads((installer.parent / 'installer.json').read_text())
    if installer.name != record['filename'] or hashlib.sha256(installer.read_bytes()).hexdigest() != record['sha256']:
        raise ValueError('Installer manifest mismatch')
    with zipfile.ZipFile(linux) as archive:
        release = json.loads(archive.read('release.json'))
    if release['version'] != record['version'] or release['platform'] != 'linux-x86_64':
        raise ValueError('Platform version mismatch')
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    replacements = {'__VERSION__': record['version'], '__INSTALLER__': installer.name,
                    '__INSTALLER_SHA256__': record['sha256'],
                    '__LINUX_URL__': '/campus-updates/targets/' + hashlib.sha256(linux.read_bytes()).hexdigest() + '.linux-x86_64.zip'}
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
    print(json.dumps({'version': record['version'], 'output': str(output), 'static_only': True}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--installer', required=True, type=Path)
    p.add_argument('--linux', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    build(a.installer, a.linux, a.output)
