"""Offline TUF signing. Private keys remain DPAPI-encrypted on publisher PC."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import zipfile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root, Targets, Snapshot, Timestamp, TargetFile, MetaFile

from sustech_dashboard.dpapi_store import protect_password, unprotect_password, _restrict_directory
from sustech_dashboard.updates import TRUST_ROOT, valid_version

KEY_DIR = Path("D:/AppData/SUSTechCampusPublisher")


def keys(initialize=False):
    path = KEY_DIR / "release-keys.dpapi.json"
    if not path.exists():
        if not initialize:
            raise RuntimeError("Release keys have not been initialized")
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        _restrict_directory(KEY_DIR)
        payload = {role: CryptoSigner.generate_ed25519().private_bytes.decode() for role in ("root", "targets", "snapshot", "timestamp")}
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"encrypted": protect_password(json.dumps(payload))}, handle)
    payload = json.loads(unprotect_password(json.loads(path.read_text(encoding="utf-8"))["encrypted"]))
    return {role: CryptoSigner(load_pem_private_key(pem.encode(), password=None)) for role, pem in payload.items()}


def initialize():
    signers = keys(True)
    if TRUST_ROOT.exists():
        root = Metadata.from_file(str(TRUST_ROOT))
        assert signers["root"].public_key.keyid in root.signed.keys
        return
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365*5)))
    for role, signer in signers.items():
        root.signed.add_key(signer.public_key, role)
    root.sign(signers["root"])
    root.to_file(str(TRUST_ROOT))
    print("Public trust root initialized; private keys remain on this Windows account.")


def publish(repository, archives):
    signers = keys()
    metadata, targets = repository / "metadata", repository / "targets"
    metadata.mkdir(parents=True, exist_ok=True)
    targets.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    old = metadata / "targets.json"
    target_metadata = Metadata.from_file(str(old)) if old.exists() else Metadata(Targets(version=1))
    if old.exists():
        target_metadata.signed.version += 1
    # Package authorization stays offline and lasts until the pinned root's
    # maintenance deadline. Online snapshot/timestamp renewal cannot extend it.
    root = Metadata.from_file(str(TRUST_ROOT))
    if root.signed.is_expired(now):
        raise ValueError("Trust root expired; rotate it before publishing")
    target_metadata.signed.expires = root.signed.expires
    for archive in archives:
        with zipfile.ZipFile(archive) as zipped:
            release = json.loads(zipped.read("release.json"))
        valid_version(release["version"])
        name = release["platform"] + ".zip"
        target = TargetFile.from_file(name, str(archive))
        previous = target_metadata.signed.targets.get(name)
        if previous:
            previous_version = previous.unrecognized_fields["custom"]["version"]
            if valid_version(release["version"]) < valid_version(previous_version):
                raise ValueError("Cannot publish a downgrade")
            if release["version"] == previous_version and previous.hashes != target.hashes:
                raise ValueError("Existing version is immutable; bump version before rebuilding")
        target.unrecognized_fields["custom"] = release
        target_metadata.signed.targets[name] = target
        shutil.copyfile(archive, targets / (target.hashes["sha256"] + "." + name))
        shutil.copyfile(archive, targets / name)
    target_metadata.sign(signers["targets"])
    version = target_metadata.signed.version
    target_metadata.to_file(str(metadata / f"{version}.targets.json"))
    target_metadata.to_file(str(old))
    # The server advances these versions independently of targets.json.
    snapshot_old = metadata / "snapshot.json"
    snapshot_version = max(version, Metadata.from_file(str(snapshot_old)).signed.version + 1) if snapshot_old.exists() else version
    timestamp_old = metadata / "timestamp.json"
    timestamp_version = max(version, Metadata.from_file(str(timestamp_old)).signed.version + 1) if timestamp_old.exists() else version
    snapshot = Metadata(Snapshot(version=snapshot_version, expires=now + timedelta(days=30), meta={
        "targets.json": MetaFile.from_data(version, old.read_bytes(), ["sha256"])}))
    snapshot.sign(signers["snapshot"])
    snapshot.to_file(str(metadata / f"{snapshot_version}.snapshot.json"))
    snapshot.to_file(str(metadata / "snapshot.json"))
    timestamp = Metadata(Timestamp(version=timestamp_version, expires=now + timedelta(days=30),
        snapshot_meta=MetaFile.from_data(snapshot_version, (metadata / "snapshot.json").read_bytes(), ["sha256"])))
    timestamp.sign(signers["timestamp"])
    timestamp.to_file(str(metadata / "timestamp.json"))
    shutil.copyfile(TRUST_ROOT, metadata / "1.root.json")
    shutil.copyfile(TRUST_ROOT, metadata / "root.json")
    print(json.dumps({"repository": str(repository), "metadata_version": version,
                      "platforms": list(target_metadata.signed.targets)}, ensure_ascii=False))


def sync_public(repository):
    """Refresh independent online versions before preparing a new release.

Keep publish() offline/testable; the production CLI must start from the current
    authenticated public feed, not a mirror last written months earlier.
    """
    from renew_feed import get_public, verified_feed, save_metadata
    from feed_metadata import reject_rollback
    blobs, roles = verified_feed(get_public)
    local = repository / "metadata"
    if (local / "timestamp.json").exists():
        _, previous = verified_feed(lambda n: (local / n).read_bytes())
        reject_rollback(roles, previous)
    save_metadata(local, blobs)
    return roles


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--repository", type=Path)
    parser.add_argument("archives", type=Path, nargs="*")
    args = parser.parse_args()
    if args.init:
        initialize()
    else:
        if not args.repository:
            parser.error("--repository is required")
        if (args.repository / "metadata" / "timestamp.json").exists():
            sync_public(args.repository)
        publish(args.repository, args.archives)
