import os
from types import SimpleNamespace

import pytest

from sustech_dashboard import paths
from sustech_dashboard.windows_storage import owned_by_current_user


@pytest.fixture
def windows_paths(monkeypatch, tmp_path):
    drive = tmp_path / "drive-D"
    legacy = drive / "AppData/SUSTechCampusDashboard"
    home = tmp_path / "another user"
    local = home / "AppData/Local"
    real_path = type(tmp_path)

    class FixturePath(real_path):
        def __new__(cls, *parts):
            if parts and str(parts[0]) == "D:/":
                return drive
            if parts and str(parts[0]) == "D:/AppData/SUSTechCampusDashboard":
                return legacy
            if parts and str(parts[0]) == "D:/download":
                return drive / "download"
            return super().__new__(cls, *parts)

        @classmethod
        def home(cls):
            return home

    monkeypatch.setattr(paths, "Path", FixturePath)
    monkeypatch.setattr(paths, "os", SimpleNamespace(name="nt", environ={"LOCALAPPDATA": str(local)}))
    return drive, legacy, home, local


@pytest.mark.parametrize("drive_present", [False, True])
def test_new_user_defaults_do_not_depend_on_a_d_drive(monkeypatch, windows_paths, drive_present):
    drive, legacy, home, local = windows_paths
    if drive_present:
        drive.mkdir()
    monkeypatch.setattr(paths, "owned_by_current_user", lambda path: pytest.fail("No legacy owner to inspect"))
    root = paths.default_data_root()
    assert root == local / "SUSTechCampusDashboard"
    monkeypatch.setattr(paths, "DATA_ROOT", root)
    assert paths.download_root() == home / "Downloads/SUSTech"


def test_another_users_legacy_directory_is_not_adopted(monkeypatch, windows_paths):
    _, legacy, _, local = windows_paths
    legacy.mkdir(parents=True)
    monkeypatch.setattr(paths, "owned_by_current_user", lambda path: False)
    assert paths.default_data_root() == local / "SUSTechCampusDashboard"


def test_existing_owner_keeps_legacy_data_and_download_defaults(monkeypatch, windows_paths):
    drive, legacy, _, _ = windows_paths
    legacy.mkdir(parents=True)
    monkeypatch.setattr(paths, "owned_by_current_user", lambda path: path == legacy)
    assert paths.default_data_root() == legacy
    monkeypatch.setattr(paths, "DATA_ROOT", legacy)
    assert paths.download_root() == (drive / "download").resolve()


def test_windows_localappdata_absence_uses_current_home(monkeypatch, windows_paths):
    _, _, home, _ = windows_paths
    paths.os.environ.clear()
    assert paths.default_data_root() == home / "AppData/Local/SUSTechCampusDashboard"


@pytest.mark.skipif(os.name != "nt", reason="Windows security descriptor API")
def test_native_ownership_probe_accepts_current_user_and_rejects_system_directory(tmp_path):
    folder = tmp_path / "另一用户路径 验收"
    folder.mkdir()
    assert owned_by_current_user(folder)
    assert not owned_by_current_user(os.environ["SystemRoot"])
    assert not owned_by_current_user(folder / "does-not-exist")
