import os
import subprocess

import pytest

from sustech_dashboard.dpapi_store import _restrict_directory
from sustech_dashboard.windows_storage import private_directory_acl


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows directory security")


def test_existing_private_directory_does_not_reapply_acl(monkeypatch, tmp_path):
    folder = tmp_path / "私密目录 带空格"
    folder.mkdir()
    _restrict_directory(folder)
    assert private_directory_acl(folder)
    retained = folder / "retained.txt"
    retained.write_bytes(b"existing user data")
    def unexpected(*args, **kwargs):
        pytest.fail("A verified private ACL must not launch another permission command")
    monkeypatch.setattr(subprocess, "check_output", unexpected)
    monkeypatch.setattr(subprocess, "run", unexpected)
    _restrict_directory(folder)
    assert retained.read_bytes() == b"existing user data"


def test_inherited_permissions_are_checked_and_repaired(tmp_path):
    folder = tmp_path / "another directory"
    folder.mkdir()
    assert not private_directory_acl(folder)
    _restrict_directory(folder)
    assert private_directory_acl(folder)
    subprocess.run(["icacls", str(folder), "/inheritance:e"], check=True,
                   capture_output=True, timeout=120)
    assert not private_directory_acl(folder)
    _restrict_directory(folder)
    assert private_directory_acl(folder)
