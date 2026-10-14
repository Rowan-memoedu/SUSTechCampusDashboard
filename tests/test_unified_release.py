import importlib.util
import json
from pathlib import Path
import sys

import pytest

DEPLOY=Path(__file__).parents[1]/'deploy'
sys.path.insert(0,str(DEPLOY))
import release


def manifest():
    return {'version':'1.2.3','source_commit':'a'*40,'assets':[
        {'filename':name,'sha256':'b'*64,'size':10} for name in
        ('windows-x86_64.zip','linux-x86_64.zip','SUSTechCampusDashboard-1.2.3-windows-x86_64-setup.exe')]}


def test_same_version_cannot_change_commit_or_platform_artifact_set(tmp_path):
    release.Release('1.2.3','a'*40,tmp_path).record('frozen',{'source_commit':'a'*40})
    with pytest.raises(ValueError,match='different source'):
        release.Release('1.2.3','c'*40,tmp_path)
    m=manifest();release.validate_manifest(m,'1.2.3','a'*40)
    with pytest.raises(ValueError,match='frozen source'):
        release.validate_manifest(m,'1.2.3','c'*40)
    m['assets'][1]=m['assets'][0]
    with pytest.raises(ValueError,match='asset list'):
        release.validate_manifest(m,'1.2.3','a'*40)


def test_windows_scope_uses_exact_windows_assets_and_cannot_change_on_resume(tmp_path):
    job=release.Release('1.2.3','a'*40,tmp_path,('windows-x86_64',))
    job.record('frozen',{'source_commit':'a'*40})
    with pytest.raises(ValueError,match='platform scope'):
        release.Release('1.2.3','a'*40,tmp_path)
    m=manifest();m['assets']=[a for a in m['assets'] if a['filename']!='linux-x86_64.zip']
    release.validate_manifest(m,'1.2.3','a'*40,job.platforms)
    with pytest.raises(ValueError,match='asset list'):
        release.validate_manifest(m,'1.2.3','a'*40)


def test_windows_release_requires_independent_ci_upgrade_and_real_owner(tmp_path,monkeypatch):
    job=release.Release('1.2.3','a'*40,tmp_path,('windows-x86_64',));job.records={}
    calls=[]
    for name in ('freeze','candidate','publish','verify_public','public_upgrades','local_sync','local_ui'):
        monkeypatch.setattr(job,name,lambda *a,_name=name,**k:calls.append(_name))
    monkeypatch.setattr(job,'independent_windows',lambda *a:pytest.fail('Windows scope must not depend on the legacy VM'))
    monkeypatch.setattr(release,'gh',lambda *a:'')
    job.run(tmp_path,18765)
    assert calls.index('candidate') < calls.index('public_upgrades') < calls.index('local_sync') < calls.index('local_ui')
    assert job.state['status']=='complete' and job.state['platforms']==['windows-x86_64']


def test_windows_public_upgrade_does_not_dispatch_linux(tmp_path,monkeypatch):
    job=release.Release('1.2.3','a'*40,tmp_path,('windows-x86_64',))
    job.state['previous']={'windows-x86_64.zip':{'sha256':'b'*64}}
    calls=[]
    monkeypatch.setattr(job,'workflow',lambda *a:calls.append(a) or {'conclusion':'success'})
    job.public_upgrades()
    assert calls[0][2]['platforms']=='windows' and 'previous_linux' not in calls[0][2]


