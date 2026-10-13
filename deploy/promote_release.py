"""Publish the signed feed and matching static site under the existing server lock."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

from feed_metadata import verified_feed
from publish_feed import publish


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.publishing')
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    temporary.replace(destination)


def promote(stage, expected_timestamp, root=Path('/var/www/sustech-campus-updates'),
            site=Path('/var/www/sustech-campus-site')):
    manifest = json.loads((stage/'site/release-manifest.json').read_text())
    trusted = (root/'metadata/root.json').read_bytes()
    _, incoming = verified_feed(lambda n: (stage/'metadata'/n).read_bytes(), trusted)
    assets = {a['filename']:a for a in manifest['assets']}
    targets = incoming['targets'].signed.targets
    for name, target in targets.items():
        identity = target.unrecognized_fields['custom']
        if (identity['version'] != manifest['version'] or identity.get('source_commit') != manifest['source_commit']
                or target.hashes['sha256'] != assets[name]['sha256']
                or digest(stage/name) != assets[name]['sha256']):
            raise ValueError('Staged feed identity mismatch')
    installer = next(a for a in manifest['assets'] if a['filename'].endswith('-setup.exe'))
    if digest(stage/'site/downloads'/installer['filename']) != installer['sha256']:
        raise ValueError('Staged installer mismatch')
    with (root/'.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _, current = verified_feed(lambda n: (root/'metadata'/n).read_bytes(), trusted)
        already = all(current['targets'].signed.targets[n].hashes == t.hashes for n,t in targets.items())
        if not already:
            if digest(root/'metadata/timestamp.json') != expected_timestamp:
                raise ValueError('Feed advanced; refresh metadata and retry the same artifacts')
            for name, target in targets.items():
                archive = root/'targets'/name
                previous = archive.with_name(digest(archive)+'.'+name)
                if not previous.is_file():
                    raise ValueError('Previous artifact recovery missing')
                replace(stage/name, archive)
            publish(stage/'metadata', root, expected_timestamp=expected_timestamp)
        # TUF has committed. A later site failure is resumable, never downgrade it.
        for name in (installer['filename'], 'release-manifest.json'):
            source = stage/'site/downloads'/name
            destination = site/'downloads'/name
            if destination.exists() and name.endswith('.exe') and digest(source) != digest(destination):
                raise ValueError('Existing installer version is immutable')
            replace(source, destination)
        for name in ('site.css','site.js','tokens.css','release.json','index.html'):
            replace(stage/'site'/name, site/name)
    return {'version':manifest['version'], 'source_commit':manifest['source_commit'],
            'feed_and_site_published':True}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',type=Path)
    p.add_argument('--expected-timestamp',required=True)
    a=p.parse_args()
    print(json.dumps(promote(a.stage,a.expected_timestamp)))
