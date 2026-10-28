import json,time,uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from sustech_dashboard import printing
from sustech_dashboard.actions import Actions

class Response:
    def __init__(self,status=302,location='',body=None):self.status_code=status;self.headers={'Location':location};self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def json(self):return self.body

@pytest.mark.parametrize('response,state',[
 (Response(location='https://pms.sustech.edu.cn/client/new/cprintPc/printDoc.html?code=0&message='),'accepted'),
 (Response(location='/client/new/cprintPc/printDoc.html?code=5&message=unsupported'),'rejected'),
 (Response(status=200,body={'code':0}),'accepted'),
 (Response(status=200,body=['unexpected']),'needs_review'),
 (Response(location='https://evil.example/printDoc.html?code=0'),'needs_review'),
 (Response(status=307,location='https://cas.sustech.edu.cn/login'),'needs_review'),
 (Response(status=413),'rejected')])
def test_upload_uses_official_backurl_and_never_repeats_redirected_post(tmp_path,response,state):
    path=tmp_path/'sample.pdf';path.write_bytes(b'%PDF-fixture');calls=[]
    def post(url,**kwargs):
        calls.append(1)
        assert url=='https://pms.sustech.edu.cn/api/client/CloudPrint/Upload'
        assert kwargs['allow_redirects'] is False
        assert kwargs['data']['BackURL'].startswith('https://pms.sustech.edu.cn/client/new/cprintPc/printDoc.html?t=')
        assert kwargs['data']['BackURL'].split('=')[-1].isdigit()
        assert kwargs['files']['szPath'][1].read()==b'%PDF-fixture'
        return response
    c=SimpleNamespace(session=SimpleNamespace(post=post))
    assert printing.send_upload(c,path,printing.options({}))['state']==state
    assert calls==[1]

def receipt():return {'filename':'sample.pdf','options':printing.options({'duplex':2}),'before_ids':[1],'at':time.time()}
def job(**kw):return SimpleNamespace(**{'dw_job_id':2,'file_name':'sample.pdf','dw_copies':1,'is_color':False,'duplex_label':'双面短边','paper':'A4',**kw})

@pytest.mark.parametrize('changes',[{'dw_job_id':1},{'dw_job_id':'1'},{'dw_copies':2},{'is_color':True},{'duplex_label':'双面长边'},{'paper':'A3'},{'file_name':'other.pdf'}])
def test_readback_does_not_match_wrong_or_existing_document(changes):
    assert printing.match_upload([job(**changes)],receipt()) is None

def test_delayed_readback_survives_restart_without_reupload(tmp_path,monkeypatch):
    ledger=Actions(tmp_path/'actions.sqlite3');token=str(uuid.uuid4());target='print:upload:fixture'
    monkeypatch.setattr(printing,'DATA_ROOT',tmp_path)
    polls=[];sent=[]
    monkeypatch.setattr(printing.time,'sleep',lambda _:None)
    c=SimpleNamespace(list_print_jobs=lambda:polls.append(1) or [],upload_print=lambda *a,**kw:None)
    monkeypatch.setattr(printing,'client',lambda:c)
    monkeypatch.setattr(printing,'send_upload',lambda *a:sent.append(1) or {'state':'accepted'})
    opts=printing.options({'duplex':2})
    result=ledger.run(token,target,lambda:printing.upload('sample.pdf',b'%PDF-fixture',opts))
    assert result['state']=='needs_review' and sent==[1]
    assert ledger.run(token,target,lambda:pytest.fail('Never repeat upload'))==result
    values=printing.reconcile_pending([job()],Actions(ledger.path))
    assert values[0]['operation_id']==token and values[0]['state']=='confirmed'
    assert ledger.run(token,target,lambda:pytest.fail('Never repeat upload'))['state']=='confirmed'
    assert sent==[1]

def test_presend_network_failure_is_rejected_not_uncertain(tmp_path,monkeypatch):
    monkeypatch.setattr(printing,'client',lambda:(_ for _ in ()).throw(ConnectionError()))
    result=printing.upload('sample.pdf',b'%PDF-fixture',printing.options({}))
    assert result['state']=='rejected' and result['phase']=='connect'

def test_transmission_exception_still_confirms_only_matching_queue(tmp_path,monkeypatch):
    monkeypatch.setattr(printing,'DATA_ROOT',tmp_path);calls=[]
    c=SimpleNamespace(list_print_jobs=lambda: calls.append(1) or ([] if len(calls)==1 else [job()]),upload_print=lambda *a,**kw:None)
    monkeypatch.setattr(printing,'client',lambda:c)
    monkeypatch.setattr(printing,'send_upload',lambda *a:(_ for _ in ()).throw(ConnectionError()))
    assert printing.upload('sample.pdf',b'%PDF-fixture',printing.options({'duplex':2}))['state']=='confirmed'

def test_agent_crashed_checkpoint_is_uncertain_not_replayed(tmp_path):
    from sustech_dashboard.print_agent import execute
    ledger=Actions(tmp_path/'actions.sqlite3');token=str(uuid.uuid4());target='print:upload:fixture'
    with ledger.connect() as db:
        db.execute('INSERT INTO actions VALUES (?,?,?,?,?)',(token,target,'sending',json.dumps({'state':'sending','receipt':receipt()}),time.time()))
    result=execute({'id':token,'kind':'upload','payload':{},'target':target,'recovery_only':True},lambda _:pytest.fail('Never upload during recovery'),ledger)
    assert result['state']=='needs_review' and result['receipt']['filename']=='sample.pdf'

def test_same_name_pending_documents_cannot_share_one_queue_confirmation(tmp_path):
    ledger=Actions(tmp_path/'actions.sqlite3')
    with ledger.connect() as db:
        for target in ('first-content','different-content'):
            db.execute('INSERT INTO actions VALUES (?,?,?,?,?)',(str(uuid.uuid4()),'print:upload:'+target,'needs_review',json.dumps({'state':'needs_review','receipt':receipt()}),time.time()))
    assert printing.reconcile_pending([job()],ledger)==[]

def test_same_named_different_content_is_blocked_before_second_post(tmp_path):
    from sustech_dashboard.actions import record_evidence,mark_sent
    ledger=Actions(tmp_path/'actions.sqlite3');sent=[]
    def work():
        record_evidence({'guard':'same-name-and-settings'})
        sent.append(1);mark_sent();raise ConnectionError()
    assert ledger.run(str(uuid.uuid4()),'print:upload:content-A',work)['state']=='needs_review'
    assert ledger.run(str(uuid.uuid4()),'print:upload:content-B',work)['state']=='rejected'
    assert sent==[1]
