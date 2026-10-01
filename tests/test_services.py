import uuid
from datetime import date,datetime,timedelta
from types import SimpleNamespace

import pytest
from sustech_dashboard import study,printing,classrooms
from sustech_dashboard.actions import Actions
from sustech_dashboard.core import CHINA_TZ


def grade(**kwargs):
    return study.normalize_grade({'kcdm':'MA1','kcmc':'Math','xf':4,'xnxqmc':'2026秋季','xscj':'A',**kwargs})


def test_gpa_excludes_pass_and_unknown_and_uses_school_numeric_formula():
    assert grade()['point']==3.94
    assert grade(xscj='P',zzcj=99)['point'] is None
    assert grade(xscj='',zzcj=90)['point'] is None
    assert grade(xscj='',zzcj=90,jfzlbmc='百分制')['point']==3.81
    assert grade(xscj='F',jfzlbmc='二级制')['point'] is None
    assert grade(xscj='F',jfzlbmc='十三级制')['point']==0
    assert grade(xscj='F',jd=0)['point'] is None
    assert grade(xscj='',jd=0)['point'] is None
    assert study.grade_summary([])['gpa'] is None
    assert study.grade_summary([grade(),grade(kcdm='MA2',xscj='')])['gpa'] is None


def test_credits_retakes_and_scope_are_not_inflated():
    g=study.grade_summary([grade(xscj='B'),grade(),grade(kcdm='PE1',xscj='P',xf=1)])
    assert g['courses']==2 and g['earned_credits']==5 and g['gpa']==3.94
    assert study.grade_term({'xnxqmc':'2026春季'}) < study.COURSE_CUTOFF
    assert study.grade_term({'xn':'2026-2027','xq':'1'})==study.COURSE_CUTOFF
    assert study.grade_term({}) is None
    assert study.grade_summary([grade(cjbzmc='旷考')])['gpa'] is None
    assert "'=HYPERLINK" in study.grade_csv([grade(kcmc='=HYPERLINK("x")')])


def course(code,day=1,weeks=(1,2),start=1,end=2):
    return {'code':code,'rwh':code,'slots_raw':[{'day':day,'weeks':list(weeks),'period_start':start,'period_end':end}]}


def test_solver_respects_week_parity_fixed_courses_and_full_coverage():
    a,b,c=course('a'),course('b'),course('b',day=2)
    result,_=study.solve_groups([[a],[b,c]],[])
    assert result==[[a,c]]
    assert study.solve_groups([[a],[b]],[])[0]==[]
    assert study.solve_groups([[a]],[b])[0]==[]
    assert not study.conflicts(course('a',weeks=(1,3)),course('b',weeks=(2,4)))
    assert study.solve_groups([[{'code':'z','slots_raw':[]}]],[])[0]==[]


def test_expired_round_never_prepares_a_write(monkeypatch):
    sem=SimpleNamespace(xn='2026-2027',xq='1')
    monkeypatch.setattr(study,'semester',lambda *a:sem)
    monkeypatch.setattr(study,'enrollment',lambda *a,**kw:{'xkgzszList':[{'xkfsdm':'r','ksrq':'2026-01-01','jsrq':'2026-01-02'}]})
    with pytest.raises(ValueError,match='结束'):
        study.prepare_selection({'action':'drop','rwh':'test','round_code':'r'})


@pytest.mark.parametrize('where',['catalog','cart'])
def test_catalog_and_cart_candidates_can_reach_dry_run_but_never_write(monkeypatch,where):
    import importlib
    mod=importlib.import_module('sustech_survival.selectcourse.selectcourse')
    sem=SimpleNamespace(xn='2026-2027',xq='1')
    start=(datetime.now(CHINA_TZ)-timedelta(hours=1)).isoformat()
    end=(datetime.now(CHINA_TZ)+timedelta(hours=1)).isoformat()
    cfg={'xkfsdm':'r','ksrq':start,'jsrq':end,'sfkx':'1'}
    raw={'rwh':'rwh','id':'official-id','xkfsdm':'r','xkxs':3}
    c={**course('x'),'name':'X','credits':2}
    c['rwh']='rwh'
    hit=SimpleNamespace(rwh='rwh',id='official-id')
    sends=[]
    def dry(rwh,**kw):
        sends.append(kw)
        assert kw['dry_run'] is True
        return {'endpoint':'/Xsxk/addXuanke','would_post':{'p_dqxn':'2026-2027','p_dqxq':'1'}}
    fake=SimpleNamespace(search_personal=lambda **kw:{'ok':True,'courses':[hit] if where=='catalog' else [],'current_type':{'jfxs':100}},add_course=dry)
    monkeypatch.setattr(mod,'SelectCourseClient',lambda **kw:fake)
    monkeypatch.setattr(study,'semester',lambda *a:sem)
    monkeypatch.setattr(study,'enrollment',lambda *a,**kw:{'xkgzszList':[cfg],'yxkcList':[],'xkgwcList':[raw] if where=='cart' else []})
    monkeypatch.setattr(study,'course_dict',lambda x:c)
    monkeypatch.setattr(study,'member',lambda x:c)
    out=study.prepare_selection({'action':'add','rwh':'rwh','round_code':'r','where':where,'bid':3})
    assert out['summary']['bid']==3 and len(sends)==1


