"""Shared Windows client / headless server lifecycle; no remote control plane."""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .paths import DATA_ROOT, prepare_private_directory
from .core import load_json, save_json
from .locking import exclusive_file, InstanceBusy


def owner_token():
    path = DATA_ROOT / "browser-token"
    if not path.exists():
        with path.open("x", encoding="ascii") as handle:
            handle.write(secrets.token_urlsafe(48))
        if os.name != "nt":
            path.chmod(0o600)
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 40:
        raise RuntimeError("本机访问令牌损坏")
    return value


class Runtime:
    def __init__(self):
        from .authentication import load_owner_credentials
        from .updates import UpdateManager
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.configured = threading.Event()
        self.sync_requested = threading.Event()
        self.configure_lock = threading.Lock()
        self.guard = threading.Condition()
        self.active_writes = 0
        self.draining = False
        self.restart = False
        self.shutdown = False
        self.token = owner_token()
        self.health_token = os.environ.get("SUSTECH_HEALTH_TOKEN", secrets.token_urlsafe(32))
        self.update = UpdateManager()
        self.workers = []
        self.print_worker = None
        self.credential_error = False
        try:
            if load_owner_credentials():
                self.configured.set()
        except Exception:
            self.credential_error = True

    def start_workers(self):
        from .app import _sync_loop, _materials_loop, CLOUD
        from .download_agent import local_agent
        def after_login(fn):
            while not self.stop.is_set():
                if self.configured.wait(1):
                    if fn is _sync_loop:
                        fn(self.stop, self.configured, self.sync_requested)
                    elif fn is _materials_loop:
                        fn(self.stop, self.configured)
                    else:
                        fn(self.stop)
                    return
        functions = [_sync_loop, _materials_loop]
        if not CLOUD or os.environ.get("SUSTECH_DOWNLOAD_MODE") == "local":
            functions.append(local_agent)
        for fn in functions:
            worker = threading.Thread(target=after_login, args=(fn,), daemon=True)
            self.workers.append(worker)
            worker.start()
        self.start_print_agent()
        def versions():
            while not self.stop.wait(30):
                self.update.check()
                if self.stop.wait(24 * 3600):
                    return
        from .execution import hosted
        if not hosted():
            threading.Thread(target=versions, daemon=True, name="version-check").start()

    def request_sync(self):
        self.sync_requested.set()

    def start_print_agent(self):
        from .execution import mode
        from .download_agent import personal_pair_available
        if mode() != 'local' or not personal_pair_available():return
        if self.print_worker and self.print_worker.is_alive():return
        from .print_agent import run
        def after_login():
            while not self.stop.is_set():
                if self.configured.wait(1):
                    run(self.stop)
                    return
        self.print_worker = threading.Thread(target=after_login, daemon=True, name='campus-print-agent')
        self.workers.append(self.print_worker)
        self.print_worker.start()

    def begin_install(self):
        from .execution import hosted
        if hosted():
            raise ValueError('托管空间由维护者统一更新')
        if not os.environ.get("SUSTECH_MANAGED_RUNTIME"):
            raise ValueError("源码运行请使用发布包启动后安装更新")
        if not self.configure_lock.acquire(False):
            raise ValueError("已有登录或更新操作在进行")
        def install():
            try:
                self.update.stage()
                with self.guard:
                    self.draining = True
                self.update.state.update(state='waiting', message='更新已校验，等待正在进行的提交、下载和回执完成')
                self.stop.set()
                # Let downloads, one-shot school writes and their acknowledgements finish.
                for worker in self.workers:
                    worker.join()
                with self.guard:
                    self.guard.wait_for(lambda: self.active_writes == 0)
                self.update.state.update(state='restarting', message='正在切换版本，页面将自动重新连接')
                self.restart = True
            except Exception:
                pass  # UpdateManager retains a sanitized, visible failure.
            finally:
                self.configure_lock.release()
        threading.Thread(target=install, daemon=True, name="install-update").start()


def backend(port=18765):
    from .execution import hosted
    if hosted():
        raise ValueError('运营者托管已退役；请使用本机或用户自有服务器模式')
    from waitress import create_server
    from .app import create_app
    prepare_private_directory()
    with exclusive_file(DATA_ROOT / "backend.lock"):
        runtime = Runtime()
        app = create_app(runtime)
        server = create_server(app, host="127.0.0.1", port=port, threads=8,
                               clear_untrusted_proxy_headers=False, max_request_body_size=256*1024*1024)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        runtime.ready.set()
        runtime.start_workers()
        quitting = threading.Event()
        def shutdown(*_):
            quitting.set()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            while thread.is_alive() and not quitting.wait(.25) and not runtime.restart and not runtime.shutdown:
                pass
        finally:
            runtime.draining = True
            runtime.stop.set()
            for worker in runtime.workers:
                worker.join(timeout=5)
            server.close()
        return 75 if runtime.restart else 0


