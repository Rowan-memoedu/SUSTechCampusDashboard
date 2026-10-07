"""Remove this release's bounded server staging only after verified retirement."""
import json
from pathlib import Path
import shutil

from retire_hosted import WORK, load, run


def main():
    result = load('retirement-result.json')
    if not result.get('retired') or not result.get('D_recovery_retained'):
        raise ValueError('Retirement not accepted')
    inventory = load('inventory.json')
    names = (
        'campus-local040-publish', 'campus-local040-migration.tar.gz', 'campus-local040-export',
        'campus-local040-build', 'campus-local040-source', 'campus-local040-export.tar.gz',
        'campus-local040-final-canary', 'campus-local040-package-canary', 'campus-local040-source.tar.gz',
        'campus-local040-extras.tar.gz', 'campus-local040-final-tests', 'campus-local040-logical-check.json',
        'campus-local040-linux', 'campus-local040-inventory.py', 'campus-local040-recovery.tar.gz',
        'campus-local040-tests', 'campus-local040-upgrade', 'campus-local040-nginx', 'campus-retirement-040')
    paths = [Path('/tmp') / name for name in names]
    for path in paths:
        if path.parent != Path('/tmp') or path.is_symlink() or path.resolve() != path:
            raise ValueError('Staging removal path differs')
    allocated = sum(int(run('du', '-s', '-B1', '--', str(p)).split()[0]) for p in paths if p.exists())
    removed = 0
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
            removed += 1
        elif path.exists():
            path.unlink()
            removed += 1
    for unit in inventory['units']:
        for line in unit['properties'].splitlines():
            if line.startswith('DropInPaths='):
                for value in line.split('=', 1)[1].split():
                    parent = Path(value).parent
                    if parent.parent == Path('/etc/systemd/system') and parent.name.endswith('.service.d') and parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
    print(json.dumps({'staging_removed': removed, 'staging_allocated_bytes_removed': allocated,
                      'D_recovery_retained': True, 'update_repository_preserved': True}), flush=True)


if __name__ == '__main__':
    main()
