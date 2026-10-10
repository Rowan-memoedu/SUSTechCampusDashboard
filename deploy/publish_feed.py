"""Atomic publication, shared by offline releases and restricted online renewal."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import argparse
from feed_metadata import verified_feed, reject_rollback


def publish(source, root, *, expected_timestamp=None, renew_only=False, bootstrap=None):
    """Caller holds the server lock; commit timestamp only after all checks."""
    trusted = bootstrap if bootstrap is not None else (root / "metadata" / "root.json").read_bytes()
    _, signed = verified_feed(lambda n: (source / n).read_bytes(), trusted)
    metadata = json.loads((source / "targets.json").read_text())
    incoming = json.loads((source / "timestamp.json").read_text())
    current_path = root / "metadata" / "timestamp.json"
    if current_path.exists():
        _, prior = verified_feed(lambda n: (root / "metadata" / n).read_bytes(), trusted)
        reject_rollback(signed, prior)
        current_bytes = current_path.read_bytes()
        current = json.loads(current_bytes)
        if expected_timestamp and hashlib.sha256(current_bytes).hexdigest() != expected_timestamp:
            raise ValueError("Feed changed during renewal; retry from the public feed")
        if incoming["signed"]["version"] <= current["signed"]["version"]:
            raise ValueError("Metadata version must increase")
    elif expected_timestamp:
        raise ValueError("Expected an existing feed")
    if renew_only:
        if not current_path.exists():
            raise ValueError("Renewal requires an existing feed")
        # Read the committed version, not a possibly interrupted upload's alias.
        snapshot_version = current["signed"]["meta"]["snapshot.json"]["version"]
        snapshot = json.loads((root / "metadata" / f"{snapshot_version}.snapshot.json").read_text())
        target_version = snapshot["signed"]["meta"]["targets.json"]["version"]
        previous = json.loads((root / "metadata" / f"{target_version}.targets.json").read_text())
        if metadata["signed"]["targets"] != previous["signed"]["targets"]:
            raise ValueError("Renewal cannot change release packages")
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
    # Versioned files are immutable, including during interrupted publications.
    for path in files:
        existing = root / "metadata" / path.name
        if path.name[0].isdigit() and existing.exists() and existing.read_bytes() != path.read_bytes():
            raise ValueError("Versioned metadata already exists with different content")
    for path in files:
        temporary = root / "metadata" / (path.name + ".new")
        shutil.copyfile(path, temporary)
        temporary.chmod(0o644)
        temporary.replace(root / "metadata" / path.name)
    return {"metadata_version": metadata["signed"]["version"], "platforms": list(metadata["signed"]["targets"])}


if __name__ == "__main__":
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--expected-timestamp")
    parser.add_argument("--renew-only", action="store_true")
    args = parser.parse_args()
    root = Path("/var/www/sustech-campus-updates")
    with (root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(publish(args.source.resolve(), root,
            expected_timestamp=args.expected_timestamp, renew_only=args.renew_only,
            bootstrap=Path('/etc/sustech-campus-renewal/trust-root.json').read_bytes())))
