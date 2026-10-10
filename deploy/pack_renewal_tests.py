"""Package only public source/test files for independent Linux validation."""
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
output = Path("D:/Artifacts/SUSTechCampusDashboard/server-renewal-20261010")
output.mkdir(parents=True, exist_ok=True)
names = set(subprocess.run(["git", "ls-files"], cwd=root, check=True, text=True, capture_output=True).stdout.splitlines())
names.update(str(p.relative_to(root)).replace("\\", "/") for p in (root / "deploy").glob("*.py"))
with tarfile.open(output / "source-tests.tar.gz", "w:gz") as archive:
    for name in sorted(names):
        if name.startswith(("src/", "tests/", "deploy/")) or name in {"pyproject.toml", "README.md"}:
            path = root / name
            if path.is_file() and path.resolve().is_relative_to(root):
                archive.add(path, arcname=name, recursive=False)
print(output / "source-tests.tar.gz")