@pytest.mark.parametrize('alteration',['none','changed','removed','added'])
def test_partial_publication_preserves_the_other_platform(alteration):
    from types import SimpleNamespace
    from feed_metadata import verify_preserved_targets
    old=SimpleNamespace(to_dict=lambda:{'sha256':'old','version':'1.0.0'})
    new=SimpleNamespace(to_dict=lambda:{'sha256':'new','version':'2.0.0'})
    before={'windows-x86_64.zip':old,'linux-x86_64.zip':old}
    after=dict(before,**{'windows-x86_64.zip':new})
    if alteration=='changed':after['linux-x86_64.zip']=new
    if alteration=='removed':del after['linux-x86_64.zip']
    if alteration=='added':after['other.zip']=new
    if alteration=='none':verify_preserved_targets(before,after,{'windows-x86_64.zip'})
    else:
        with pytest.raises(ValueError,match='Unselected platform'):
            verify_preserved_targets(before,after,{'windows-x86_64.zip'})


def test_resume_reuses_successful_workflow_and_never_dispatches_a_rebuild(tmp_path,monkeypatch):
    run={'id':42,'display_title':'Candidate v1.2.3','status':'completed','conclusion':'success','html_url':'https://github.com/fixture/42'}
    calls=[]
    def gh(*args):
        calls.append(args)
        assert args[0]=='api', 'resume must not dispatch or rerun successful builds'
        return json.dumps({'workflow_runs':[run]} if '/workflows/' in args[1] else run)
    monkeypatch.setattr(release,'gh',gh)
    job=release.Release('1.2.3','a'*40,tmp_path)
    assert job.workflow('build-release.yml','Candidate v1.2.3',{},'build_run')['run_id']==42


def test_public_success_followed_by_local_failure_stays_incomplete(tmp_path,monkeypatch):
    job=release.Release('1.2.3','a'*40,tmp_path)
    for name in ('freeze','candidate','independent_windows','publish','verify_public','public_upgrades'):
        monkeypatch.setattr(job,name,lambda *a,**k:None)
    monkeypatch.setattr(release,'gh',lambda *a:'')
    def fail(*args):raise ValueError('actual installed artifact differs')
    monkeypatch.setattr(job,'local_sync',fail)
    with pytest.raises(ValueError,match='installed artifact'):
        job.run(tmp_path,18765)
    assert release.read_json(job.path)['status']=='incomplete'


def test_retention_compares_preexisting_data_without_rejecting_new_downloads():
    from verify_owner_update import preserved
    before={'private':{'credential':'a'},'baseline':'start','files':{'one':'b'},'actions':{'action':'needs_review'}}
    after=dict(before,files={'one':'b','new':'c'})
    assert preserved(before,after)
    assert not preserved(before,dict(after,files={'one':'different'}))
    assert not preserved(before,dict(after,actions={'action':'confirmed'}))


