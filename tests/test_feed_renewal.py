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
    with pytest.raises((ValueError, UnsignedMetadataError, LengthOrHashMismatchError)):
        server.publish(stage / "metadata", public, expected_timestamp=expected, renew_only=True)
    assert {p.name: p.read_bytes() for p in (public / "metadata").iterdir()} == before


@pytest.fixture
def online(feed, tmp_path):
    renewal, publisher, server, public, keys = feed
    online_module = importlib.import_module("server_renewal")
    key_file = tmp_path / "online.json"
    key_file.write_text(json.dumps({r: keys[r].private_bytes.decode() for r in ("snapshot", "timestamp")}))
    messages = []

    def check(bootstrap, cache, *, expected=None):
        client = renewal.UpdateManager(cache)._client()
        client.refresh()
        assert hashlib.sha256((cache / "updates/metadata/timestamp.json").read_bytes()).hexdigest() == expected
        return {name: client.get_targetinfo(name).unrecognized_fields["custom"]["version"] for name in renewal.PLATFORMS}

    worker = online_module.Renewal(public, tmp_path / "state", publisher.TRUST_ROOT.read_bytes(), key_file, {},
                                  check=check, notify=lambda config, subject, body: messages.append((subject, body)))
    return worker, messages, feed


def test_online_renewal_keeps_offline_authorization_and_supports_later_release(online):
    worker, _, (_, publisher, _, public, _) = online
    targets = (public / "metadata/targets.json").read_bytes()
    root = (public / "metadata/root.json").read_bytes()
    assert Metadata.from_bytes(targets).signed.expires == Metadata.from_bytes(root).signed.expires
    assert worker.run(force=True)["status"] == "renewed"
    assert worker.run(force=True)["status"] == "renewed"
    assert (public / "metadata/targets.json").read_bytes() == targets
    assert (public / "metadata/root.json").read_bytes() == root
    old_timestamp = Metadata.from_file(str(public / "metadata/timestamp.json")).signed.version
    publisher.publish(public, [])
    assert Metadata.from_file(str(public / "metadata/timestamp.json")).signed.version > old_timestamp
    assert worker.run()["client_verified"] is True


def test_online_retry_after_readback_failure_does_not_sign_again(online, monkeypatch):
    worker, messages, (_, _, _, public, _) = online
    check = worker.check
    worker.check = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline"))
    assert worker.run(force=True)["status"] == "error"
    after = (public / "metadata/timestamp.json").read_bytes()
    worker.check = check
    monkeypatch.setattr(importlib.import_module("server_renewal"), "load_online_keys", forbid_signing)
    result = worker.run()
    assert result["status"] == "renewed"
    assert result["client_verified"] is True
    assert (public / "metadata/timestamp.json").read_bytes() == after
    assert len(messages) == 2
    assert "失败" in messages[0][0] and "恢复" in messages[1][0]


def test_alert_delivery_failure_retries_without_resigning(online, monkeypatch):
    worker, messages, (_, _, _, public, _) = online
    send = worker.notify
    worker.notify = lambda *a: (_ for _ in ()).throw(ConnectionError("smtp offline"))
    first = worker.run(force=True, test_alert=True)
    assert first["pending_alerts"] == 1 and first["mail_error"] == "ConnectionError"
    after = (public / "metadata/timestamp.json").read_bytes()
    worker.notify = send
    monkeypatch.setattr(importlib.import_module("server_renewal"), "load_online_keys", forbid_signing)
    assert worker.run()["pending_alerts"] == 0
    assert (public / "metadata/timestamp.json").read_bytes() == after
    assert len(messages) == 1


def test_failure_alerts_are_deduplicated_and_reminded_daily(online):
    worker, messages, _ = online
    worker.check = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline"))
    now = datetime.now(timezone.utc)
    worker.run(now=now, force=True)
    worker.run(now=now + timedelta(hours=1))
    assert len(messages) == 1
    worker.run(now=now + timedelta(days=1, seconds=1))
    assert len(messages) == 2


def test_server_rejects_offline_key_export_and_wrong_role(online):
    worker, _, (_, _, _, _, keys) = online
    module = importlib.import_module("server_renewal")
    payload = json.loads(worker.keys.read_text())
    payload["targets"] = keys["targets"].private_bytes.decode()
    worker.keys.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="only snapshot"):
        module.load_online_keys(worker.keys, worker.bootstrap)
    payload.pop("targets")
    payload["timestamp"] = keys["targets"].private_bytes.decode()
    worker.keys.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="not authorized"):
        module.load_online_keys(worker.keys, worker.bootstrap)


