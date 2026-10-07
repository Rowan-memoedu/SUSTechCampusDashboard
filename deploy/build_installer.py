"""Package the existing Windows runtime; TUF continues to distribute its ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def build(bundle, output, compiler):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    release = json.loads((bundle / 'release.json').read_text(encoding='utf-8'))
    if release['platform'] != 'windows-x86_64' or not (bundle / 'campus-client.exe').is_file():
        raise ValueError('A complete Windows build is required')
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), '/Qp', '/DBundleDir='+str(bundle),
        '/DAppVersion='+release['version'], '/O'+str(output),
        str(Path(__file__).with_name('client.iss'))], check=True)
    installer = output / f"SUSTechCampusDashboard-{release['version']}-windows-x86_64-setup.exe"
    record = {'filename': installer.name, 'version': release['version'],
              'size': installer.stat().st_size, 'sha256': hashlib.sha256(installer.read_bytes()).hexdigest(),
              'authenticode_signed': False, 'per_user': True, 'data_removed_on_uninstall': False}
    (output / 'installer.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record), flush=True)
    return installer


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compiler', required=True, type=Path)
    args = parser.parse_args()
    build(args.bundle, args.output, args.compiler)
