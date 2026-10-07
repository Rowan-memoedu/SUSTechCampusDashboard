"""Promote staged public archives under the same lock as timestamp publication."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil

from publish_feed import publish


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', type=Path)
    p.add_argument('--expected-timestamp', required=True)
    a = p.parse_args()
    root = Path('/var/www/sustech-campus-updates')
    with (root / '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if hashlib.sha256((root / 'metadata/timestamp.json').read_bytes()).hexdigest() != a.expected_timestamp:
            raise ValueError('Publication changed; prepare again')
        targets = json.loads((a.stage / 'targets.json').read_text())['signed']['targets']
        previous = {}
        for name, record in targets.items():
            if name not in {'windows-x86_64.zip', 'linux-x86_64.zip'}:
                raise ValueError('Unknown archive')
            source = a.stage / name
            with source.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != record['hashes']['sha256']:
                    raise ValueError('Staged package differs')
            old = root / 'targets' / name
            with old.open('rb') as stream:
                old_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            previous[name] = old.with_name(old_digest + '.' + name)
            if not previous[name].is_file():
                raise ValueError('Previous package recovery missing')
        try:
            for name in targets:
                candidate = root / 'targets' / (name + '.new')
                shutil.copyfile(a.stage / name, candidate)
                candidate.chmod(0o644)
                candidate.replace(root / 'targets' / name)
            result = publish(a.stage, root, expected_timestamp=a.expected_timestamp)
        except Exception:
            # Before timestamp changes, older clients still use immutable hashes.
            for name, old in previous.items():
                candidate = root / 'targets' / (name + '.restore')
                shutil.copyfile(old, candidate)
                candidate.chmod(0o644)
                candidate.replace(root / 'targets' / name)
            raise
        print(json.dumps(result))


if __name__ == '__main__':
    main()
