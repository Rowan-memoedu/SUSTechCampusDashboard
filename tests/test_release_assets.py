import importlib.util
import json
import hashlib
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    'release_assets', Path(__file__).parents[1] / 'deploy/release_assets.py')
release_assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_assets)


def bundle(path, platform='windows-x86_64', extra=None, version='1.2.3'):
    files = {'campus-client.exe': b'fixture executable',
             '_internal/sustech_dashboard/update_root.json': b'{"keys":{}}',
             '_internal/certifi/cacert.pem': b'public certificate'}
    index = json.dumps({n: {'size':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()}).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('release.json', json.dumps(
            {'version': version, 'platform': platform, 'schema': 1, 'protocol': 1,
             'source_commit': 'a'*40, 'payload_sha256': hashlib.sha256(index).hexdigest()}))
        archive.writestr('release-files.json', index)
        for name, data in files.items(): archive.writestr(name, data)
        if extra:
            archive.writestr(extra, 'fixture')


@pytest.mark.parametrize('name', ['../private.txt', '/private.txt', 'C:/private.txt',
                                 'data/snapshot.json', 'data/credentials.dpapi.json',
                                 '.git/config', '.env.local', 'data/cache.sqlite3',
                                 'release-keys.dpapi.json', 'browser-token'])
def test_rejects_unsafe_and_private_paths(tmp_path, name):
    path = tmp_path / 'client.zip'
    bundle(path, extra=name)
    with pytest.raises(ValueError):
        release_assets.audit_archive(path, '1.2.3', 'windows-x86_64')


def test_public_trust_root_allowed_and_version_verified(tmp_path):
    path = tmp_path / 'client.zip'
    bundle(path)
    assert len(release_assets.audit_archive(path, '1.2.3', 'windows-x86_64')['sha256']) == 64
    with pytest.raises(ValueError, match='metadata mismatch'):
        release_assets.audit_archive(path, '1.2.4', 'windows-x86_64')


def test_duplicate_and_symlink_rejected(tmp_path):
    path = tmp_path / 'client.zip'
    bundle(path)
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('Release.JSON', '{}')
    with pytest.raises(ValueError, match='duplicate'):
        release_assets.audit_archive(path, '1.2.3', 'windows-x86_64')
    bundle(path)
    with zipfile.ZipFile(path, 'a') as archive:
        entry = zipfile.ZipInfo('link')
        entry.external_attr = 0o120777 << 16
        archive.writestr(entry, 'outside')
    with pytest.raises(ValueError, match='unsafe'):
        release_assets.audit_archive(path, '1.2.3', 'windows-x86_64')


def test_allowlist_and_installer_hash(tmp_path):
    for platform in ('windows-x86_64', 'linux-x86_64'):
        bundle(tmp_path / (platform + '.zip'), platform)
    installer = tmp_path / 'SUSTechCampusDashboard-1.2.3-windows-x86_64-setup.exe'
    installer.write_bytes(b'fixture')
    receipt = {'version': '1.2.3', 'filename': installer.name, 'size': 7,
               'source_commit': 'a'*40,
               'payload_sha256': release_assets.audit_archive(tmp_path/'windows-x86_64.zip','1.2.3','windows-x86_64')['payload_sha256'],
               'sha256': release_assets.digest(installer), 'authenticode_signed': False}
    (tmp_path / 'installer.json').write_text(json.dumps(receipt))
    (tmp_path / 'private-log.txt').write_text('must not enter manifest')
    manifest = release_assets.prepare(tmp_path, '1.2.3', 'a' * 40)
    assert len(manifest['assets']) == 3
    assert 'private-log' not in (tmp_path / 'SHA256SUMS').read_text()
    assert manifest['authenticode_signed'] is False
    installer.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='receipt mismatch'):
        release_assets.prepare(tmp_path, '1.2.3', 'a' * 40)
