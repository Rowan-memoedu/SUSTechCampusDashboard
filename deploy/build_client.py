"""Build on each target OS. Never copy data, checkout or publisher keys."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import hashlib

from sustech_dashboard import __version__
from sustech_dashboard.updates import platform_tag, TRUST_ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source-commit", help="Frozen, clean source commit for distribution")
    parser.add_argument("--notes", default="默认在本机使用；新增安装与唤起、首页更新提醒，保留个人资料和操作记录。")
    args = parser.parse_args()
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    assert TRUST_ROOT.is_file(), "Initialize release trust root before building"
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    if args.source_commit:
        if args.source_commit != commit:
            raise ValueError('Build checkout differs from requested source commit')
        subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=repo, check=True,
                       stdout=subprocess.DEVNULL)
        untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard',
            '--', 'src', 'deploy', 'site'], cwd=repo, text=True)
        if untracked.strip():
            raise ValueError('Untracked source cannot enter a release')
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "campus-client", "--distpath", str(args.output), "--workpath", str(args.work / "build"),
               "--specpath", str(args.work), "--collect-all", "sustech_dashboard",
               "--collect-submodules", "sustech_survival", "--collect-data", "sustech_survival",
               "--recursive-copy-metadata", "sustech_survival", "--collect-submodules", "tuf",
               "--collect-all", "playwright", "--collect-all", "pypdf",
               "--collect-submodules", "securesystemslib", "--collect-data", "certifi",
               "--exclude-module", "sustech_dashboard.monitor_remote", "--exclude-module", "sustech_dashboard.monitor_cli"]
    if os.name == "nt":
        command += ["--noconsole"]
    command += [str(repo / "deploy/freeze_entry.py")]
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(args.work / "cache"))
    subprocess.run(command, check=True, env=env)
    bundle = args.output / "campus-client"
    release = {"version": __version__, "platform": platform_tag(), "schema": 1, "protocol": 1,
               "source_commit": commit if args.source_commit else None, "notes": args.notes[:2000]}
    shutil.copyfile(repo / "CLIENT-README.md", bundle / "使用说明.md")
    shutil.copyfile(repo / "THIRD-PARTY-NOTICES.md", bundle / "THIRD-PARTY-NOTICES.md")
    for distribution in importlib.metadata.distributions():
        for item in distribution.files or []:
            if not any(word in Path(item).name.lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file():
                dest = bundle / "licenses" / distribution.metadata["Name"] / Path(item).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
    (bundle / "dependencies.json").write_text(json.dumps(sorted(
        [{"name": d.metadata["Name"], "version": d.version} for d in importlib.metadata.distributions()],
        key=lambda d: d["name"].lower()), indent=2), encoding="utf-8")
    inventory = {}
    for path in sorted(bundle.rglob('*')):
        if path.is_file() and path.name not in {'release.json', 'release-files.json'}:
            with path.open('rb') as stream:
                inventory[path.relative_to(bundle).as_posix()] = {
                    'size': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    index = json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode('utf-8')
    (bundle / 'release-files.json').write_bytes(index)
    release['payload_sha256'] = hashlib.sha256(index).hexdigest()
    (bundle / 'release.json').write_text(json.dumps(release), encoding='utf-8')
    archive = args.output / (platform_tag() + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                target.write(path, str(path.relative_to(bundle)))
    print(json.dumps({"archive": str(archive), **release}), flush=True)


if __name__ == "__main__":
    main()
