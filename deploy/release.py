"""One resumable release: frozen source -> candidate -> stable feed/site -> owner update.

Run from the existing maintainer checkout. The journal records evidence, not a
second update service. GitHub builds once; the existing offline TUF keys sign
those exact files. No campus credentials or publisher keys are uploaded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import requests

from release_assets import audit_archive, digest
from renew_feed import SSH_HOST, SSH_OPTIONS, get_public, verified_feed, save_metadata
from sustech_dashboard.locking import exclusive_file
from sustech_dashboard.updates import RELEASE_URL, TRUST_ROOT, UpdateManager

REPO = 'Rowan-memoedu/SUSTechCampusDashboard'
ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://124.221.144.155/app/'
DEFAULT_OUTPUT = Path('D:/Artifacts/SUSTechCampusDashboard/releases')


def emit(stage, **fields):
    print(json.dumps({'stage':stage, **fields}, ensure_ascii=False), flush=True)


def command(args, **kwargs):
    env=dict(os.environ, PYTHONUTF8='1')
    # gh uses the normal signed-in user's configuration, never a copied token.
    if os.name=='nt' and not env.get('APPDATA'):
        env['APPDATA']=str(Path.home()/'AppData/Roaming')
    result=subprocess.run([str(a) for a in args], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=kwargs.pop('timeout',180), **kwargs)
    if result.returncode:
        raise RuntimeError(Path(str(args[0])).name+' failed: '+result.stderr.strip()[-1200:])
    return result.stdout.strip()


def gh(*args):
    return command(['gh',*args])


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.new')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def upload_file(source, remote):
    """Resume public staging by digest, using a separate connection per file."""
    if not re.fullmatch(r'/tmp/sustech-release-[A-Za-z0-9._/-]+',remote):
        raise ValueError('Invalid public staging path')
    expected=digest(source)
    def remote_digest():
        value=command(['ssh',*SSH_OPTIONS,SSH_HOST,
            'if test -f '+remote+'; then sha256sum '+remote+'; fi'])
        return value.split()[0] if value else None
    if remote_digest()==expected:
        emit('upload',file=Path(source).name,state='reused_verified_file');return
    emit('upload',file=Path(source).name,state='transferring')
    command(['scp',*SSH_OPTIONS,str(source),SSH_HOST+':'+remote],timeout=900)
    if remote_digest()!=expected:raise ValueError('Remote staging hash mismatch: '+Path(source).name)
    emit('upload',file=Path(source).name,state='verified')


def download(url, path, expected=None):
    path=Path(path)
    if expected and path.exists() and digest(path)==expected:
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.partial')
    s=requests.Session(); s.trust_env=False
    if expected and temporary.exists() and digest(temporary)==expected:
        temporary.replace(path);return
    for attempt in range(3):
        size=temporary.stat().st_size if expected and temporary.exists() else 0
        headers={'Cache-Control':'no-cache'}
        if size:headers['Range']=f'bytes={size}-'
        try:
            with s.get(url,stream=True,timeout=(15,90),headers=headers) as response:
                response.raise_for_status()
                if response.status_code==206:
                    interval=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                    if not interval or int(interval[1])!=size or not size or not size<=int(interval[2])<int(interval[3]):
                        raise ValueError('Invalid resumed download range')
                    mode='ab'
                else:
                    size=0;mode='wb'
                last=time.monotonic()
                with temporary.open(mode) as stream:
                    for block in response.iter_content(1024*1024):
                        stream.write(block); size+=len(block)
                        if time.monotonic()-last>25:
                            emit('download',file=path.name,bytes=size); last=time.monotonic()
            break
        except requests.RequestException:
            if attempt==2:raise
            emit('download',file=path.name,state='retrying_transport',attempt=attempt+2,bytes=size)
            time.sleep(2**attempt)
    if expected and digest(temporary)!=expected:
        raise ValueError('Public artifact hash mismatch: '+path.name)
    temporary.replace(path)


def validate_manifest(manifest, version, commit, platforms=('windows-x86_64','linux-x86_64')):
    names={p+'.zip' for p in platforms}|{f'SUSTechCampusDashboard-{version}-windows-x86_64-setup.exe'}
    if manifest.get('version')!=version or manifest.get('source_commit')!=commit:
        raise ValueError('Release identity differs from the frozen source')
    assets=manifest.get('assets',[])
    if len(assets)!=len(names) or {a.get('filename') for a in assets}!=names:
        raise ValueError('Unexpected release asset list')
    if any(not re.fullmatch('[a-f0-9]{64}', a.get('sha256','')) or a.get('size',0)<=0 for a in assets):
        raise ValueError('Invalid release digest')
    return {a['filename']:a for a in assets}


class Release:
    def __init__(self, version, commit, output=DEFAULT_OUTPUT, platforms=('windows-x86_64','linux-x86_64')):
        if not re.fullmatch(r'\d+\.\d+\.\d+',version) or not re.fullmatch('[a-f0-9]{40}',commit):
            raise ValueError('A version and full source commit are required')
        self.version,self.commit,self.tag=version,commit,'v'+version
        self.platforms=tuple(platforms)
        if self.platforms not in (('windows-x86_64',),('windows-x86_64','linux-x86_64')):
            raise ValueError('Unsupported release platforms')
        self.scope='windows' if len(self.platforms)==1 else 'all'
        self.root=Path(output)/self.tag; self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'state.json'
        self.state=read_json(self.path) if self.path.exists() else {
            'schema':1,'version':version,'source_commit':commit,'platforms':list(self.platforms),'status':'incomplete','steps':{}}
        if (self.state['version'],self.state['source_commit'])!=(version,commit):
            raise ValueError('This version already belongs to a different source commit')
        if tuple(self.state.get('platforms',('windows-x86_64','linux-x86_64'))) != self.platforms:
            raise ValueError('This release batch has a different platform scope')

    def record(self, stage, result):
        self.state['steps'][stage]=dict(result,verified_at=datetime.now(timezone.utc).isoformat())
        self.state.pop('error',None); save(self.path,self.state); emit(stage,**result)

    def workflow(self, file, title, inputs, key):
        runs=json.loads(gh('api',f'repos/{REPO}/actions/workflows/{file}/runs?event=workflow_dispatch&per_page=50'))['workflow_runs']
        matching=[r for r in runs if r.get('display_title')==title]
        if matching:
            run=matching[0]
            if run['status']=='completed' and run['conclusion']!='success':
                # Reuse successful jobs and their artifacts; never rebuild successful platforms.
                gh('run','rerun',str(run['id']),'--failed','--repo',REPO)
        else:
            args=['workflow','run',file,'--repo',REPO,'--ref','main']
            for k,v in inputs.items(): args+=['-f',f'{k}={v}']
            gh(*args)
            for _ in range(30):
                time.sleep(2)
                runs=json.loads(gh('api',f'repos/{REPO}/actions/workflows/{file}/runs?event=workflow_dispatch&per_page=20'))['workflow_runs']
                matching=[r for r in runs if r.get('display_title')==title]
                if matching: break
            else: raise RuntimeError('Workflow dispatch outcome unknown; rerun this release command to reconcile')
            run=matching[0]
        self.state[key]=run['id']; save(self.path,self.state)
        deadline=time.monotonic()+60*60
        while time.monotonic()<deadline:
            run=json.loads(gh('api',f'repos/{REPO}/actions/runs/{run["id"]}'))
            emit(key,run=run['id'],status=run['status'],conclusion=run['conclusion'])
            if run['status']=='completed':
                if run['conclusion']!='success': raise RuntimeError('Workflow failed; successful stages retained: '+run['html_url'])
                return {'run_id':run['id'],'url':run['html_url'],'conclusion':'success'}
            time.sleep(30)
        raise TimeoutError('Workflow still pending; rerun to continue the same batch')

    def freeze(self):
        source=command(['git','show',self.commit+':pyproject.toml'])
        if f'version = "{self.version}"' not in source: raise ValueError('Source version mismatch')
        # Local deployment scripts/site must match the exact frozen commit.
        command(['git','diff','--exit-code',self.commit,'--','deploy','site','tokens.css',
                 'src/sustech_dashboard/updates.py','src/sustech_dashboard/release_identity.py'])
        existing=command(['git','tag','--list',self.tag])
        if existing:
            if command(['git','rev-parse',self.tag+'^{commit}'])!=self.commit:
                raise ValueError('Existing version tag has different source')
        else: command(['git','tag',self.tag,self.commit])
        command(['git','push','origin','refs/tags/'+self.tag])
        remote=json.loads(gh('api',f'repos/{REPO}/commits/{self.tag}'))['sha']
        if remote!=self.commit: raise ValueError('Remote source tag mismatch')
        self.record('frozen',{'version':self.version,'source_commit':self.commit})

    def candidate(self):
        try:
            release=json.loads(gh('api',f'repos/{REPO}/releases/tags/{self.tag}'))
        except RuntimeError as exc:
            if '404' not in str(exc): raise
            result=self.workflow('build-release.yml','Candidate '+self.tag,{'tag':self.tag,'platforms':self.scope},'build_run')
            self.record('built',result)
            release=json.loads(gh('api',f'repos/{REPO}/releases/tags/{self.tag}'))
        self.assets=self.root/'assets'; self.assets.mkdir(exist_ok=True)
        remote={a['name']:a for a in release['assets']}
        download(remote['release-manifest.json']['browser_download_url'],self.assets/'release-manifest.json')
        self.manifest=read_json(self.assets/'release-manifest.json')
        self.records=validate_manifest(self.manifest,self.version,self.commit,self.platforms)
        for name,record in self.records.items():
            if remote[name]['size']!=record['size'] or remote[name].get('digest')!='sha256:'+record['sha256']:
                raise ValueError('GitHub asset receipt differs: '+name)
            download(remote[name]['browser_download_url'],self.assets/name,record['sha256'])
            if (self.assets/name).stat().st_size!=record['size']: raise ValueError('Artifact size mismatch')
            if name.endswith('.zip'):
                audit_archive(self.assets/name,self.version,name[:-4],self.commit)
        installer=next(r for n,r in self.records.items() if n.endswith('.exe'))
        save(self.assets/'installer.json',dict(installer,version=self.version,source_commit=self.commit,
            payload_sha256=self.records['windows-x86_64.zip']['payload_sha256'],authenticode_signed=False))
        self.record('candidate',{'version':self.version,'source_commit':self.commit,
            'assets':{n:r['sha256'] for n,r in self.records.items()}})

    def independent_windows(self, agent_directory, phase):
        from verify_independent_windows import verify
        report=self.root/('windows-'+phase+'.json')
        if report.exists():
            prior=read_json(report)
            if (prior.get('version'),prior.get('source_commit'),prior.get('artifact_sha256')) == (
                    self.version,self.commit,self.records['windows-x86_64.zip']['sha256']) and prior.get('passed'):
                self.record('windows_'+phase,prior); return
        result=verify(Path(agent_directory),self.assets,self.manifest,phase,self.root)
        save(report,result); self.record('windows_'+phase,result)

    def publish(self):
        from build_site import build
        from publish_release import publish
        blobs,roles=verified_feed(get_public)
        current=roles['targets'].signed.targets
        archives=[p+'.zip' for p in self.platforms]
        already=all(n in current and current[n].hashes['sha256']==self.records[n]['sha256'] for n in archives)
        if not self.state.get('previous'):
            if already: raise ValueError('Missing original upgrade baseline; recover the existing batch journal')
            self.state['previous']={n:dict(version=t.unrecognized_fields['custom']['version'],sha256=t.hashes['sha256']) for n,t in current.items()}
            save(self.path,self.state)
        stage=self.root/'publication'; stage.mkdir(exist_ok=True)
        if not already:
            save_metadata(stage/'metadata',blobs)
            publish(stage,[self.assets/n for n in archives])
        else: save_metadata(stage/'metadata',blobs)
        for name in archives:
            target=stage/name
            if not target.exists(): os.link(self.assets/name,target)
            if digest(target)!=self.records[name]['sha256']: raise ValueError('Staging artifact changed')
        site=stage/'site'
        if not site.exists():
            installer=next(self.assets/n for n in self.records if n.endswith('.exe'))
            build(installer,self.assets/'linux-x86_64.zip' if 'linux-x86_64' in self.platforms else None,site)
        elif read_json(site/'release-manifest.json')!=self.manifest:
            raise ValueError('Staged site belongs to different artifacts')
        remote='/tmp/sustech-release-'+self.version+'-'+self.commit[:12]
        files=sorted(p for folder in (stage/'metadata',site) for p in folder.rglob('*') if p.is_file())
        files += [stage/n for n in archives]
        directories=sorted({remote+'/'+p.relative_to(stage).parent.as_posix() for p in files})
        command(['ssh',*SSH_OPTIONS,SSH_HOST,'mkdir -p '+' '.join(directories)])
        for source in files:upload_file(source,remote+'/'+source.relative_to(stage).as_posix())
        command(['scp',*SSH_OPTIONS,*[ROOT/'deploy'/n for n in ('promote_release.py','publish_feed.py','feed_metadata.py')],SSH_HOST+':'+remote+'/'])
        expected=hashlib.sha256(blobs['timestamp.json']).hexdigest()
        output=command(['ssh',*SSH_OPTIONS,SSH_HOST,
            'sudo -n /opt/sustech-campus-renewal/venv/bin/python '+remote+'/promote_release.py '+remote+' --expected-timestamp '+expected],timeout=300)
        self.record('feed_site',json.loads(output))

    def verify_public(self, require_stable=False):
        # Strict client validation includes expiry, signature, rollback and both platform targets.
        client=UpdateManager(self.root/'public-client')._client(); client.refresh()
        for platform in self.platforms:
            name=platform+'.zip'; target=client.get_targetinfo(name)
            if not target or target.hashes['sha256']!=self.records[name]['sha256']:
                raise ValueError('Public TUF target differs')
            identity=target.unrecognized_fields['custom']
            if identity.get('source_commit')!=self.commit or identity['version']!=self.version:
                raise ValueError('Public TUF identity differs')
            download(RELEASE_URL+'/targets/'+target.hashes['sha256']+'.'+name,
                self.root/'public-readback'/name,target.hashes['sha256'])
        for name,previous in self.state.get('previous',{}).items():
            if name in self.records:continue
            target=client.get_targetinfo(name)
            if (not target or target.hashes['sha256']!=previous['sha256']
                    or target.unrecognized_fields['custom']['version']!=previous['version']):
                raise ValueError('Unselected public platform changed: '+name)
        s=requests.Session();s.trust_env=False
        r=s.get(SITE+'downloads/release-manifest.json',timeout=30);r.raise_for_status()
        if r.json()!=self.manifest: raise ValueError('Public site manifest differs')
        r=s.get(SITE,timeout=30);r.raise_for_status()
        if '<strong>'+self.version+'</strong>' not in r.text: raise ValueError('Public page version differs')
        if 'id="open-local" class="button secondary" href="sustech-campus://open"' not in r.text:
            raise ValueError('Public entry does not launch the installed Windows app')
        installer=next(r for n,r in self.records.items() if n.endswith('.exe'))
        download(SITE+'downloads/'+installer['filename'],self.root/'public-readback'/installer['filename'],installer['sha256'])
        release=json.loads(gh('api',f'repos/{REPO}/releases/tags/{self.tag}'))
        if require_stable and (release['prerelease'] or release['draft']): raise ValueError('GitHub is not stable yet')
        for asset in release['assets']:
            if asset['name'] in self.records and asset.get('digest')!='sha256:'+self.records[asset['name']]['sha256']:
                raise ValueError('GitHub binary changed during promotion')
        self.record('public',{'version':self.version,'source_commit':self.commit,'strict_tuf':True,
            'public_hashes_verified':True,'platforms':list(self.platforms),
            'windows_launch_link':True,'github_stable':not release['prerelease'] and not release['draft']})

    def public_upgrades(self):
        previous=self.state['previous']
        inputs={'tag':self.tag,'commit':self.commit,'platforms':self.scope,
                'previous_windows':previous['windows-x86_64.zip']['sha256']}
        if 'linux-x86_64' in self.platforms:inputs['previous_linux']=previous['linux-x86_64.zip']['sha256']
        result=self.workflow('verify-published.yml','Published upgrade '+self.tag,inputs,'upgrade_run')
        self.record('public_upgrades',result)

    def local_sync(self, port):
        from verify_owner_update import verify
        result=verify(self.manifest,self.root,port)
        self.record('local',result)

    def local_ui(self, port):
        from verify_owner_ui import verify
        self.record('owner_ui',verify(self.version,self.root,port))

    def run(self, agent_directory, port, until=None):
        try:
            self.state['status']='incomplete';save(self.path,self.state)
            self.freeze();self.candidate()
            if until=='candidate':return
            if self.scope=='all':self.independent_windows(agent_directory,'candidate')
            published=self.state['steps'].get('feed_site',{})
            if not (published.get('version')==self.version and published.get('source_commit')==self.commit
                    and published.get('feed_and_site_published') is True):
                self.publish()
            # A recorded publication is revalidated from public addresses. Do not
            # reopen SSH or republish it when only a later acceptance step failed.
            self.verify_public()
            self.public_upgrades()
            if self.scope=='all':self.independent_windows(agent_directory,'upgrade')
            gh('release','edit',self.tag,'--repo',REPO,'--prerelease=false','--latest')
            self.verify_public(require_stable=True)
            if until=='published':return
            self.local_sync(port)
            self.local_ui(port)
            self.verify_public(require_stable=True)
            self.state['status']='complete';self.state['completed_at']=datetime.now(timezone.utc).isoformat()
            save(self.path,self.state);emit('complete',version=self.version,source_commit=self.commit,
                artifacts={n:r['sha256'] for n,r in self.records.items()})
        except Exception as exc:
            self.state.update(status='incomplete',error={'type':type(exc).__name__,'detail':str(exc)})
            save(self.path,self.state)
            raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',required=True);p.add_argument('--commit',required=True)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--windows-agent-dir',type=Path,
        default=Path('D:/Artifacts/SUSTechCampusDashboard/full-flow-20261010/windows-vm'))
    p.add_argument('--port',type=int,default=18765)
    p.add_argument('--platforms',choices=('windows','all'),default='windows',
                   help='Windows uses independent standard-user CI and the real owner instance; all also requires the legacy VM')
    p.add_argument('--until',choices=('candidate','published'),help='Stop incomplete for inspection; rerun without this option to finish')
    a=p.parse_args()
    lock=Path('D:/AppData/SUSTechCampusPublisher/unified-release.lock')
    lock.parent.mkdir(parents=True,exist_ok=True)
    with exclusive_file(lock):
        platforms=('windows-x86_64',) if a.platforms=='windows' else ('windows-x86_64','linux-x86_64')
        Release(a.version,a.commit,a.output,platforms).run(a.windows_agent_dir,a.port,a.until)


if __name__=='__main__':main()
