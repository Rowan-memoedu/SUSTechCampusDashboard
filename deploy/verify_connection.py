"""Isolated Chrome -> native handoff -> real local/cloud queues. No school traffic."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

PREFIX = '/spaces/' + 'f' * 24


def child(role, root):
    config = json.loads((root/'config.json').read_text())
    data = root/('local' if role == 'activate' else role)
    data.mkdir(exist_ok=True)
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(data), SUSTECH_DOWNLOAD_ROOT=str(root/'files'),
        SUSTECH_EXECUTION_MODE='hosted' if role == 'cloud' else 'local',
        SUSTECH_CLOUD='1' if role == 'cloud' else '0', SUSTECH_PAIR_HOST=config['host'])
    if role == 'cloud':
        os.environ.update(SUSTECH_SPACE_ID='f'*24, SUSTECH_PROXY_PREFIX=PREFIX,
            SUSTECH_PUBLIC_HOST=config['host'], CREDENTIALS_DIRECTORY=str(root/'credentials'))
    else:
        os.environ.pop('CREDENTIALS_DIRECTORY', None)
        os.environ['REQUESTS_CA_BUNDLE'] = str(root/'certificate.pem')
    if role == 'activate':
        from sustech_dashboard.protocol import handle_uri
        return handle_uri(sys.stdin.read(), config['local_port'])
    import requests
    original = requests.Session.request
    def fixture_only(session, method, url, **kwargs):
        from urllib.parse import urlsplit
        assert urlsplit(url).hostname == '127.0.0.1', 'External traffic forbidden in fixture'
        return original(session, method, url, **kwargs)
    requests.Session.request = fixture_only
    from sustech_dashboard import app, authentication, runtime, venues, printing, local_files
    from sustech_dashboard import shared_metadata
    shared_metadata.configured_client = lambda: None  # Fixture manifests contain no shared references.
    from sustech_dashboard.core import save_json
    from sustech_dashboard.materials_store import MaterialsStore
    venues.catalog = lambda: {'library': [], 'ehall': [], 'errors': []}
    venues.library_mine = venues.eh_mine = lambda *a, **k: []
    authentication.load_owner_credentials = lambda: role == 'local'
    authentication.secure_upstream = lambda: None
    printing.check_network = lambda: None
    printing.overview = lambda: {'stations': [], 'jobs': [], 'scans': [], 'updated_at': '2026-10-07T00:00:00+08:00'}
    def opened(path):
        with (root/'opened.jsonl').open('a') as handle:
            handle.write(json.dumps(str(path))+'\n')
    local_files.open_target = opened
    manifest = {'updated_at': '2026-10-07', 'courses': [{'id': 'fixture-course', 'name': 'Fixture course'}],
        'items': [{'course_id': 'fixture-course', 'course_name': 'Fixture course', 'content_id': 'content',
                  'id': 'attachment', 'title': 'Fixture', 'file_name': 'fixture.pdf'}]}
    save_json(app.MANIFEST_PATH, manifest)
    store = MaterialsStore(app.MATERIALS_DB); store.scan_finished(manifest)
    class School:
        def download_attachment(self, course, content, attachment, target):
            target.write_bytes(b'%PDF-1.4\nFixture direct school bytes\n%%EOF')
    app.Blackboard = School
    from sustech_dashboard import provider
    provider.Blackboard = School
    rt = runtime.Runtime(); rt.configured.set()
    if role == 'cloud':
        from sustech_dashboard.hosted import Space
        space = Space(); space.redeem(space.invite(), 'fixture-panel-password')
        space.bind('fixture-student', 'fixture-cas-password', False, lambda *a: None)
    else:
        from sustech_dashboard.dpapi_store import save_credentials
        save_credentials('fixture-student', 'fixture-cas-password')
        from sustech_dashboard.download_agent import local_agent
        threading.Thread(target=local_agent, args=(rt.stop,), daemon=True).start()
    from werkzeug.serving import make_server, WSGIRequestHandler
    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs): pass
    dashboard = app.create_app(rt)
    if role == 'local':
        make_server('127.0.0.1', config['local_port'], dashboard, threaded=True, request_handler=Quiet).serve_forever()
    else:
        from sustech_dashboard.entry import create_app, backend
        from werkzeug.middleware.dispatcher import DispatcherMiddleware
        from werkzeug.wrappers import Response
        private = make_server('127.0.0.1', config['private_port'], dashboard, threaded=True, request_handler=Quiet)
        threading.Thread(target=private.serve_forever, daemon=True).start()
        routes = root/'routes.json'; routes.write_text(json.dumps({'host': config['host'], 'owner_name': '',
            'routes': [{'prefix': PREFIX, 'username': 'member', 'port': 18900}]}))
        entry = create_app(root/'entry', routes, lambda route, host, endpoint, fields, peer:
            backend({**route, 'port': config['private_port']}, host, endpoint, fields, peer))
        import sqlite3
        with sqlite3.connect(root/'entry/accounts.sqlite3') as db:
            db.execute('INSERT INTO accounts VALUES (?,?)', ('fixture-user', PREFIX))
        dispatch = DispatcherMiddleware(Response(status=404), {'/app': entry, PREFIX: dashboard})
        def frontend(environ, start_response):
            environ['HTTP_X_FORWARDED_PROTO'] = 'https'
            environ['HTTP_X_FORWARDED_PREFIX'] = '/app' if environ['PATH_INFO'].startswith('/app/') else PREFIX
            return dispatch(environ, start_response)
        make_server('127.0.0.1', config['cloud_port'], frontend, ssl_context=(str(root/'certificate.pem'),
            str(root/'certificate-key.pem')), threaded=True, request_handler=Quiet).serve_forever()


def main():
    import argparse
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--chrome', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='connection-', dir=args.output))
    from sustech_dashboard.dpapi_store import _restrict_directory
    _restrict_directory(root)
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]
    config = {'local_port': port(), 'cloud_port': port(), 'private_port': port(), 'root': str(root),
              'python': sys.executable, 'script': str(Path(__file__).resolve()), 'chrome': args.chrome}
    config.update(host='127.0.0.1:'+str(config['cloud_port']), origin='https://127.0.0.1:'+str(config['cloud_port']))
    (root/'config.json').write_text(json.dumps(config))
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime, timedelta, timezone
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'localhost fixture')])
    now = datetime.now(timezone.utc)
    certificate = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(
        x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), critical=True).add_extension(x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256())
    (root/'certificate.pem').write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (root/'certificate-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (root/'credentials').mkdir()
    from cryptography.fernet import Fernet
    (root/'credentials/campus-vault').write_bytes(Fernet.generate_key())
    children, logs = [], []
    try:
        for role in ['cloud', 'local']:
            log = (root/(role+'.log')).open('wb'); logs.append(log)
            children.append(subprocess.Popen([sys.executable, __file__, role, str(root)], stdout=log, stderr=log))
        for _ in range(150):
            if any(p.poll() is not None for p in children):
                raise RuntimeError('Fixture startup failed; private logs: '+str(root))
            try:
                for number in [config['local_port'], config['cloud_port']]:
                    with socket.create_connection(('127.0.0.1', number), timeout=.1): pass
                break
            except OSError: time.sleep(.2)
        result = subprocess.run([args.node, str(Path(__file__).with_suffix('.cjs'))], input=json.dumps(config),
                                text=True, capture_output=True, timeout=240)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:]+'; private logs: '+str(root))
        report = json.loads(result.stdout)
        assert len((root/'opened.jsonl').read_text().splitlines()) == 3
        receipts = json.loads((root/'local/downloaded-files.json').read_text())
        import hashlib
        for receipt in receipts.values():
            assert hashlib.sha256((root/'files'/receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
        report.update(file_hashes_verified=len(receipts), native_open_dispatches=3, school_business_writes=0)
        (args.output/'connection-report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        for process in children:
            if process.poll() is None: process.terminate(); process.wait(timeout=15)
        for log in logs: log.close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in {'local', 'cloud', 'activate'}:
        sys.exit(child(sys.argv[1], Path(sys.argv[2])))
    main()