def test_expired_online_metadata_recovers_without_targets_key(online):
    worker, _, (_, _, _, public, keys) = online
    ts = Metadata.from_file(str(public / "metadata/timestamp.json"))
    snap = Metadata.from_file(str(public / "metadata/1.snapshot.json"))
    snap.signed.expires = datetime.now(timezone.utc) - timedelta(days=1)
    snap.sign(keys["snapshot"])
    snap.to_file(str(public / "metadata/1.snapshot.json"))
    ts.signed.snapshot_meta = importlib.import_module("tuf.api.metadata").MetaFile.from_data(1, snap.to_bytes(), ["sha256"])
    ts.signed.expires = datetime.now(timezone.utc) - timedelta(days=1)
    ts.sign(keys["timestamp"])
    ts.to_file(str(public / "metadata/timestamp.json"))
    assert worker.run()["client_verified"] is True


def test_offline_expiry_has_no_online_bypass_and_warns_90_days_early(online):
    worker, messages, (_, _, _, public, _) = online
    assert worker.run(force=True)["client_verified"] is True
    before = (public / "metadata/timestamp.json").read_bytes()
    roles = importlib.import_module("feed_metadata").verified_feed(
        lambda n: (public / "metadata" / n).read_bytes(), worker.bootstrap)[1]
    now = roles["targets"].signed.expires - timedelta(days=89)
    state = worker.load()
    worker.deadlines(state, roles, now)
    worker.flush(state, now)
    assert len(messages) == 2  # root and targets require offline maintenance
    expired = worker.run(now=roles["targets"].signed.expires + timedelta(days=1))
    assert expired["status"] == "error"
    assert (public / "metadata/timestamp.json").read_bytes() == before


def test_online_mirror_detects_signed_rollback(online):
    worker, _, (_, _, _, public, _) = online
    originals = {p.name: p.read_bytes() for p in (public / "metadata").iterdir()}
    worker.run(force=True)
    for name, blob in originals.items():
        (public / "metadata" / name).write_bytes(blob)
    result = worker.run()
    assert result["status"] == "error"
    assert "older" in result["detail"]


def test_offline_publication_refreshes_stale_mirror_after_online_renewals(online, tmp_path):
    worker, _, (renewal, publisher, server, public, _) = online
    mirror = tmp_path / "offline-mirror"
    blobs, _ = renewal.verified_feed(renewal.get_public)
    renewal.save_metadata(mirror / "metadata", blobs)
    worker.run(force=True)
    worker.run(force=True)
    publisher.sync_public(mirror)
    assert (mirror / "metadata/timestamp.json").read_bytes() == (public / "metadata/timestamp.json").read_bytes()
    before = Metadata.from_file(str(public / "metadata/timestamp.json")).signed.version
    publisher.publish(mirror, [])
    server.publish(mirror / "metadata", public)
    assert Metadata.from_file(str(public / "metadata/timestamp.json")).signed.version > before
    assert worker.run()["client_verified"] is True


def test_independent_startup_alert_retries_transport_and_marks_recovery(online):
    worker, messages, _ = online
    module = importlib.import_module("renewal_failure_alert")
    worker.state_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    def offline(*a):
        raise ConnectionError("SMTP offline")
    failed = module.alert(worker.state_dir, {}, now=now, send=offline)
    assert failed["pending_alerts"] == 1
    sent = []
    result = module.alert(worker.state_dir, {}, now=now + timedelta(hours=1),
                          send=lambda config, subject, body: sent.append(subject))
    assert result["pending_alerts"] == 0 and len(sent) == 1
    module.alert(worker.state_dir, {}, now=now + timedelta(hours=2),
                 send=lambda config, subject, body: sent.append(subject))
    assert len(sent) == 1
    assert worker.run()["client_verified"]
    assert len(messages) == 1 and "恢复" in messages[0][0]


def test_onfailure_does_not_duplicate_an_already_handled_failure(online):
    worker, messages, _ = online
    module = importlib.import_module("renewal_failure_alert")
    worker.check = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline"))
    assert worker.run()["status"] == "error"
    assert module.alert(worker.state_dir, {}, send=forbid_signing)["already_handled"] is True
    assert len(messages) == 1
