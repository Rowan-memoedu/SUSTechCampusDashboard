"""Update the existing owner instance only from the public signed source."""
import json
from pathlib import Path
import re
import sqlite3
import time

import requests
from sustech_dashboard.paths import DATA_ROOT, download_root
from sustech_dashboard.release_identity import sha256, verify_payload
from sustech_dashboard.runtime import wait_existing


def retention(root, downloads):
    """Keep values private; emit only comparisons and counts."""
    result={'private':{},'files':{},'actions':{},'baseline':None}
    for name in ('credentials.dpapi.json','browser-token'):
        path=root/name
        if path.exists():result['private'][name]=sha256(path)
    baseline=root/'attachments.json'
    if baseline.exists():result['baseline']=json.loads(baseline.read_text(encoding='utf-8')).get('baseline_at')
    receipts=root/'downloaded-files.json'
    if receipts.exists():
        for record in json.loads(receipts.read_text(encoding='utf-8')).values():
            path=(downloads/record.get('path','')).resolve()
            if path.is_file() and path.is_relative_to(downloads.resolve()):
                result['files'][str(path)]=sha256(path)
    db=root/'actions.sqlite3'
    if db.exists():
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
            rows=conn.execute('SELECT * FROM actions ORDER BY id').fetchall()
            import hashlib
            result['actions']={row[0]:hashlib.sha256(json.dumps(row,ensure_ascii=False).encode()).hexdigest() for row in rows}
    return result


def preserved(before,after):
    return (before['private']==after['private'] and before['baseline']==after['baseline']
        and all(after['files'].get(k)==v for k,v in before['files'].items())
        and all(after['actions'].get(k)==v for k,v in before['actions'].items()))


def committed_selection(root, version, timeout=90):
    """A healthy new backend can answer before its supervisor commits the pointer."""
    from release import emit
    deadline=time.monotonic()+timeout
    announced=False
    while True:
        try:
            pointer=json.loads((root/'updates/current.json').read_text(encoding='utf-8'))
            result=json.loads((root/'updates/last-result.json').read_text(encoding='utf-8'))
        except (FileNotFoundError,json.JSONDecodeError):
            pointer,result={},{}
        if result.get('state')=='rolled_back' and result.get('failed_version')==version:
            raise RuntimeError('Owner update rolled back; original usable version retained')
        if pointer.get('version')==version and result=={'state':'installed','version':version}:
            folder=(root/'updates/releases'/pointer['directory']).resolve()
            if not folder.is_relative_to((root/'updates/releases').resolve()):
                raise ValueError('Invalid installed pointer')
            return folder
        if time.monotonic()>=deadline:
            raise TimeoutError('Owner updater has not committed the published version')
        if not announced:
            emit('owner_update',version=version,state='waiting_for_committed_selection')
            announced=True
        time.sleep(2)


def verify(manifest, output, port):
    from release import save,emit
    version,commit=manifest['version'],manifest['source_commit']
    asset=next(a for a in manifest['assets'] if a['filename']=='windows-x86_64.zip')
    token=(DATA_ROOT/'browser-token').read_text(encoding='utf-8').strip()
    if not wait_existing(port,token,timeout=10):
        raise RuntimeError('Existing owner instance is not running; start its registered launcher and resume')
    origin=f'http://127.0.0.1:{port}';s=requests.Session();s.trust_env=False
    headers={'Origin':origin}
    def unlock():
        r=s.post(origin+'/auth/unlock',json={'token':token},headers={'Origin':origin},timeout=10);r.raise_for_status()
        page=s.get(origin+'/',timeout=15);page.raise_for_status()
        headers['X-CSRF-Token']=re.search(r'const roomCsrf="([^"]+)"',page.text).group(1)
    def status():
        r=s.get(origin+'/api/instance',timeout=10)
        if r.status_code==401:unlock();r=s.get(origin+'/api/instance',timeout=10)
        r.raise_for_status();return r.json()
    unlock();initial=status()
    if not initial['managed']:raise ValueError('Owner instance is not managed by the existing updater')
    # Saved privately under the already protected instance, never in release assets.
    proof=DATA_ROOT/'updates'/('release-'+version+'-retention.json')
    before=json.loads(proof.read_text(encoding='utf-8')) if proof.exists() else retention(DATA_ROOT,download_root())
    if not proof.exists():save(proof,before)
    if initial['version']!=version:
        r=s.post(origin+'/api/instance/updates/check',json={},headers=headers,timeout=15);r.raise_for_status()
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            current=status();u=current['update']
            if u.get('latest')==version and u.get('available'):break
            if u.get('state')=='error':raise RuntimeError('Owner signed update check failed')
            time.sleep(2)
        else:raise RuntimeError('Owner did not offer the exact published version')
        r=s.post(origin+'/api/instance/updates/install',json={},headers=headers,timeout=20);r.raise_for_status()
    deadline=time.monotonic()+1800;last=None
    while time.monotonic()<deadline:
        try:
            current=status();u=current['update'];state=(current['version'],u['state'])
            if state!=last:emit('owner_update',version=state[0],state=state[1]);last=state
            if current['version']==version:break
            if u['state']=='error' or current.get('last_update',{}).get('state')=='rolled_back':
                raise RuntimeError('Owner update failed; original usable version retained')
        except requests.RequestException:pass
        time.sleep(3)
    else:raise TimeoutError('Owner update incomplete; resume the same release')
    identity=current.get('release_identity',{})
    expected={'version':version,'source_commit':commit,'payload_sha256':asset['payload_sha256']}
    if any(identity.get(k)!=v for k,v in expected.items()):raise ValueError('Running identity differs from public release')
    folder=committed_selection(DATA_ROOT,version)
    verify_payload(folder,expected)
    downloaded=[p for p in (DATA_ROOT/'updates/downloads').iterdir() if p.is_file()]
    if not any(sha256(p)==asset['sha256'] for p in downloaded):raise ValueError('Owner download hash differs')
    after=retention(DATA_ROOT,download_root())
    if not preserved(before,after):raise ValueError('Personal data retention differs; keep release incomplete')
    if initial['configured'] and (not current['configured'] or current.get('credential_error')):
        raise ValueError('Owner login state was not preserved')
    r=s.post(origin+'/api/instance/updates/check',json={},headers=headers,timeout=15);r.raise_for_status()
    for _ in range(45):
        current=status()
        if current['update'].get('state')=='current' and current['update'].get('latest')==version:break
        time.sleep(2)
    else:raise ValueError('Owner has not confirmed the stable channel identity')
    r=s.get(origin+'/api/status',timeout=30);r.raise_for_status();campus=r.json()
    if campus.get('errors'):raise ValueError('Campus readback has errors; retain incomplete status for review')
    return {'version':version,'source_commit':commit,'artifact_sha256':asset['sha256'],
        'payload_sha256':asset['payload_sha256'],'retained_files':len(before['files']),
        'data_preserved':True,'configured_without_relogin':bool(current['configured']),
        'assignment_count':len(campus.get('assignments',[])),'school_writes':0,
        'stable_channel_equal':True,'passed':True}
