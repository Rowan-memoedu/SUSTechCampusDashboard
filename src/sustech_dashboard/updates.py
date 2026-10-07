"""TUF verified releases. No campus session or account data enters this client."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from packaging.version import Version

from . import __version__
from .core import save_json, load_json
from .paths import DATA_ROOT

RELEASE_URL = "https://124.221.144.155/campus-updates"
TRUST_ROOT = Path(__file__).with_name("update-root.json")
SCHEMA = 1


def platform_tag():
    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64"}:
        raise RuntimeError("此发布通道暂不支持当前处理器")
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform == "linux":
        return "linux-x86_64"
    raise RuntimeError("此发布通道暂不支持当前系统")


def executable_name():
    return "campus-client.exe" if os.name == "nt" else "campus-client"


def valid_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("发布版本格式无效")
    return Version(value)


def extract_release(archive, destination, release):
    """Only files below a new staging directory; no links, devices or escapes."""
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > 20000 or sum(i.file_size for i in infos) > 4 * 1024**3:
                raise ValueError("更新包超过解压限制")
            seen = set()
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                if (not path.parts or path.is_absolute() or "\\" in name or ":" in name
                        or any(p in {".", ".."} or p.endswith((" ", ".")) for p in path.parts)
                        or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in path.parts)):
                    raise ValueError("更新包路径无效")
                key = str(path).casefold()
                if key in seen:
                    raise ValueError("更新包包含重复路径")
                seen.add(key)
                mode = info.external_attr >> 16
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("更新包不允许包含链接或设备")
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("更新包路径越界")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("xb") as sink:
                    shutil.copyfileobj(source, sink)
                if os.name != "nt":
                    target.chmod(0o755 if mode & 0o111 else 0o644)
        manifest = json.loads((destination / "release.json").read_text(encoding="utf-8"))
        for field in ("version", "platform", "schema", "protocol"):
            if manifest.get(field) != release.get(field):
                raise ValueError("更新包与签名版本信息不一致")
        if not (destination / executable_name()).is_file():
            raise ValueError("更新包缺少启动程序")
    except Exception:
        # This is our newly created, bounded staging directory, never user data.
        shutil.rmtree(destination)
        raise


class UpdateManager:
    def __init__(self, root=DATA_ROOT, *, feed=RELEASE_URL, bootstrap=None, fetcher=None):
        parsed = urlparse(feed)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("更新源必须是无账号参数的 HTTPS 地址")
        self.base = Path(root) / "updates"
        self.feed = feed.rstrip("/")
        self.bootstrap = bootstrap
        self.fetcher = fetcher
        self.lock = threading.Lock()
        self.state = {"current": __version__, "state": "idle", "message": "尚未检查更新", "available": False}

    def _client(self):
        from tuf.ngclient import Updater
        from tuf.ngclient.config import UpdaterConfig
        metadata, downloads = self.base / "metadata", self.base / "downloads"
        metadata.mkdir(parents=True, exist_ok=True)
        downloads.mkdir(parents=True, exist_ok=True)
        class PortableUpdater(Updater):
            def _update_root_symlink(self):
                # TUF's root.json is a convenience alias. Windows standard users
                # cannot create symlinks. Copy the already-verified history entry;
                # verification still starts at the immutable embedded bootstrap.
                if os.name == "nt":
                    source = Path(self._dir) / "root_history" / f"{self._trusted_set.root.version}.root.json"
                    temporary = Path(self._dir) / "root.json.tmp"
                    temporary.write_bytes(source.read_bytes())
                    temporary.replace(Path(self._dir) / "root.json")
                else:
                    super()._update_root_symlink()
        return PortableUpdater(str(metadata), self.feed + "/metadata/", str(downloads), self.feed + "/targets/",
                       bootstrap=self.bootstrap if self.bootstrap is not None else TRUST_ROOT.read_bytes(),
                       fetcher=self.fetcher, config=UpdaterConfig(max_root_rotations=32))

    def _target(self):
        client = self._client()
        client.refresh()
        target = client.get_targetinfo(platform_tag() + ".zip")
        if target is None or target.length > 500 * 1024**2:
            raise ValueError("当前系统没有可用的更新包")
        release = target.unrecognized_fields.get("custom", {})
        valid_version(release.get("version"))
        if release.get("platform") != platform_tag() or release.get("schema") != SCHEMA or release.get("protocol") != 1:
            raise ValueError("更新包需要不同的数据格式或启动器，请手动升级")
        return client, target, release

    def check(self):
        if not self.lock.acquire(False):
            return dict(self.state)
        try:
            self.state.update(state="checking", message="正在校验版本签名")
            _, _, release = self._target()
            available = valid_version(release["version"]) > valid_version(__version__)
            self.state.update(state="available" if available else "current", latest=release["version"],
                              available=available, message="有新版本可安装" if available else "已是当前版本",
                              notes=str(release.get('notes', ''))[:2000],
                              checked_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            self.state.update(state="error", available=False,
                              message="更新检查失败，现有版本继续运行；请稍后重试", error=type(exc).__name__)
        finally:
            self.lock.release()
        return dict(self.state)

    def stage(self):
        with self.lock:
            self.state.update(state="downloading", message="正在下载并校验更新")
            try:
                client, target, release = self._target()
                if valid_version(release["version"]) <= valid_version(__version__):
                    raise ValueError("没有可安装的新版本")
                archive = client.download_target(target)
                directory = release["version"] + "-" + target.hashes["sha256"][:16]
                destination = self.base / "releases" / directory
                if destination.exists():
                    # Restage into a distinct directory, never trust old extracted contents.
                    import secrets
                    directory += "-" + secrets.token_hex(4)
                    destination = self.base / "releases" / directory
                extract_release(archive, destination, release)
                pending = {"directory": directory, "version": release["version"], "schema": SCHEMA}
                save_json(self.base / "pending.json", pending)
                self.state.update(state="ready", message="更新已校验，正在等待当前操作结束后重启")
                return pending
            except Exception as exc:
                self.state.update(state="error", message="更新未安装，原版本继续运行", error=type(exc).__name__)
                raise


def release_executable(base, selection, fallback):
    if not selection:
        return Path(fallback)
    name = selection.get("directory", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+-[a-f0-9]{16}(?:-[a-f0-9]{8})?", name):
        raise ValueError("本机版本指针无效")
    folder = (Path(base) / "releases" / name).resolve()
    if not folder.is_relative_to((Path(base) / "releases").resolve()):
        raise ValueError("本机版本路径无效")
    return folder / executable_name()
