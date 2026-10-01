"""Publisher automation: real signatures, unchanged packages and retry safety."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import zipfile

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root
from tuf.api.exceptions import DownloadHTTPError, LengthOrHashMismatchError, UnsignedMetadataError
from tuf.ngclient.fetcher import FetcherInterface

from sustech_dashboard.updates import UpdateManager


@pytest.fixture
def feed(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "deploy"))
    renewal = importlib.import_module("renew_feed")
    publisher = importlib.import_module("publish_release")
    server = importlib.import_module("publish_feed")
    keys = {r: CryptoSigner.generate_ed25519() for r in ("root", "targets", "snapshot", "timestamp")}
    root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
    for role, key in keys.items():
        root.signed.add_key(key.public_key, role)
    root.sign(keys["root"])
    trust = tmp_path / "trust.json"
    trust.write_bytes(root.to_bytes())
    monkeypatch.setattr(publisher, "TRUST_ROOT", trust)
    monkeypatch.setattr(renewal, "TRUST_ROOT", trust)
    monkeypatch.setattr(publisher, "keys", lambda: keys)
    public = tmp_path / "public"
    archives = []
    for platform in ("linux-x86_64", "windows-x86_64"):
        archive = tmp_path / (platform + ".zip")
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("release.json", json.dumps({"version": "1.2.3", "platform": platform,
                                                        "schema": 1, "protocol": 1}))
        archives.append(archive)
    publisher.publish(public, archives)
    monkeypatch.setattr(renewal, "get_public", lambda name: (public / "metadata" / name).read_bytes())

    class Fetcher(FetcherInterface):
        def _fetch(self, url):
            name = url.removeprefix(renewal.RELEASE_URL + "/")
            path = public / name
            if not path.is_file():
                raise DownloadHTTPError("missing", 404)
            yield path.read_bytes()

    monkeypatch.setattr(renewal, "UpdateManager", lambda cache: UpdateManager(
        cache, bootstrap=trust.read_bytes(), fetcher=Fetcher()))
    monkeypatch.setattr(renewal, "upload", lambda metadata, previous: server.publish(
        metadata, public, expected_timestamp=hashlib.sha256(previous).hexdigest(), renew_only=True))
    return renewal, publisher, server, public, keys


def forbid_signing(*args, **kwargs):
    raise AssertionError("Healthy feed must not decrypt signing keys")


def test_daily_check_skips_signing_and_verifies_both_clients(feed, tmp_path, monkeypatch):
    renewal, publisher, _, public, _ = feed
    before = (public / "metadata/timestamp.json").read_bytes()
    monkeypatch.setattr(publisher, "keys", forbid_signing)
    result = renewal.renew(tmp_path / "mirror")
    assert result["status"] == "healthy"
    assert result["client_verified"] is True
    assert set(result["releases"].values()) == {"1.2.3"}
    assert len(result["releases"]) == 2
    assert (public / "metadata/timestamp.json").read_bytes() == before


@pytest.mark.parametrize("remaining", [-2, 7, 14])
def test_due_or_expired_feed_renews_without_changing_packages(feed, tmp_path, remaining):
    renewal, _, _, public, keys = feed
    timestamp_path = public / "metadata/timestamp.json"
    timestamp = Metadata.from_file(str(timestamp_path))
    timestamp.signed.expires = datetime.now(timezone.utc) + timedelta(days=remaining)
    timestamp.sign(keys["timestamp"])
    timestamp.to_file(str(timestamp_path))
    before = {p.name: p.read_bytes() for p in (public / "targets").iterdir()}
    result = renewal.renew(tmp_path / "mirror")
    assert result["status"] == "renewed"
    assert result["metadata_version"] == 2
    assert datetime.fromisoformat(result["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=29)
    assert {p.name: p.read_bytes() for p in (public / "targets").iterdir()} == before
    assert renewal.renew(tmp_path / "mirror")["status"] == "healthy"


@pytest.mark.parametrize("failure", ["signature", "hash"])
def test_rejects_tampered_public_metadata_before_decrypting_keys(feed, tmp_path, monkeypatch, failure):
    renewal, publisher, _, public, _ = feed
    monkeypatch.setattr(publisher, "keys", forbid_signing)
    if failure == "signature":
        path = public / "metadata/timestamp.json"
        data = json.loads(path.read_bytes())
        data["signed"]["version"] += 1
        path.write_text(json.dumps(data))
    else:
        path = public / "metadata/1.snapshot.json"
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(UnsignedMetadataError if failure == "signature" else LengthOrHashMismatchError):
        renewal.renew(tmp_path / "mirror", force=True)
    assert not (tmp_path / "mirror").exists()


def test_public_rollback_is_rejected(feed, tmp_path):
    renewal, _, _, public, _ = feed
    original = {p.name: p.read_bytes() for p in (public / "metadata").iterdir()}
    mirror = tmp_path / "mirror"
    renewal.renew(mirror, force=True)
    for name, content in original.items():
        (public / "metadata" / name).write_bytes(content)
    with pytest.raises(ValueError, match="older"):
        renewal.renew(mirror)


def test_readback_failure_recovers_without_duplicate_signing(feed, tmp_path, monkeypatch):
    renewal, publisher, _, _, _ = feed
    mirror = tmp_path / "mirror"
    renewal.renew(mirror)
    with monkeypatch.context() as patch:
        patch.setattr(renewal, "client_check", lambda _: (_ for _ in ()).throw(ConnectionError("offline")))
        with pytest.raises(ConnectionError):
            renewal.renew(mirror, force=True)
    monkeypatch.setattr(publisher, "keys", forbid_signing)
    result = renewal.renew(mirror)
    assert result["status"] == "healthy"
    assert result["metadata_version"] == 2
    assert Metadata.from_file(str(mirror / "metadata/timestamp.json")).signed.version == 2


@pytest.mark.parametrize("failure", ["concurrent", "changed-target", "broken-package"])
def test_server_refuses_unsafe_publication_before_timestamp_commit(feed, tmp_path, failure):
    renewal, publisher, server, public, _ = feed
    stage = tmp_path / "stage"
    blobs, _ = renewal.verified_feed(renewal.get_public)
    renewal.save_metadata(stage / "metadata", blobs)
    publisher.publish(stage, [])
    expected = hashlib.sha256(blobs["timestamp.json"]).hexdigest()
    if failure == "concurrent":
        expected = "0" * 64
    elif failure == "changed-target":
        path = stage / "metadata/targets.json"
        data = json.loads(path.read_bytes())
        data["signed"]["targets"]["linux-x86_64.zip"]["custom"]["version"] = "2.0.0"
        path.write_text(json.dumps(data))
    else:
        (public / "targets/linux-x86_64.zip").write_bytes(b"broken")
    before = {p.name: p.read_bytes() for p in (public / "metadata").iterdir()}
    with pytest.raises(ValueError):
        server.publish(stage / "metadata", public, expected_timestamp=expected, renew_only=True)
    assert {p.name: p.read_bytes() for p in (public / "metadata").iterdir()} == before
