"""Server-side atomic publication of public metadata; no signing keys here."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1]).resolve()
root = Path("/var/www/sustech-campus-updates")
metadata = json.loads((source / "targets.json").read_text())
for name, target in metadata["signed"]["targets"].items():
    if name not in {"windows-x86_64.zip", "linux-x86_64.zip"}:
        raise ValueError("Unknown platform target")
    archive = root / "targets" / name
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != target["hashes"]["sha256"] or archive.stat().st_size != target["length"]:
        raise ValueError("Uploaded artifact differs from signed release")
    hashed = archive.with_name(digest + "." + name)
    if not hashed.exists():
        os.link(archive, hashed)

files = sorted(p for p in source.glob("*.json") if p.name != "timestamp.json")
files.append(source / "timestamp.json")
for path in files:
    temporary = root / "metadata" / (path.name + ".new")
    shutil.copyfile(path, temporary)
    temporary.chmod(0o644)
    temporary.replace(root / "metadata" / path.name)
print(json.dumps({"metadata_version": metadata["signed"]["version"], "platforms": list(metadata["signed"]["targets"])}))