def test_browser_acceptance_waits_for_async_launch_and_rejects_missing_browser(monkeypatch):
    import verify_independent_windows as guest
    now=[0]
    monkeypatch.setattr(guest.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(guest.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    replies=iter(['false','false','true'])
    guest.wait_browser(lambda _:next(replies),True,timeout=10)
    assert now[0]==6
    with pytest.raises(ValueError,match='did not open'):
        guest.wait_browser(lambda _:'false',True,timeout=6)
    with pytest.raises(ValueError,match='did not close'):
        guest.wait_browser(lambda _:'true',False,timeout=6)


def test_interrupted_upload_resumes_and_reuses_only_verified_remote_file(tmp_path,monkeypatch):
    source=tmp_path/'linux-x86_64.zip';source.write_bytes(b'fixed release bytes')
    remote='/tmp/sustech-release-1.2.3-aaaaaaaaaaaa/linux-x86_64.zip'
    state={'hash':None,'attempts':0}
    def command(args,**kwargs):
        if args[0]=='ssh':return (state['hash']+'  '+remote) if state['hash'] else ''
        assert args[0]=='scp'
        state['attempts']+=1
        if state['attempts']==1:
            state['hash']='partial'
            raise RuntimeError('connection reset')
        state['hash']=release.digest(source)
        return ''
    monkeypatch.setattr(release,'command',command)
    with pytest.raises(RuntimeError,match='connection reset'):release.upload_file(source,remote)
    release.upload_file(source,remote)
    release.upload_file(source,remote)
    assert state['attempts']==2


def test_owner_acceptance_waits_for_pointer_and_success_record(tmp_path,monkeypatch):
    import verify_owner_update as owner
    now=[0]
    def commit_after_backend_started(delay):
        now[0]+=delay
        release.save(tmp_path/'updates/current.json',{'directory':'new','version':'1.2.3'})
        if now[0]>=4:
            release.save(tmp_path/'updates/last-result.json',{'state':'installed','version':'1.2.3'})
    release.save(tmp_path/'updates/current.json',{'directory':'old','version':'1.2.2'})
    release.save(tmp_path/'updates/last-result.json',{'state':'installed','version':'1.2.2'})
    monkeypatch.setattr(owner.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(owner.time,'sleep',commit_after_backend_started)
    assert owner.committed_selection(tmp_path,'1.2.3',10)==(tmp_path/'updates/releases/new').resolve()
    assert now[0]==4


def test_owner_acceptance_rejects_uncommitted_or_rolled_back_selection(tmp_path,monkeypatch):
    import verify_owner_update as owner
    now=[0]
    monkeypatch.setattr(owner.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(owner.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    release.save(tmp_path/'updates/current.json',{'directory':'new','version':'1.2.3'})
    release.save(tmp_path/'updates/last-result.json',{'state':'installed','version':'1.2.2'})
    with pytest.raises(TimeoutError,match='not committed'):
        owner.committed_selection(tmp_path,'1.2.3',4)
    release.save(tmp_path/'updates/last-result.json',{'state':'rolled_back','failed_version':'1.2.3'})
    with pytest.raises(RuntimeError,match='rolled back'):
        owner.committed_selection(tmp_path,'1.2.3',4)


@pytest.mark.parametrize('public_changed',[False,True])
def test_resume_after_publication_needs_public_validation_but_no_republication(tmp_path,monkeypatch,public_changed):
    job=release.Release('1.2.3','a'*40,tmp_path);job.records={}
    job.record('feed_site',{'version':'1.2.3','source_commit':'a'*40,'feed_and_site_published':True})
    for name in ('freeze','candidate','independent_windows','public_upgrades','local_ui'):
        monkeypatch.setattr(job,name,lambda *a,**k:None)
    def unexpected_publish():raise AssertionError('A completed publication must not reopen SSH')
    monkeypatch.setattr(job,'publish',unexpected_publish)
    monkeypatch.setattr(release,'gh',lambda *a:'')
    verified=[];local=[]
    def public(**kwargs):
        verified.append(True)
        if public_changed:raise ValueError('Public identity changed')
    monkeypatch.setattr(job,'verify_public',public)
    monkeypatch.setattr(job,'local_sync',lambda *a:local.append(True))
    if public_changed:
        with pytest.raises(ValueError,match='Public identity changed'):job.run(tmp_path,18765)
        assert not local and job.state['status']=='incomplete'
    else:
        job.run(tmp_path,18765)
        assert local==[True] and job.state['status']=='complete'
    assert verified


class DownloadResponse:
    def __init__(self,status,chunks,content_range=None):
        self.status_code=status;self.chunks=chunks
        self.headers={'Content-Range':content_range} if content_range else {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def iter_content(self,size):
        for chunk in self.chunks:
            if isinstance(chunk,Exception):raise chunk
            yield chunk


def download_session(monkeypatch,responses):
    from types import SimpleNamespace
    calls=[];responses=iter(responses)
    def get(*args,**kwargs):
        calls.append(kwargs['headers']);return next(responses)
    monkeypatch.setattr(release.requests,'Session',lambda:SimpleNamespace(get=get))
    monkeypatch.setattr(release.time,'sleep',lambda _:None)
    return calls


def test_public_download_resumes_interrupted_response_and_checks_complete_hash(tmp_path,monkeypatch):
    import hashlib
    calls=download_session(monkeypatch,[
        DownloadResponse(200,[b'ab',release.requests.ConnectionError('interrupted')]),
        DownloadResponse(206,[b'cdef'],'bytes 2-5/6')])
    path=tmp_path/'fixed.zip'
    release.download('https://fixture.invalid/fixed.zip',path,hashlib.sha256(b'abcdef').hexdigest())
    assert path.read_bytes()==b'abcdef' and calls[1]['Range']=='bytes=2-'
    assert not path.with_suffix('.zip.partial').exists()


@pytest.mark.parametrize('status,interval,expected_success',[(200,None,True),(206,'bytes 3-5/6',False)])
def test_public_download_handles_ignored_range_and_rejects_wrong_range(tmp_path,monkeypatch,status,interval,expected_success):
    import hashlib
    path=tmp_path/'fixed.zip';partial=path.with_suffix('.zip.partial');partial.write_bytes(b'ab')
    download_session(monkeypatch,[DownloadResponse(status,[b'abcdef'],interval)])
    if expected_success:
        release.download('https://fixture.invalid/fixed.zip',path,hashlib.sha256(b'abcdef').hexdigest())
        assert path.read_bytes()==b'abcdef'
    else:
        with pytest.raises(ValueError,match='Invalid resumed download range'):
            release.download('https://fixture.invalid/fixed.zip',path,hashlib.sha256(b'abcdef').hexdigest())
        assert not path.exists() and partial.read_bytes()==b'ab'


def test_public_download_does_not_accept_corrupt_complete_file(tmp_path,monkeypatch):
    import hashlib
    path=tmp_path/'fixed.zip'
    download_session(monkeypatch,[DownloadResponse(200,[b'wrong'])])
    with pytest.raises(ValueError,match='hash mismatch'):
        release.download('https://fixture.invalid/fixed.zip',path,hashlib.sha256(b'abcdef').hexdigest())
    assert not path.exists()


@pytest.mark.parametrize('url,use_proxy',[
    ('https://github.com/owner/project/releases/download/v1/package.zip',True),
    ('https://124.221.144.155/campus-updates/targets/package.zip',False),
    ('https://github.com.other.invalid/package.zip',False)])
def test_public_github_download_reuses_existing_windows_proxy_only(tmp_path,monkeypatch,url,use_proxy):
    from types import SimpleNamespace
    import hashlib
    session=SimpleNamespace(get=lambda *a,**k:DownloadResponse(200,[b'artifact']))
    monkeypatch.setattr(release.requests,'Session',lambda:session)
    monkeypatch.setattr(release,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(release,'proxy_bypass',lambda _:False)
    monkeypatch.setattr(release,'getproxies',lambda:{'https':'http://127.0.0.1:7897','no':'localhost'})
    release.download(url,tmp_path/'package.zip',hashlib.sha256(b'artifact').hexdigest())
    assert getattr(session,'proxies',{})==({'https':'http://127.0.0.1:7897'} if use_proxy else {})
    assert session.trust_env is False


def test_no_proxy_only_environment_keeps_the_enabled_windows_proxy(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from urllib import request
    import hashlib
    session=SimpleNamespace(get=lambda *a,**k:DownloadResponse(200,[b'artifact']))
    monkeypatch.setattr(release.requests,'Session',lambda:session)
    monkeypatch.setattr(release,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(release,'proxy_bypass',lambda _:False)
    monkeypatch.setattr(release,'getproxies',lambda:{'no':'localhost,127.0.0.1'})
    monkeypatch.setattr(request,'getproxies_registry',lambda:{'http':'http://127.0.0.1:7897','https':'http://127.0.0.1:7897'},raising=False)
    release.download('https://github.com/owner/repo/package.zip',tmp_path/'package.zip',hashlib.sha256(b'artifact').hexdigest())
    assert session.proxies['https']=='http://127.0.0.1:7897' and not session.trust_env
