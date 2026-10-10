"""Shared authenticated TUF chain reader; expired online metadata is recoverable.

Only maintainers may use this reader to recover expiry. End users use Updater,
which continues to enforce all metadata expiration dates.
"""
from datetime import datetime, timezone
from tuf.api.metadata import Metadata

PLATFORMS = {"windows-x86_64.zip", "linux-x86_64.zip"}


def verified_feed(fetch, bootstrap, *, now=None):
    now = now or datetime.now(timezone.utc)
    root = Metadata.from_bytes(bootstrap)
    root.signed.verify_delegate("root", root.signed_bytes, root.signatures)
    if root.signed.is_expired(now):
        raise ValueError("Trust root expired; offline maintainer action required")
    blobs, roles = {}, {}
    for role in ("timestamp", "snapshot", "targets"):
        if role == "timestamp":
            name = "timestamp.json"
        else:
            info = (roles["timestamp"].signed.snapshot_meta if role == "snapshot"
                    else roles["snapshot"].signed.meta["targets.json"])
            name = f"{info.version}.{role}.json"
        blob = fetch(name)
        if len(blob) > 1024 * 1024:
            raise ValueError("Update metadata exceeds size limit")
        if role != "timestamp":
            info.verify_length_and_hashes(blob)
        item = Metadata.from_bytes(blob)
        if item.signed.type != role:
            raise ValueError("Incorrect metadata role")
        root.signed.verify_delegate(role, item.signed_bytes, item.signatures)
        if role != "timestamp" and item.signed.version != info.version:
            raise ValueError("Metadata version differs from signed chain")
        roles[role], blobs[role + ".json"] = item, blob
    if set(roles["targets"].signed.targets) != PLATFORMS:
        raise ValueError("Feed must authorize both existing platforms")
    blobs["root.json"] = bootstrap
    blobs[f"{root.signed.version}.root.json"] = bootstrap
    for role in ("snapshot", "targets"):
        blobs[f"{roles[role].signed.version}.{role}.json"] = blobs[role + ".json"]
    return blobs, roles


def reject_rollback(current, previous):
    for role in current:
        a, b = current[role], previous[role]
        if a.signed.version < b.signed.version:
            raise ValueError("Public feed is older than the publisher mirror")
        if a.signed.version == b.signed.version and a.signed_bytes != b.signed_bytes:
            raise ValueError("Same metadata version has different content")
