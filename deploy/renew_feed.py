"""Renew only the existing public releases, using the publisher's local keys.

Safe to run daily: no signing unless metadata has <= 14 days remaining.
It uses neither campus credentials nor the personal dashboard API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import requests
from tuf.api.metadata import Metadata

import publish_release
from feed_metadata import verified_feed as read_feed, reject_rollback
from sustech_dashboard.updates import RELEASE_URL, TRUST_ROOT, UpdateManager

REPOSITORY = Path("D:/Artifacts/SUSTechCampusDashboard/feed")
REPORT = Path("D:/AppData/SUSTechCampusPublisher/renewal-status.json")
SSH_HOST = "ubuntu@124.221.144.155"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15"]
PLATFORMS = {"windows-x86_64.zip", "linux-x86_64.zip"}


def get_public(name):
    with requests.get(RELEASE_URL + "/metadata/" + name, timeout=30, stream=True,
                      headers={"Cache-Control": "no-cache"}) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise ValueError("Update metadata exceeds size limit")
        return bytes(data)


def verified_feed(fetch, *, bootstrap=None, now=None):
    """Authenticate the chain even when expired, so renewal can recover it.

    Only this publisher permits expired non-root metadata; normal clients still
    enforce expiry through TUF. The root remains pinned and must be unexpired.
    """
    return read_feed(fetch, bootstrap if bootstrap is not None else TRUST_ROOT.read_bytes(), now=now)


def needs_renewal(roles, now, days=14):
    return any(item.signed.expires <= now + timedelta(days=days) for item in roles.values())


def save_metadata(directory, blobs):
    directory.mkdir(parents=True, exist_ok=True)
    # Like the public feed, advance the local timestamp last.
    for name in sorted(blobs, key=lambda name: name == "timestamp.json"):
        temporary = directory / (name + ".new")
        temporary.write_bytes(blobs[name])
        temporary.replace(directory / name)


def run_command(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {result.stderr.strip()[-1500:]}")
    return result.stdout.strip()


def upload(metadata, previous_timestamp):
    # A private, freshly allocated staging directory; only public JSON + code.
    remote = run_command(["ssh", *SSH_OPTIONS, SSH_HOST, "mktemp -d /tmp/sustech-renew-XXXXXXXX"])
    if not re.fullmatch(r"/tmp/sustech-renew-[A-Za-z0-9]{8}", remote):
        raise ValueError("Unexpected remote staging path")
    files = [str(p) for p in metadata.glob("*.json")]
    files.append(str(Path(__file__).with_name("publish_feed.py")))
    files.append(str(Path(__file__).with_name("feed_metadata.py")))
    run_command(["scp", *SSH_OPTIONS, *files, SSH_HOST + ":" + remote + "/"])
    expected = hashlib.sha256(previous_timestamp).hexdigest()
    run_command(["ssh", *SSH_OPTIONS, SSH_HOST,
        f"sudo -n /opt/sustech-campus-renewal/venv/bin/python {remote}/publish_feed.py {remote} --renew-only --expected-timestamp {expected}"])
    # Tiny public staging files are retained for troubleshooting; no keys uploaded.


def client_check(cache):
    manager = UpdateManager(cache)
    client = manager._client()
    client.refresh()
    releases = {}
    for name in sorted(PLATFORMS):
        target = client.get_targetinfo(name)
        if target is None:
            raise ValueError("Client cannot see a platform release")
        releases[name] = target.unrecognized_fields["custom"]["version"]
    return releases


def renew(repository=REPOSITORY, *, force=False, days=14):
    now = datetime.now(timezone.utc)
    public, roles = verified_feed(get_public, now=now)
    local = repository / "metadata"
    if (local / "timestamp.json").exists():
        _, previous = verified_feed(lambda name: (local / name).read_bytes(), now=now)
        reject_rollback(roles, previous)
    repository.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="renewal-", dir=repository.parent) as scratch:
        work = Path(scratch)
        if force or needs_renewal(roles, now, days):
            staged = work / "feed"
            save_metadata(staged / "metadata", public)
            publish_release.publish(staged, [])
            candidate, signed = verified_feed(lambda name: (staged / "metadata" / name).read_bytes())
            if signed["targets"].signed.targets != roles["targets"].signed.targets:
                raise ValueError("Renewal changed release packages")
            upload(staged / "metadata", public["timestamp.json"])
            public, roles = verified_feed(get_public)
            if public["timestamp.json"] != candidate["timestamp.json"]:
                raise ValueError("Public readback differs from the signed renewal")
            status = "renewed"
        else:
            status = "healthy"
        releases = client_check(work / "client")
        save_metadata(local, public)
    return {"status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
            "metadata_version": roles["timestamp"].signed.version,
            "expires_at": roles["timestamp"].signed.expires.isoformat(),
            "renew_before_days": days, "client_verified": True, "releases": releases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--force", action="store_true", help="Renew once regardless of remaining days")
    args = parser.parse_args()
    try:
        result = renew(args.repository, force=args.force)
    except Exception as exc:
        result = {"status": "error", "checked_at": datetime.now(timezone.utc).isoformat(),
                  "error": type(exc).__name__, "detail": str(exc)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".new")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.report)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
