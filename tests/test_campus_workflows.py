import io
import uuid
from datetime import datetime,date
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import FileStorage
from sustech_dashboard import blackboard_work as work,venues
from sustech_dashboard.actions import Actions
from sustech_dashboard.core import CHINA_TZ,unique_name


class BlackboardFixture:
    def __init__(self,confirm=True,files_visible=True):
        self.sent=False;self.posts=[];self.confirm=confirm;self.files_visible=files_visible
        self.session=SimpleNamespace(post=self.post)
        self.kind='resource/x-bb-assignment';self.group=False
    def current_courses(self):return [{'id':'c','name':'Course'}]
    def results(self,path):
        if path.endswith('/columns'):return [{'id':'g','contentId':'a','grading':{'type':'Attempts','attemptsAllowed':1},'name':'Homework'}]
        if path.endswith('/attempts'):return [{'id':'attempt','status':'NeedsGrading'}] if self.sent and self.confirm else []
        return []
    def json(self,path):return {'body':'<p>Submit a PDF.</p>','contentHandler':{'id':self.kind,'groupContent':self.group}}
    def get(self,path):
        body='<form action="/webapps/assignment/uploadAssignment?action=submit"><input type="hidden" name="nonce" value="fresh"><input type="hidden" name="dispatch" value="save"></form>'
        if self.sent:
            body='<div id="currentAttempt_submissionList">'
            if self.files_visible:body+='<li><a class="attachment">answer.pdf</a><a class="dwnldBtn" href="/webapps/assignment/download?course_id=c&amp;attempt_id=attempt&amp;file_id=f&amp;fileName=answer.pdf"></a></li>'
            body+='</div>'
        return SimpleNamespace(text=body)
    def post(self,url,**kwargs):
        self.sent=True;self.posts.append(kwargs)
        return SimpleNamespace(json=lambda:{})


def attachment():return FileStorage(stream=io.BytesIO(b'%PDF-1.4 fixture'),filename='answer.pdf',content_type='application/pdf')


def test_submission_uses_fresh_nonce_multipart_and_verified_files():
    bb=BlackboardFixture()
    result=work.submit(bb,'c','a',[attachment()])
    assert result['state']=='confirmed'
    sent=bb.posts[0]['files'];values=dict(sent)
    assert values['nonce']==(None,'fresh')
    assert values['dispatch']==(None,'submit')
    assert values['newFile_LocalFile0'][0]=='answer.pdf'
    assert values['newFile_attachmentType']==(None,'L')
    assert len(bb.posts)==1


@pytest.mark.parametrize('confirm,visible',[(False,True),(True,False)])
def test_no_success_without_submitted_state_and_all_uploaded_files(confirm,visible):
    bb=BlackboardFixture(confirm,visible)
    assert work.submit(bb,'c','a',[attachment()])['state']=='needs_review'
    assert len(bb.posts)==1


@pytest.mark.parametrize('kind,group',[('resource/x-bb-asmt-test-link',False),('resource/x-bb-assignment',True)])
def test_test_and_group_tasks_never_receive_assignment_upload(kind,group):
    bb=BlackboardFixture();bb.kind=kind;bb.group=group
    with pytest.raises(ValueError):work.submit(bb,'c','a',[attachment()])
    assert not bb.posts


def test_no_overwrite_blank_or_duplicate_files():
    bb=BlackboardFixture()
    with pytest.raises(ValueError):work.submit(bb,'c','a',[])
    with pytest.raises(ValueError):work.submit(bb,'c','a',[attachment(),attachment()])
    bb.sent=True
    with pytest.raises(ValueError):work.submit(bb,'c','a',[attachment()])
    assert not bb.posts


def test_draft_files_are_preserved():
    bb=BlackboardFixture()
    original=bb.get
    bb.get=lambda path:SimpleNamespace(text=original(path).text.replace('</form>','<input name="newFile_fileId" value="old"></form>'))
    with pytest.raises(ValueError,match='草稿'):work.submit(bb,'c','a',[attachment()])
    assert not bb.posts


def test_rich_content_sanitization_and_proxy_boundaries():
    body=work.safe_html('<script>steal()</script><p onclick="bad()">Task<img src="https://elsewhere.test/track"><a href="javascript:bad()">bad</a><a href="/bbcswebdav/file.pdf">PDF</a><math><mi>x</mi></math></p>','/campus')
    assert 'steal' not in body and 'onclick' not in body and 'javascript' not in body
    assert 'elsewhere' not in body and '/campus/api/blackboard-resource' in body and 'x' in body
    with pytest.raises(ValueError):work.resource_path('https://elsewhere.test/file.pdf')
    with pytest.raises(ValueError):work.resource_path('/webapps/login/?action=logout')
    assert work.resource_path('/webapps/assignment/download?file_id=1').startswith('/webapps/assignment/download')