@pytest.mark.parametrize('action,rows,expected',[('add',[{'rwh':'r'}],'confirmed'),('add',[],'needs_review'),('drop',[],'confirmed'),('drop',[{'rwh':'r'}],'needs_review'),('bid',[{'rwh':'r','xkxs':3}],'needs_review'),('bid',[{'rwh':'r','xkxs':5}],'confirmed')])
def test_selection_requires_official_membership_and_bid_readback(monkeypatch,action,rows,expected):
    p={'request':{'rwh':'r'},'sem':('2026-2027','1'),'summary':{'action':action,'where':'enrolled','bid':5},'wire':{'endpoint':'/Xsxk/addXuanke','would_post':{'id':'fresh'}}}
    monkeypatch.setattr(study,'prepare_selection',lambda d:p)
    monkeypatch.setattr(study,'semester',lambda *a:None)
    monkeypatch.setattr(study,'enrollment',lambda *a,**kw:{'yxkcList':rows,'xkgwcList':[]})
    sent=[]
    def post(*a,**kw):
        sent.append(kw);return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'jg':'1'})
    monkeypatch.setattr(study,'auth',lambda:SimpleNamespace(session=SimpleNamespace(post=post)))
    assert study.commit_selection(p)['state']==expected
    assert len(sent)==1


def test_print_upload_uses_tempfile_then_verifies_new_id_and_options(monkeypatch,tmp_path):
    monkeypatch.setattr(printing,'DATA_ROOT',tmp_path)
    calls=[];sent=[]
    job=SimpleNamespace(dw_job_id=7,file_name='a.pdf',dw_copies=2,is_color=False,is_duplex=True)
    def jobs():calls.append(1);return [] if len(calls)==1 else [job]
    def upload(path,**kw):
        sent.append(kw);assert path.read_bytes()==b'%PDF test';return SimpleNamespace(ok=True)
    monkeypatch.setattr(printing,'client',lambda:SimpleNamespace(list_print_jobs=jobs,upload_print=upload))
    opts=printing.options({'copies':2,'duplex':3})
    assert printing.upload('a.pdf',b'%PDF test',opts)['state']=='confirmed'
    assert sent[0]['dry_run'] is True and len(sent)==2
    assert not list((tmp_path/'print-staging').iterdir())


def test_ambiguous_print_write_is_not_replayed(monkeypatch,tmp_path):
    store=Actions(tmp_path/'actions.sqlite3');calls=[]
    def work():
        calls.append(1);from sustech_dashboard.actions import mark_sent
        mark_sent();raise ValueError('parse failed after send')
    token=str(uuid.uuid4())
    assert store.run(token,'print:upload:hash',work)['state']=='needs_review'
    assert store.run(token,'print:upload:hash',work)['state']=='needs_review'
    with pytest.raises(ValueError):store.run(str(uuid.uuid4()),'print:upload:hash',work)
    assert len(calls)==1


def test_missing_classroom_code_is_not_reported_as_verified_free(monkeypatch):
    sem=SimpleNamespace(xn='2026-2027',xq='1')
    monkeypatch.setattr(classrooms,'semester',lambda *a:sem)
    monkeypatch.setattr(classrooms,'room_inventory',lambda *a:[{'name':'一教110','capacity':80}])
    monkeypatch.setattr(classrooms,'calendar_day',lambda *a:({'kind':'holiday'},4,4,4))
    monkeypatch.setattr(classrooms,'client',lambda *a:SimpleNamespace(rooms=lambda:[SimpleNamespace(name='一教110')],_room_code_for_name=lambda n:None))
    result=classrooms.detail({'date':'2026-10-01','name':'一教110'})
    assert result['verified'] is False


def test_holiday_suppresses_courses_but_preserves_borrowings(monkeypatch):
    sem=SimpleNamespace(xn='2026-2027',xq='1')
    entry=lambda borrowing:SimpleNamespace(is_borrowing=borrowing,is_course=not borrowing,active_on=lambda w,d:True,type='borrowing' if borrowing else 'undergrad',course_name='课程' if not borrowing else None,purpose='借用',period_start=1)
    monkeypatch.setattr(classrooms,'semester',lambda *a:sem)
    monkeypatch.setattr(classrooms,'room_inventory',lambda *a:[{'name':'一教110','capacity':80}])
    monkeypatch.setattr(classrooms,'calendar_day',lambda *a:({'kind':'holiday'},4,4,4))
    live=SimpleNamespace(query_room=lambda *a,**kw:[entry(False),entry(True)])
    monkeypatch.setattr(classrooms,'client',lambda *a:SimpleNamespace(rooms=lambda:[SimpleNamespace(name='一教110')],_room_code_for_name=lambda n:'code',_live_client=live))
    assert [e['type'] for e in classrooms.detail({'date':'2026-10-01','name':'一教110'})['entries']]==['borrowing']


def test_service_pages_and_csrf_gate(monkeypatch):
    from sustech_dashboard import app as module
    monkeypatch.setattr(module,'CLOUD',False)
    app=module.create_app();client=app.test_client()
    for path in ('grades','printing','selection','venues/classroom/free'):
        assert client.get('/'+path,base_url='http://127.0.0.1').status_code==200
    r=client.post('/api/selection/commit',json={'confirmed':True},base_url='http://127.0.0.1')
    assert r.status_code==400


def test_ics_uses_current_period_times_and_calendar_dates():
    from sustech_dashboard.course_export import serialize
    c={**course('c',start=5,end=6),'name':'课程，演示','teachers':['老师']}
    term=SimpleNamespace(dates=lambda ct:[date(2026,10,8)])
    text=serialize(term,[c]);assert '20261008T060000Z' in text
    assert '20261008T070000Z' in text and 'DTSTART:20261001' not in text
    assert text.count('BEGIN:VEVENT')==2
    assert all(len(line.encode())<=75 for line in text.split('\r\n'))
