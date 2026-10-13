"""Build identity shared by the running app and the release verifier."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from . import __version__


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_identity(directory=None):
    if directory is None:
        if not getattr(sys, 'frozen', False):
            return {'version': __version__, 'source_commit': None, 'payload_sha256': None}
        directory = Path(sys.executable).parent
    value = json.loads((Path(directory) / 'release.json').read_text(encoding='utf-8'))
    return {key: value.get(key) for key in ('version', 'platform', 'source_commit', 'payload_sha256')}


def verify_payload(directory, expected):
    """Verify actual installed files, not just a version string or receipt."""
    directory = Path(directory).resolve()
    identity = read_identity(directory)
    for key in ('version', 'source_commit', 'payload_sha256'):
        if not expected.get(key) or identity.get(key) != expected[key]:
            raise ValueError('Installed build identity differs: ' + key)
    index = directory / 'release-files.json'
    if sha256(index) != identity['payload_sha256']:
        raise ValueError('Installed file inventory differs')
    files = json.loads(index.read_text(encoding='utf-8'))
    if not files or not any(name in files for name in ('campus-client', 'campus-client.exe')):
        raise ValueError('Build inventory lacks executable')
    for name, record in files.items():
        parts = PurePosixPath(name).parts
        path = directory.joinpath(*parts)
        if (not parts or '\\' in name or ':' in name or '..' in parts
                or PurePosixPath(name).is_absolute() or path.is_symlink()
                or not path.resolve().is_relative_to(directory)):
            raise ValueError('Unsafe build inventory path')
        if path.stat().st_size != record['size'] or sha256(path) != record['sha256']:
            raise ValueError('Installed payload differs: ' + name)
    if not re.fullmatch('[a-f0-9]{40}', identity['source_commit']):
        raise ValueError('Missing source commit')
    return dict(identity, verified_files=len(files))
