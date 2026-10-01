"""One owner, one process, one data directory on Windows or Linux."""
import os
from pathlib import Path


def default_data_root():
    if os.name == "nt":
        if Path("D:/").is_dir():
            return Path("D:/AppData/SUSTechCampusDashboard")
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "SUSTechCampusDashboard"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "sustech-campus-dashboard"


DATA_ROOT = Path(os.environ.get("SUSTECH_DASHBOARD_DATA_ROOT", default_data_root())).expanduser().resolve()


def download_root():
    import json
    settings = DATA_ROOT / "instance.json"
    config = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    fallback = Path("D:/download") if os.name == "nt" and Path("D:/").is_dir() else Path.home() / "Downloads/SUSTech"
    return Path(os.environ.get("SUSTECH_DOWNLOAD_ROOT", config.get("download_root", fallback))).expanduser().resolve()


def prepare_private_directory(path=DATA_ROOT):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        from .dpapi_store import _restrict_directory
        _restrict_directory(path)
    else:
        path.chmod(0o700)
