"""Create a source deployment artifact from an allowlist; no runtime data."""
import hashlib
import json
from pathlib import Path
import tarfile


def pack(output):
    root = Path(__file__).resolve().parents[1]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for folder in ('src', 'deploy', 'tests', 'docs'):
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and
                     p.suffix in {'.py', '.html', '.js', '.cjs', '.css', '.md', '.json', '.conf', '.service', '.ps1'} and '__pycache__' not in p.parts)
    files.extend(root/p for p in ('pyproject.toml', 'README.md', 'CLIENT-README.md', 'UPDATING.md', 'THIRD-PARTY-NOTICES.md', 'requirements-build.txt'))
    manifest = {}
    with tarfile.open(output, 'w:gz') as archive:
        for path in sorted(files):
            name = path.relative_to(root).as_posix()
            archive.add(path, arcname=name)
            manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'archive': str(output), 'files': len(files), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    import sys
    pack(sys.argv[1])