def test_actions_survive_uncertain_network_and_never_replay(tmp_path):
    actions=Actions(tmp_path/'actions.sqlite3');token=str(uuid.uuid4());calls=[]
    def uncertain():calls.append(1);raise TimeoutError('private token')
    first=actions.run(token,'assignment:c:a',uncertain)
    assert first['state']=='needs_review' and 'private' not in str(first)
    assert Actions(actions.path).run(token,'assignment:c:a',uncertain)==first
    with pytest.raises(ValueError):actions.run(str(uuid.uuid4()),'assignment:c:a',uncertain)
    assert calls==[1]
    actions.confirm('assignment:c:a',{'state':'confirmed','status':'submitted'})
    assert actions.pending('assignment:c:a') is None
    assert actions.run(token,'assignment:c:a',uncertain)['state']=='confirmed'


def test_actions_rejected_preflight_can_be_corrected(tmp_path):
    a=Actions(tmp_path/'actions.sqlite3')
    def reject():raise ValueError('请填写主题')
    assert a.run(str(uuid.uuid4()),'book',reject)['state']=='rejected'
    assert a.run(str(uuid.uuid4()),'book',lambda:{'state':'confirmed'})['state']=='confirmed'


def test_malformed_response_after_write_is_not_a_safe_rejection(tmp_path):
    from sustech_dashboard.actions import mark_sent
    actions=Actions(tmp_path/'actions.sqlite3')
    def work():
        mark_sent()
        raise ValueError('malformed server body')
    assert actions.run(str(uuid.uuid4()),'book',work)['state']=='needs_review'
    with pytest.raises(ValueError):actions.run(str(uuid.uuid4()),'book',work)


def room():return {'id':1,'bookable':True,'min_people':3,'max_people':7,'open':[{'openStartTime':'08:00','openEndTime':'21:59'}],'busy':[],
    'rules':{'minResvTime':10,'maxResvTime':120,'earliestResvTime':2880,'latestResvTime':0}}


def booking_payload():return {'system':'library','room_id':1,'category':'discussion','title':'Course discussion','members':[2,3],
    'begin':'2026-10-02T10:00:00+08:00','end':'2026-10-02T12:00:00+08:00'}


@pytest.fixture
def lib(monkeypatch):
    monkeypatch.setattr(venues,'now',lambda:datetime(2026,10,1,12,tzinfo=CHINA_TZ))
    return SimpleNamespace(whoami=lambda:SimpleNamespace(acc_no=1))


def test_room_interval_uses_full_interval_and_distinct_participants(lib):
    payload=booking_payload();r=room()
    assert venues.validate_library(payload,r,lib)[-1]==[1,2,3]
    r['busy']=[{'start':'2026-10-02T10:50:00+08:00','end':'2026-10-02T11:00:00+08:00'}]
    with pytest.raises(ValueError,match='不空闲'):venues.validate_library(payload,r,lib)
    r['busy']=[];payload['members']=[1,2,2]
    with pytest.raises(ValueError,match='不同'):venues.validate_library(payload,r,lib)


@pytest.mark.parametrize('begin,end',[('2026-10-02T07:00:00+08:00','2026-10-02T09:00:00+08:00'),('2026-10-02T10:00:00+08:00','2026-10-02T13:00:00+08:00'),('2026-10-04T10:00:00+08:00','2026-10-04T12:00:00+08:00')])
def test_library_official_open_duration_and_advance_rules(lib,begin,end):
    with pytest.raises(ValueError):venues.validate_library({**booking_payload(),'begin':begin,'end':end},room(),lib)


def test_duplicate_booking_readback_prevents_second_write(lib,monkeypatch):
    record={'resvDevInfoList':[{'devId':1}],'begin':booking_payload()['begin'],'end':booking_payload()['end']}
    monkeypatch.setattr(venues,'lib_client',lambda:lib)
    monkeypatch.setattr(venues,'schedules',lambda *a:{'rooms':[room()]})
    monkeypatch.setattr(venues,'library_mine',lambda *a:[record])
    assert venues.book(booking_payload())['state']=='confirmed'


def test_monitor_api_retired_and_week_fold_present():
    from sustech_dashboard.app import create_app
    client=create_app().test_client()
    for method,path in [('get','/api/room-watch'),('post','/api/room-watch'),('post','/api/room-watch/stop')]:assert getattr(client,method)(path).status_code==410
    page=client.get('/').data.decode()
    assert 'id="week-fold"' in page and 'room-panel' not in page
    assert client.get('/venues/library/discussion').status_code==200


def test_filename_preserves_openable_extension():
    assert Path(unique_name('file.pdf','key')).suffix=='.pdf'


def test_weekly_views_do_not_accumulate_duplicate_courses():
    from test_academic_calendar import make_calendar,patterns
    from sustech_dashboard.academic_calendar import daily_view
    term=make_calendar().fall
    for _ in range(8):view=daily_view(term,patterns(),date(2026,10,8))
    assert len(view['today_classes'])==1


