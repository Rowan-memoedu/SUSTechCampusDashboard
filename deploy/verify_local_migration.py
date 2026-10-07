"""Check a private preflight export against the current Windows owner, then stage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--local', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--recovery', type=Path, help='Commit the verified stage and retain a separate recovery directory')
    args = p.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != args.sha256:
        raise ValueError('Export archive checksum differs')
    from sustech_dashboard.paths import prepare_private_directory
    prepare_private_directory(args.output)
    extracted = args.output / 'export'
    if extracted.exists():
        raise ValueError('Use a fresh preflight output')
    prepare_private_directory(extracted)
    with tarfile.open(args.archive) as archive:
        archive.extractall(extracted, filter='data')
    args.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='migration-auth-', dir=args.cache) as temporary:
        prepare_private_directory(Path(temporary))
        from sustech_dashboard import authentication, dpapi_store
        authentication.DATA_ROOT = Path(temporary)
        dpapi_store.CREDENTIALS_PATH = args.local / 'credentials.dpapi.json'
        authentication.set_credentials(*dpapi_store.load_credentials())
        from sustech_dashboard.provider import Blackboard
        Blackboard()
        owner = authentication.current_credentials()[0]
        settings = json.loads((args.local / 'instance.json').read_text()) if (args.local / 'instance.json').exists() else {}
        default = 'D:/download' if os.name == 'nt' and Path('D:/').is_dir() else str(Path.home() / 'Downloads/SUSTech')
        from migrate_local import stage
        result = stage(extracted, args.local, args.output / 'staged', owner, Path(settings.get('download_root', default)))
        result.update(owner_verified_with_school=True, source_unchanged=True, applied=False)
        if args.recovery:
            from migrate_local import commit, digest
            preserved = {name: digest(args.local / name) for name in ('credentials.dpapi.json', 'instance.json', 'pair.dpapi.json')
                         if (args.local / name).exists()}
            applied = commit(args.output / 'staged', args.local, args.recovery, owner)
            if any(digest(args.local / name) != expected for name, expected in preserved.items()):
                raise ValueError('A protected local file changed')
            result.update(applied=applied['verified'], source_unchanged=False, protected_local_files_unchanged=True)
        (args.output / 'result.json').write_text(json.dumps(result, indent=2))
        authentication.clear_credentials()
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        frames = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                  for frame in traceback.extract_tb(exc.__traceback__)]
        print(json.dumps({'verification_failed': type(exc).__name__, 'applied': None, 'review_required': True,
                          'errno': getattr(exc, 'errno', None), 'frames': frames}))
        raise SystemExit(1) from None
