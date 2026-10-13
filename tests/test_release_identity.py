import hashlib
import json
from pathlib import Path

import pytest
from sustech_dashboard.release_identity import verify_payload


def test_identity_requires_actual_files_not_only_matching_version(tmp_path):
    binary=tmp_path/'campus-client.exe'; binary.write_bytes(b'real canary')
    index=json.dumps({'campus-client.exe':{'size':11,'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}}).encode()
    identity={'version':'1.2.3','source_commit':'b'*40,'payload_sha256':hashlib.sha256(index).hexdigest()}
    (tmp_path/'release-files.json').write_bytes(index)
    (tmp_path/'release.json').write_text(json.dumps(identity))
    assert verify_payload(tmp_path,identity)['verified_files']==1
    with pytest.raises(ValueError,match='source_commit'):
        verify_payload(tmp_path,dict(identity,source_commit='a'*40))
    binary.write_bytes(b'fake canary')
    with pytest.raises(ValueError,match='payload differs'):
        verify_payload(tmp_path,identity)


def test_equal_or_ahead_version_does_not_hide_build_mismatch(tmp_path, monkeypatch):
    from test_updates import Repository, manager
    from sustech_dashboard import updates, release_identity, __version__
    repo=Repository(); repo.publish(version='0.1.0')
    assert manager(tmp_path,repo).check()['state']=='ahead'
    repo.publish(version=__version__,number=2)
    client=manager(tmp_path,repo)
    old=client._target
    def target():
        tuf,target,release=old()
        return tuf,target,dict(release,source_commit='a'*40,payload_sha256='b'*64)
    monkeypatch.setattr(client,'_target',target)
    monkeypatch.setattr(release_identity,'read_identity',lambda:{'source_commit':'c'*40,'payload_sha256':'b'*64})
    assert client.check()['state']=='mismatch'
    monkeypatch.setattr(release_identity,'read_identity',lambda:{'source_commit':'a'*40,'payload_sha256':'b'*64})
    assert client.check()['state']=='current'