def test_week_crossing_new_year_loads_both_holiday_years(monkeypatch):
    from sustech_survival.calendar import AcademicCalendar
    from sustech_survival.tis import schedule
    from test_academic_calendar import make_calendar,patterns
    from sustech_dashboard.academic_calendar import read_daily_calendar
    years=[]
    def load(year,**kw):years.append(year);return make_calendar(year)
    monkeypatch.setattr(AcademicCalendar,'load',load)
    monkeypatch.setattr(schedule,'class_times',lambda **kw:patterns())
    result=read_daily_calendar({'XN':'2026-2027','XQ':'1'},date(2026,12,30))
    assert years==[2026,2027] and len(result['week_schedule']['days'])==7


def test_ehall_uses_official_model_and_confirms_matching_record(monkeypatch):
    monkeypatch.setattr(venues,'now',lambda:datetime(2026,10,1,12,tzinfo=CHINA_TZ))
    client=SimpleNamespace(whoami=lambda:{'id':'user-id','groups':'Department'})
    calls=[]
    def call(method,data):
        calls.append((method,data))
        if method=='CheckMeetingTime':return {'Data':{'CanBook':True}}
        assert method=='AddMeeting';return {'Data':{}}
    client._call_raw=call
    monkeypatch.setattr(venues,'eh_client',lambda:client)
    monkeypatch.setattr(venues,'eh_rooms',lambda c:[{'MeetingRoomID':'r','IsAvailable':True,'MeetingRoomName':'Room','MeetingRoomType':'自习室','CapacityNumber':5}])
    monkeypatch.setattr(venues,'eh_options',lambda *a:{'types':[{'id':13}],'styles':[{'id':20}]})
    payload={**booking_payload(),'system':'ehall','room_id':'r','memo':'Study','meeting_type':13,'meeting_style':20,'members':[]}
    reads=[]
    def mine(*args):
        reads.append(1)
        return [] if len(reads)==1 else [{'MeetingRoomID':'r','begin':payload['begin'],'end':payload['end']}]
    monkeypatch.setattr(venues,'eh_mine',mine)
    assert venues.book(payload)['state']=='confirmed'
    model=next(d['Model'] for m,d in calls if m=='AddMeeting')
    assert model['MeetingType']==13 and model['MeetingStyle']==20
    assert model['MeetingParticipantModels']==[{'UserID':'user-id'}]
    assert model['MeetingRoomModel']['MeetingRoomID']=='r' and model['NotifyType']==666


def test_booking_upload_route_keeps_files_and_payload(monkeypatch,tmp_path):
    import json,re
    from sustech_dashboard import app,actions
    monkeypatch.setattr(app,'CLOUD',False)
    monkeypatch.setattr(actions,'DATA_ROOT',tmp_path)
    received=[]
    def book(payload,files):
        received.append((payload,[f.filename for f in files],[f.read() for f in files]))
        return {'state':'confirmed','message':'fixture'}
    monkeypatch.setattr(venues,'book',book)
    client=app.create_app().test_client()
    token=re.search(r'const roomCsrf="([^"]+)"',client.get('/').text).group(1)
    payload={**booking_payload(),'system':'ehall','operation_id':str(uuid.uuid4())}
    response=client.post('/api/venues/book',data={'payload':json.dumps(payload),'files':(io.BytesIO(b'PDF'),'request.pdf')},
        headers={'Origin':'http://localhost','X-CSRF-Token':token})
    assert response.status_code==200 and received[0][1:]==(['request.pdf'],[b'PDF'])


def test_reservation_details_require_own_record(monkeypatch):
    monkeypatch.setattr(venues,'eh_mine',lambda *args:[])
    def forbidden():raise AssertionError('Must not access an unowned reservation')
    monkeypatch.setattr(venues,'eh_client',forbidden)
    with pytest.raises(ValueError):venues.reservation_detail('ehall','other-id')


def test_resource_proxy_preserves_filename_without_upstream_disposition(monkeypatch):
    from sustech_dashboard import app
    from urllib.parse import unquote
    monkeypatch.setattr(app,'CLOUD',False)
    upstream=SimpleNamespace(headers={'Content-Type':'application/pdf'},iter_content=lambda n:iter([b'%PDF']),close=lambda:None)
    monkeypatch.setattr(app,'Blackboard',lambda:SimpleNamespace(get=lambda *a,**kw:upstream))
    client=app.create_app().test_client()
    response=client.get('/api/blackboard-resource',query_string={'path':'/bbcswebdav/fixture.pdf','name':'习题.pdf'})
    assert unquote(response.headers['Content-Disposition']).endswith('习题.pdf')
    assert response.data==b'%PDF'
    response.close()
    assert app._bb_lock.acquire(blocking=False)
    app._bb_lock.release()
