"""Exercise the real nginx auth subrequest with uploads and a small server limit."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest


@pytest.mark.skipif(shutil.which('nginx') is None, reason='Requires nginx runtime')
def test_authenticated_upload_survives_auth_subrequest_limit(tmp_path):
    received = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == '/auth/verify'
            received.append(('auth', self.headers.get('Content-Length')))
            self.send_response(204 if self.headers.get('Cookie') == 'fixture=valid' else 401)
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            received.append(('upload', len(body)))
            data = json.dumps({'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    template = (Path(__file__).resolve().parents[1] / 'deploy/nginx-campus-browser.conf').read_text()
    template = template.replace('127.0.0.1:18771', f'127.0.0.1:{backend.server_port}')
    config = tmp_path / 'nginx.conf'
    config.write_text(f'pid {tmp_path}/nginx.pid; error_log {tmp_path}/error.log; '
                      'events {} http { access_log off; '
                      f'client_body_temp_path {tmp_path}/body; '
                      f'server {{ listen 127.0.0.1:{port}; client_max_body_size 64k; '
                      + template + ' } }')
    process = subprocess.Popen(['nginx', '-p', str(tmp_path), '-c', str(config), '-g', 'daemon off;'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                if process.poll() is not None:
                    pytest.fail((tmp_path / 'error.log').read_text())
                time.sleep(.02)
        for size in (832549, 2 * 1024 * 1024):
            body = b'x' * size
            req = urllib.request.Request(base + '/campus/api/upload-fixture', data=body,
                headers={'Cookie': 'fixture=valid', 'Content-Type': 'application/octet-stream'})
            with opener.open(req, timeout=10) as response:
                assert json.load(response) == {'size': size, 'sha256': hashlib.sha256(body).hexdigest()}
        uploads = [entry for entry in received if entry[0] == 'upload']
        assert len(uploads) == 2
        assert all(length is None for kind, length in received if kind == 'auth')
        req = urllib.request.Request(base + '/campus/api/upload-fixture', data=b'x' * 832549)
        with pytest.raises(urllib.error.HTTPError) as denied:
            opener.open(req, timeout=10)
        assert denied.value.code == 401
        assert json.load(denied.value)['error'] == '请登录校园面板'
        assert len([entry for entry in received if entry[0] == 'upload']) == 2
        with pytest.raises(urllib.error.HTTPError) as internal:
            opener.open(base + '/_sustech_campus_auth', timeout=10)
        assert internal.value.code == 404
        # An oversized declared body is rejected before reading or forwarding it.
        req = urllib.request.Request(base + '/campus/api/upload-fixture', data=b'',
            headers={'Cookie': 'fixture=valid', 'Content-Length': str(257 * 1024 * 1024)})
        with pytest.raises(urllib.error.HTTPError) as oversized:
            opener.open(req, timeout=10)
        assert oversized.value.code == 413
    finally:
        process.terminate()
        process.wait(timeout=10)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=5)
