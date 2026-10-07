from datetime import datetime, timedelta, timezone
import io
import json
import stat
import zipfile

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, Root, Targets, Snapshot, Timestamp, TargetFile, MetaFile
from tuf.api.exceptions import DownloadHTTPError
from tuf.ngclient.fetcher import FetcherInterface

from sustech_dashboard import updates
from sustech_dashboard.core import load_json


class Repository(FetcherInterface):
    def __init__(self):
        self.files = {}
        self.requested = []
        self.keys = {r: CryptoSigner.generate_ed25519() for r in ("root", "targets", "snapshot", "timestamp")}
        root = Metadata(Root(expires=datetime.now(timezone.utc) + timedelta(days=365)))
        for role, signer in self.keys.items():
            root.signed.add_key(signer.public_key, role)
        root.sign(self.keys["root"])
        self.bootstrap = root.to_bytes()

    def _fetch(self, url):
        self.requested.append(url)
        name = url.removeprefix("https://updates.test/")
        if name not in self.files:
            raise DownloadHTTPError("not found", 404)
        yield self.files[name]

    def publish(self, version=None, number=1, expired=False, malicious=False):
        if version is None:
            from sustech_dashboard import __version__
            from packaging.version import Version
            current = Version(__version__)
            version = f'{current.major}.{current.minor + 1}.0'
        release = {"version": version, "platform": updates.platform_tag(), "schema": 1, "protocol": 1}
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as bundle:
            bundle.writestr("release.json", json.dumps(release))
            bundle.writestr(updates.executable_name(), b"fixture executable")
            if malicious:
                bundle.writestr("../credentials.dpapi.json", "overwrite")
        archive = memory.getvalue()
        name = updates.platform_tag() + ".zip"
        target = TargetFile.from_data(name, archive)
        target.unrecognized_fields["custom"] = release
        self.target_name = "targets/" + target.hashes["sha256"] + "." + name
        self.files[self.target_name] = archive
        expiry = datetime.now(timezone.utc) + timedelta(days=-1 if expired else 7)
        targets = Metadata(Targets(version=number, expires=expiry, targets={name: target}))
        targets.sign(self.keys["targets"])
        self.files[f"metadata/{number}.targets.json"] = targets.to_bytes()
        snapshot = Metadata(Snapshot(version=number, expires=expiry, meta={
            "targets.json": MetaFile.from_data(number, targets.to_bytes(), ["sha256"])}))
        snapshot.sign(self.keys["snapshot"])
        self.files[f"metadata/{number}.snapshot.json"] = snapshot.to_bytes()
        timestamp = Metadata(Timestamp(version=number, expires=expiry,
            snapshot_meta=MetaFile.from_data(number, snapshot.to_bytes(), ["sha256"])))
        timestamp.sign(self.keys["timestamp"])
        self.files["metadata/timestamp.json"] = timestamp.to_bytes()


def manager(tmp_path, repo):
    return updates.UpdateManager(tmp_path, feed="https://updates.test", bootstrap=repo.bootstrap, fetcher=repo)


def test_real_tuf_stage_keeps_account_state_and_downloads_untouched(tmp_path):
    repo = Repository()
    repo.publish()
    for name in ("credentials.dpapi.json", "attachments.json", "actions.sqlite3", "downloaded-files.json"):
        (tmp_path / name).write_bytes(b"private fixture never sent")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    client = manager(tmp_path, repo)
    assert client.check()["available"] is True
    pending = client.stage()
    assert load_json(client.base / "pending.json", {}) == pending
    assert updates.release_executable(client.base, pending, "unused").is_file()
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    assert all(u.startswith("https://updates.test/metadata/") or u.startswith("https://updates.test/targets/") for u in repo.requested)
    assert all("private" not in u for u in repo.requested)


@pytest.mark.parametrize("failure", ["signature", "expired", "hash", "path"])
def test_rejects_untrusted_or_unsafe_updates_without_pending_install(tmp_path, failure):
    repo = Repository()
    repo.publish(expired=failure == "expired", malicious=failure == "path")
    if failure == "signature":
        payload = json.loads(repo.files["metadata/timestamp.json"])
        payload["signed"]["version"] += 1
        repo.files["metadata/timestamp.json"] = json.dumps(payload).encode()
    if failure == "hash":
        repo.files[repo.target_name] += b"tampered"
    client = manager(tmp_path, repo)
    with pytest.raises(Exception):
        client.stage()
    assert not (client.base / "pending.json").exists()
    assert not (tmp_path / "credentials.dpapi.json").exists()


def test_replayed_metadata_and_signed_downgrades_rejected(tmp_path):
    repo = Repository()
    repo.publish(number=2)
    client = manager(tmp_path, repo)
    assert client.check()["available"]
    repo.publish(number=1)
    assert client.check()["state"] == "error"
    repo.publish(version="0.1.0", number=3)
    assert not client.check()["available"]
    with pytest.raises(ValueError):
        client.stage()


def test_links_and_case_collisions_rejected(tmp_path):
    for name, mode in [("link", stat.S_IFLNK | 0o777), ("../escape", stat.S_IFREG | 0o644)]:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, "target")
        data.seek(0)
        with pytest.raises(ValueError):
            updates.extract_release(data, tmp_path / "staged", {})
        assert not (tmp_path / "staged").exists()


def test_interrupted_pending_install_with_unlaunchable_binary_rolls_back(monkeypatch, tmp_path):
    from sustech_dashboard import runtime
    from sustech_dashboard.core import save_json
    monkeypatch.setattr(runtime, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "prepare_private_directory", lambda: None)
    pending = {"directory": "9.0.0-" + "a"*16, "version": "9.0.0", "schema": 1}
    save_json(tmp_path / "updates/pending.json", pending)
    (tmp_path / "account-fixture").write_bytes(b"keep")
    calls = []
    class Process:
        def wait(self, **kwargs): return 0
        def poll(self): return 0
    def spawn(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise OSError("bad executable")
        return Process()
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "wait_ready", lambda *args: True)
    assert runtime.supervise(18799, False) == 0
    assert len(calls) == 2
    assert load_json(tmp_path / "updates/last-result.json", {})["state"] == "rolled_back"
    assert not (tmp_path / "updates/pending.json").exists()
    assert (tmp_path / "account-fixture").read_bytes() == b"keep"


def test_new_installer_does_not_launch_older_current_pointer(monkeypatch, tmp_path):
    from sustech_dashboard import runtime, __version__
    from sustech_dashboard.core import save_json
    monkeypatch.setattr(runtime, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(runtime, 'prepare_private_directory', lambda: None)
    save_json(tmp_path / 'updates/current.json', {'directory': '0.1.0-' + 'a'*16, 'version': '0.1.0'})
    launched = []
    class Process:
        def wait(self, **kwargs): return 0
        def poll(self): return 0
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda command, **kwargs: (launched.append(command) or Process()))
    def ready(child, port, token, version):
        assert version == __version__
        return True
    monkeypatch.setattr(runtime, 'wait_ready', ready)
    assert runtime.supervise(18799, False) == 0
    assert launched[0][0] == runtime.sys.executable