def wait_ready(child, port, health_token, version, timeout=180):
    import requests
    session = requests.Session()
    session.trust_env = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and child.poll() is None:
        try:
            result = session.get(f"http://127.0.0.1:{port}/_health", headers={"X-Instance-Health": health_token}, timeout=1)
            if result.ok and result.json() == {"version": version, "ready": True}:
                return True
        except (requests.RequestException, ValueError):
            pass
        time.sleep(.25)
    return False


def child_command(executable, port):
    if getattr(sys, "frozen", False) or Path(executable) != Path(sys.executable):
        return [str(executable), "--backend", "--port", str(port)]
    return [str(executable), "-m", "sustech_dashboard.client", "--backend", "--port", str(port)]


def wait_existing(port, token, timeout=185):
    """Prove the listener owns our token before handing a browser the fragment."""
    import requests
    challenge = secrets.token_hex(32)
    proof = hmac.new(token.encode(), challenge.encode(), hashlib.sha256).hexdigest()
    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        session.trust_env = False
        while time.monotonic() < deadline:
            try:
                reply = session.get(f'http://127.0.0.1:{port}/_instance',
                    headers={'X-Instance-Challenge': challenge}, timeout=2, allow_redirects=False)
                result = reply.json()
                if reply.ok and result.get('ready') is True and hmac.compare_digest(result.get('proof', ''), proof):
                    return True
            except (requests.RequestException, ValueError, TypeError):
                pass
            time.sleep(.25)
    return False


def supervise(port=18765, open_browser=True, local_only=True):
    from .updates import release_executable, valid_version
    from .protocol import installation_owner
    prepare_private_directory()
    url = f"http://127.0.0.1:{port}/#access={owner_token()}"
    try:
        lock = exclusive_file(DATA_ROOT / "client.lock")
        lock.__enter__()
    except InstanceBusy:
        if open_browser and wait_existing(port, owner_token()):
            webbrowser.open(url)
            return 0
        if not open_browser:
            return 0
        raise RuntimeError('本机组件尚未就绪或端口被占用，请稍后重新打开面板')
    child = None
    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    try:
        base = DATA_ROOT / "updates"
        current = load_json(base / "current.json", {})
        fallback = sys.executable
        # A newer installer must not launch a stale version from an earlier
        # installation. Keep its disk pointer as recovery evidence; use the
        # bundled executable as this supervisor's rollback baseline.
        if current and valid_version(current['version']) < valid_version(__version__):
            current = {}
        pending = load_json(base / "pending.json", {})
        if pending and valid_version(pending['version']) < valid_version(__version__):
            save_json(base / 'last-result.json', {'state': 'superseded', 'version': __version__})
            (base / 'pending.json').unlink(missing_ok=True)
            pending = {}
        selected = pending or current
        restarting = selected != current
        while True:
            candidate = release_executable(base, selected, fallback)
            version = selected.get("version", __version__)
            health = secrets.token_urlsafe(32)
            env = dict(os.environ, SUSTECH_HEALTH_TOKEN=health, SUSTECH_MANAGED_RUNTIME="1", PYINSTALLER_RESET_ENVIRONMENT="1",
                       SUSTECH_INSTALL_ROOT=installation_owner())
            try:
                child = subprocess.Popen(child_command(candidate, port), env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                healthy = wait_ready(child, port, health, version)
            except OSError:
                child = None
                healthy = False
            if not healthy:
                if child is not None and child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
                if restarting:
                    save_json(base / "last-result.json", {"state": "rolled_back", "failed_version": version})
                    selected = current
                    restarting = False
                    (base / "pending.json").unlink(missing_ok=True)
                    continue
                if current and (base / "previous.json").exists():
                    previous = load_json(base / "previous.json", {})
                    if previous != current:
                        save_json(base / "last-result.json", {"state": "rolled_back", "failed_version": version})
                        save_json(base / "current.json", previous)
                        selected = current = previous
                        continue
                raise RuntimeError("校园面板未能启动，请检查本机日志")
            if restarting:
                save_json(base / "previous.json", current)
                save_json(base / "current.json", selected)
                save_json(base / "last-result.json", {"state": "installed", "version": version})
                (base / "pending.json").unlink(missing_ok=True)
                current = selected
                restarting = False
            if open_browser:
                webbrowser.open(url)
                open_browser = False
            code = child.wait()
            if code != 75:
                return code
            selected = load_json(base / "pending.json", {})
            if not selected:
                raise RuntimeError("缺少已验证的更新包")
            restarting = True
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=30)
        lock.__exit__(None, None, None)
