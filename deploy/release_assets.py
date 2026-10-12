"""Prepare an explicit public asset list; never upload a checkout or local data.

This gate detects known runtime data paths, not every possible secret in binaries.
Hashes identify downloads; the existing TUF signer remains the update authority.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


PRIVATE_NAMES = {
    '.env', 'credentials.dpapi.json', 'credentials.json', 'snapshot.json',
    'cookies.json', 'pairing.json', 'publisher-keys.json', 'local-token',
}
PRIVATE_DIRS = {'userdata', 'user-data', 'publisher-keys', '.git', '.ssh'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit_archive(path, version, platform):
    seen = set()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name = entry.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            folded = name.rstrip('/').casefold()
            if (not parts or name.startswith('/') or '..' in parts
                    or ':' in name or folded in seen
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('unsafe or duplicate archive path')
            seen.add(folded)
            lowered = {part.casefold() for part in parts}
            leaf = parts[-1].casefold()
            if (lowered & PRIVATE_DIRS or leaf in PRIVATE_NAMES
                    or leaf.startswith('.env.')
                    or leaf.endswith(('.sqlite', '.sqlite3', '.db', '.dpapi'))):
                raise ValueError('private runtime data in archive')
        release = json.loads(archive.read('release.json'))
        if (release.get('version') != version or release.get('platform') != platform
                or release.get('schema') != 1 or release.get('protocol') != 1):
            raise ValueError('release metadata mismatch')
    return {'filename': Path(path).name, 'size': Path(path).stat().st_size,
            'sha256': digest(path)}


def prepare(directory, version, commit):
    directory = Path(directory)
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('a stable semantic version is required')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('full source commit required')
    assets = [audit_archive(directory / (platform + '.zip'), version, platform)
              for platform in ('windows-x86_64', 'linux-x86_64')]
    installer_name = f'SUSTechCampusDashboard-{version}-windows-x86_64-setup.exe'
    installer = directory / installer_name
    receipt = json.loads((directory / 'installer.json').read_text(encoding='utf-8'))
    installer_asset = {'filename': installer_name, 'size': installer.stat().st_size,
                       'sha256': digest(installer)}
    if (receipt.get('version') != version or receipt.get('filename') != installer_name
            or receipt.get('size') != installer_asset['size']
            or receipt.get('sha256') != installer_asset['sha256']):
        raise ValueError('installer receipt mismatch')
    assets.append(installer_asset)
    manifest = {'schema': 1, 'version': version, 'source_commit': commit,
                'authenticode_signed': receipt.get('authenticode_signed') is True,
                'assets': assets}
    (directory / 'release-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (directory / 'SHA256SUMS').write_text(''.join(
        f"{asset['sha256']}  {asset['filename']}\n" for asset in assets), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory, args.version, args.commit)), flush=True)


if __name__ == '__main__':
    main()
