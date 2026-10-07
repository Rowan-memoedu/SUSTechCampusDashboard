"""Isolated rendered-page checks; no user browser profile or school requests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--node', required=True)
    p.add_argument('--playwright', required=True)
    p.add_argument('--chrome', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    a.cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='ui-fixture-', dir=a.cache))
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_EXECUTION_MODE='local', SUSTECH_REGISTER_PROTOCOL='0')
    for name in ('SUSTECH_CLOUD', 'SUSTECH_PUBLIC_HOST', 'CREDENTIALS_DIRECTORY'):
        os.environ.pop(name, None)
    from sustech_dashboard.paths import prepare_private_directory
    from sustech_dashboard.runtime import Runtime
    from sustech_dashboard.app import create_app
    from flask import send_from_directory
    from werkzeug.serving import make_server, WSGIRequestHandler
    prepare_private_directory()
    runtime = Runtime()
    runtime.configured.set()
    app = create_app(runtime)
    repo = Path(__file__).resolve().parents[1]
    # The site uses only local assets. Replace build placeholders for UI testing.
    @app.get('/release-preview/')
    def preview():
        return (repo / 'site/index.html').read_text().replace('__VERSION__', '0.4.0').replace('__INSTALLER__', 'fixture-setup.exe').replace('__INSTALLER_SHA256__', 'a'*64).replace('__LINUX_URL__', '/fixture-linux.zip')
    @app.get('/release-preview/<path:name>')
    def preview_asset(name):
        return send_from_directory(repo if name == 'tokens.css' else repo / 'site', name)
    class Quiet(WSGIRequestHandler):
        def log(self, *args, **kwargs): pass
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = dict(os.environ, CAMPUS_PLAYWRIGHT_MODULE=a.playwright, CAMPUS_CHROME=a.chrome)
        payload = {'origin': 'http://127.0.0.1:' + str(server.server_port), 'token': runtime.token, 'output': str(a.output)}
        result = subprocess.run([a.node, str(Path(__file__).with_suffix('.cjs'))], input=json.dumps(payload),
                                env=env, text=True, capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        print(result.stdout)
    finally:
        server.shutdown()


if __name__ == '__main__': main()
