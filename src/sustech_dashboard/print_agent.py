"""Fixed print operations on the owner's paired computer. No remote commands or URLs."""
import hashlib
import json
import socket
import threading
import time

from . import printing
from .actions import Actions
from .core import DATA_ROOT, load_json, save_json
from .download_agent import cloud_session
from .print_relay import UNCERTAIN, identifier


def execute(job, fetch, ledger=None):
    kind = job['kind']
    payload = job['payload']
    jid = identifier(job['id'])
    ledger = ledger or Actions(DATA_ROOT / 'print-agent-actions.sqlite3')
    if kind not in {'overview', 'history', 'upload', 'delete'}:
        raise ValueError('打印操作无效')
    if kind == 'overview':
        with printing.lock:
            return {'state': 'confirmed', 'data': printing.overview()}
    if kind == 'history':
        with printing.lock:
            return {'state': 'confirmed', 'data': printing.history(payload)}
    if job.get('recovery_only'):
        with ledger.connect() as db:
            row = db.execute('SELECT result,object FROM actions WHERE id=?', (jid,)).fetchone()
        if row and row[0] and row[1] == job['target']:
            return json.loads(row[0])
        return {'state': 'needs_review', 'message': UNCERTAIN}

    def perform():
        with printing.lock:
            if kind == 'delete':
                return printing.delete(payload['kind'], int(payload['job_id']))
            content = fetch(jid)
            if len(content) != payload['size'] or hashlib.sha256(content).hexdigest() != payload['sha256']:
                raise ValueError('打印文件校验失败，未上传到学校')
            return printing.upload(payload['filename'], content, printing.options(payload['options']))
    return ledger.run(jid, job['target'], perform)


def run(stop):
    from .authentication import load_owner_credentials
    from .locking import exclusive_file
    with exclusive_file(DATA_ROOT / 'print-agent.lock'):
        try:
            configured = load_owner_credentials()
        except Exception:
            configured = False
        base, session = cloud_session()
        identity = DATA_ROOT / 'download-agent-id.txt'
        # The download agent already uses this machine identity.
        if not identity.exists():
            import uuid
            identity.write_text(uuid.uuid4().hex, encoding='ascii')
        agent = identity.read_text(encoding='ascii').strip()
        headers = {'X-Campus-Agent': '1', 'X-Print-Agent': agent}
        active_path = DATA_ROOT / 'print-agent-active.json'
        receipt_path = DATA_ROOT / 'print-agent-receipt.json'
        health_path = DATA_ROOT / 'print-agent-health.json'
        active = load_json(active_path, None)
        receipt = load_json(receipt_path, None)
        guard = threading.Lock()
        worker = None
        ready, error, checked = False, '', 0

        def fetch(jid):
            _, file_session = cloud_session()
            response = file_session.get(base + '/api/printing/agent/file/' + identifier(jid), headers=headers,
                                   timeout=(10,120), stream=True)
            response.raise_for_status()
            content = bytearray()
            with response:
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 50*1024*1024:
                        raise ValueError('打印文件超过 50 MB')
            return bytes(content)

        def work(job):
            nonlocal receipt
            try:
                value = execute(job, fetch)
            except Exception as exc:
                uncertain = job['kind'] in {'upload', 'delete'}
                value = {'state': 'needs_review' if uncertain else 'rejected', 'message': (
                    UNCERTAIN if uncertain else '本机未能连接或登录打印系统，请检查校园网和本机账号')}
            with guard:
                receipt = {'id': job['id'], 'result': value}
                save_json(receipt_path, receipt)

        if active and not receipt:
            active['recovery_only'] = True
            worker = threading.Thread(target=work, args=(active,), daemon=True)
            worker.start()
        while not stop.is_set() or (worker and worker.is_alive()) or receipt:
            if time.monotonic() - checked >= 30 and not active:
                try:
                    if not configured:
                        raise ValueError('执行主机尚未配置校园账号，请在该主机上登录或配置本人的加密凭据')
                    printing.check_network()
                    ready, error = True, ''
                except Exception as exc:
                    ready, error = False, (str(exc) if isinstance(exc, ValueError) else '本机无法连接打印系统，请检查校园网或学校 VPN')
                checked = time.monotonic()
            with guard:
                sent_receipt = receipt
                payload = {'agent_id': agent, 'name': socket.gethostname(), 'ready': ready, 'error': error,
                           'claim': active is None and not stop.is_set(), 'active': active['id'] if active else None}
                if sent_receipt:
                    payload['receipt'] = sent_receipt
            try:
                response = session.post(base + '/api/printing/agent', headers=headers, json=payload, timeout=(10,30))
                response.raise_for_status()
                job = response.json().get('job')
                if sent_receipt:
                    with guard:
                        receipt = active = None
                        receipt_path.unlink(missing_ok=True)
                        active_path.unlink(missing_ok=True)
                if job:
                    active = job
                    save_json(active_path, active)
                    worker = threading.Thread(target=work, args=(job,), daemon=True)
                    worker.start()
                save_json(health_path, {'connected': True, 'ready': ready, 'error': error, 'at': time.time()})
            except Exception:
                save_json(health_path, {'connected': False, 'ready': ready, 'error': '打印电脑暂时无法连接个人服务器', 'at': time.time()})
            if stop.is_set():
                time.sleep(3)
            else:
                stop.wait(3)
