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
