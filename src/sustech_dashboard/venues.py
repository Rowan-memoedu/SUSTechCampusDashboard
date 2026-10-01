"""Student-facing library and E-Hall booking operations, using official wire fields."""
from datetime import date, datetime, timedelta
import re
import uuid
from .core import CHINA_TZ, parse_dt
from .provider import _jsonable
from .room_monitor import is_free
from .actions import mark_sent


def lib_client():
    from sustech_survival.lib.booking import lib_booking
    return lib_booking()


def eh_client():
    from sustech_survival.booking import booking
    return booking()


def now():return datetime.now(CHINA_TZ)


def interval(payload):
    begin,end=parse_dt(payload.get('begin')),parse_dt(payload.get('end'))
    if not begin or not end or begin.date()!=end.date() or begin>=end:
        raise ValueError('请选择同一天的有效开始和结束时间')
    return begin,end


def library_kinds(client):
    # The official menu carries kind IDs, unlike the homepage's count-only labels.
    kinds=[]
    for lab in client.labs():
        raw=client._call('GET','/devKind/labDevKinds',params={'classKind':1,'labIds':lab.lab_id}) or []
        for item in raw:
            kid=item.get('kindId') or item.get('id')
            if kid and not any(x['id']==int(kid) for x in kinds):
                kinds.append({'id':int(kid),'name':item.get('kindName') or item.get('name') or str(kid)})
    return kinds


def library_rooms(client,day,kinds):
    if not now().date()<=day<=now().date()+timedelta(days=2):raise ValueError('图书馆只能查询今天至后天的预约情况')
    out=[]
    for kind in kinds:
        rows=client._call('GET','/reserve',params={'sysKind':1,'labId':'','resvDates':day.strftime('%Y%m%d'),
                   'page':1,'kindIds':kind,'pageSize':100}) or []
        for r in rows:
            slots=[]
            for s in r.get('resvInfo') or []:
                begin=datetime.fromtimestamp(s['startTime']/1000,CHINA_TZ)
                end=datetime.fromtimestamp(s['endTime']/1000,CHINA_TZ)
                if begin.date()==day:slots.append({'start':begin.isoformat(),'end':end.isoformat()})
            rule=r.get('resvRule') or {}
            out.append({'id':int(r['devId']),'name':str(r.get('devName','')).strip(),'kind_id':int(r.get('kindId') or kind),
                  'kind_name':r.get('kindName'),'location':r.get('labName'),'min_people':int(r.get('minUser') or 1),
                  'max_people':int(r.get('maxUser') or 1),'open':r.get('openTimes') or [],'busy':slots,
                  'rules':{k:rule.get(k) for k in ('minResvTime','maxResvTime','earliestResvTime','latestResvTime','cancelTime','timeInterval')},
                  'bookable':not r.get('onlyView') and r.get('devStatus',0)==0,
                  'services':r.get('addServices') or [],'attributes':r.get('deviceAttributes') or [],
                  'memo':r.get('resvMemo') or ''})
    return sorted(out,key=lambda r:(r['location'] or '',r['name']))


def eh_rooms(client):
    # Use limit/offset as in the official SPA. Legacy page/rows alone can silently truncate.
    result=[]
    for page in range(20):
        data=client._call_raw('GetMeetingRoomAllByCondition',{'limit':100,'offset':page*100,'page':page+1,'rows':100})['Data'] or {}
        rows=data.get('rows') or []
        result.extend(rows)
        if len(result)>=int(data.get('total',len(result))) or not rows:break
    return result


def eh_room_view(r):
    return {'id':r['MeetingRoomID'],'name':r.get('MeetingRoomName'), 'location':r.get('MeetingRoomLocal'),
      'kind_name':r.get('MeetingRoomType'),'max_people':r.get('CapacityNumber'), 'min_people':1,
      'bookable':bool(r.get('IsAvailable')),'needs_approval':bool(r.get('IsApproval')),
      'open':[{'openStartTime':str(r.get('CanBookStartTime') or '')[-8:-3],
               'openEndTime':str(r.get('CanBookEndTime') or '')[-8:-3]}],
      'rules':{'days_ahead':r.get('NumberOfDaysAhead')},'notice':r.get('MeetingRoomNotice') or '',
      'equipment':r.get('MeetingRoomEquipments') or [],'raw':r}


def eh_options(client,room):
    dtype=5 if '会议' in str(room.get('MeetingRoomType')) else 7 if '自习' in str(room.get('MeetingRoomType')) else 8 if '健身' in str(room.get('MeetingRoomType')) else 6
    def values(kind):
        rows=(client._call_raw('GetDicDetail',{'DicTypeID':kind})['Data'] or {}).get('rows') or []
        return [{'id':r['DicDetailID'],'name':r['DicDetailName']} for r in rows if r.get('IsValid')]
    return {'types':values(dtype),'styles':values(9)}


def catalog():
    output={'library':[],'ehall':[],'errors':[]}
    try:
        client=lib_client();kinds=library_kinds(client)
        summaries=_jsonable(client.home_summary())
        discussions=[s for s in summaries if '讨论间' in s['name']]
        output['library'].append({'id':'discussion','name':'讨论间','kind_ids':[1,9],
                  'idle':sum(s['idle_quantity'] for s in discussions),'total':sum(s['total_quantity'] for s in discussions)})
        for k in kinds:
            if k['id'] in {1,9}:continue
            summary=next((s for s in summaries if s['name']==k['name']),{})
            output['library'].append({'id':str(k['id']),'name':k['name'],'kind_ids':[k['id']],
                       'idle':summary.get('idle_quantity'),'total':summary.get('total_quantity')})
    except Exception as e:output['errors'].append('图书馆场地列表暂不可读取：'+type(e).__name__)
    try:output['ehall']=[eh_room_view(r) for r in eh_rooms(eh_client())]
    except Exception as e:output['errors'].append('E-Hall 场地列表暂不可读取：'+type(e).__name__)
    return output


def schedules(system,category,day,begin=None,end=None):
    if system=='library':
        client=lib_client()
        kinds=[1,9] if category=='discussion' else [int(category)]
        if category!='discussion' and int(category) not in {k['id'] for k in library_kinds(client)}:raise ValueError('场地类型不存在')
        rooms=library_rooms(client,day,kinds)
        for room in rooms:
            room['free']=is_free(room,begin,end) if begin and end else None
        return {'rooms':rooms,'date':day.isoformat()}
    if system!='ehall':raise ValueError('预约系统无效')
    client=eh_client()
    raw=next((r for r in eh_rooms(client) if r['MeetingRoomID']==category),None)
    if not raw:raise ValueError('场地不存在')
    room=eh_room_view(raw)
    records=client._call_raw('GetMeetingsByRoomAndDateAndUser',{'MeetingRoomID':category,'DateTime':day.isoformat()})['Data'] or {}
    room['busy']=[]
    for row in records.get('rows') or []:
        b=parse_dt(row.get('StartDateTime'));e=parse_dt(row.get('EndDateTime'))
        if row.get('MeetingRoomID') not in {None,category}:continue
        if b and e and b.date()<=day<=e.date():room['busy'].append({'start':b.isoformat(),'end':e.isoformat()})
    room['free']=None
    if begin and end:
        checked=client._call_raw('CheckMeetingTime',{'MeetingRoomID':category,'StartDateTime':begin.isoformat(),'EndDateTime':end.isoformat()})['Data']
        room['free']=bool(checked.get('CanBook'));room['reason']=checked.get('Reason')
    return {'rooms':[room],'date':day.isoformat(),'options':eh_options(client,raw),
            'note':'占用列表仅显示官方当前账户可见的记录；目标时段是否可预约由官方实时检查确认。'}


def library_mine(start,end,status=None):
    client=lib_client();me=client.whoami();result=[]
    for page in range(1,21):
        params={'beginDate':start.isoformat(),'endDate':end.isoformat(),'page':page,'pageNum':100,'orderKey':'gmt_create','orderModel':'desc'}
        if status is not None:params['needStatus']=int(status)
        rows=client._call('GET','/reserve/resvInfo',params=params) or []
        if isinstance(rows,dict):rows=rows.get('data') or rows.get('rows') or []
        for r in rows:
            devs=r.get('resvDevInfoList') or []
            owner=str(r.get('appAccNo'))==str(me.acc_no)
            flags=int(r.get('resvStatus') or 0)
            own_member=next((m for m in r.get('resvMemberInfoList') or [] if str(m.get('accNo'))==str(me.acc_no)),{})
            member_kind=int(own_member.get('kind') or 0)
            result.append({**r,'room_name':'、'.join(d.get('devName') or '' for d in devs),
               'begin':datetime.fromtimestamp(r['resvBeginTime']/1000,CHINA_TZ).isoformat(),
               'end':datetime.fromtimestamp(r['resvEndTime']/1000,CHINA_TZ).isoformat(),
               'can_cancel':owner and not flags&4 and not flags&128,
               'can_edit':owner and r.get('classKind')==1 and bool(flags&256),
               'can_end':owner and bool(r.get('endEarly')),
               'can_extend':owner and bool(flags&64) and not flags&128,
               'can_agree':not owner and bool(flags&8192) and not flags&128 and not member_kind&192 and r.get('classKind')==1})
        if len(rows)<100:break
    return result


def eh_mine(start,end):
    client=eh_client();result=[]
    for page in range(20):
        data=client._call_raw('GetMyMeetings',{'limit':100,'offset':page*100,'page':page+1,'rows':100})['Data'] or {}
        rows=data.get('rows') or []
        for r in rows:
            b=parse_dt(r.get('StartDateTime') or r.get('StartTime') or r.get('MeetingStart'))
            if b and start<=b.date()<=end:
                result.append({**r,'room_name':r.get('MeetingRoomName'),'begin':b.isoformat(),
                     'end':r.get('EndDateTime') or r.get('EndTime') or r.get('MeetingEnd'),
                     'title':r.get('Topical') or r.get('MeetingName'),'id':r.get('MeetingID') or r.get('ID')})
        if page*100+len(rows)>=int(data.get('total',0)) or not rows:break
    return result


def members(system,keyword):
    if not 2<=len(keyword)<=80:raise ValueError('请至少输入两个字符')
    if system=='library':
        rows=lib_client()._call('GET','/account/getMembers',params={'key':keyword,'page':1,'pageNum':10}) or []
        return [{'id':r['accNo'],'name':r.get('trueName'),'account':r.get('logonName'),
                 'disabled':r.get('status')==2 or r.get('localstatus')==2} for r in rows]
    if system=='ehall':
        data=eh_client()._call_raw('GetUserInfoSampleInner',{'search':keyword,'limit':20,'offset':0})['Data'] or {}
        return [{'id':r.get('WID'),'name':r.get('XM'),'account':r.get('ZGH')} for r in data.get('rows') or []]
    raise ValueError('预约系统无效')


def validate_library(payload,room,client):
    begin,end=interval(payload)
    if begin<=now():raise ValueError('预约开始时间须在未来')
    if not room['bookable'] or not is_free(room,begin,end):raise ValueError('所选完整时段已不空闲或场地不可预约')
    rule=room['rules'];mins=(end-begin).total_seconds()/60
    if rule.get('minResvTime') and mins<rule['minResvTime']:raise ValueError('预约时长少于该场地最低时长')
    if rule.get('maxResvTime') and mins>rule['maxResvTime']:raise ValueError('预约时长超过该场地允许时长')
    if rule.get('earliestResvTime') and (begin-now()).total_seconds()/60>rule['earliestResvTime']:raise ValueError('尚未进入该场地预约窗口')
    if rule.get('latestResvTime') and (begin-now()).total_seconds()/60<rule['latestResvTime']:raise ValueError('已超过该场地最晚预约时间')
    title=str(payload.get('title') or '').strip()
    if not title or len(title)>80:raise ValueError('请填写 1–80 字的预约主题')
    values=payload.get('members') or []
    if not isinstance(values,list) or any(not str(x).isdigit() for x in values):raise ValueError('共同申请人编号无效')
    me=client.whoami();memberids=[me.acc_no]+[int(m) for m in values if int(m)!=me.acc_no]
    if len(memberids)!=len(set(memberids)) or not room['min_people']<=len(memberids)<=room['max_people']:
        raise ValueError(f'该场地需 {room["min_people"]}–{room["max_people"]} 位不同申请人（包含本人）')
    return begin,end,title,memberids


def book(payload,files=None):
    system=payload.get('system');begin,end=interval(payload)
    if system=='library':
        client=lib_client();category=payload.get('category','discussion')
        rooms=schedules('library',category,begin.date())['rooms']
        room=next((r for r in rooms if r['id']==int(payload['room_id'])),None)
        if not room:raise ValueError('场地不存在')
        mine=library_mine(begin.date(),begin.date())
        existing=next((r for r in mine if any(int(d.get('devId',0))==room['id'] for d in r.get('resvDevInfoList') or []) and parse_dt(r['begin'])==begin and parse_dt(r['end'])==end and not int(r.get('resvStatus',0))&128),None)
        if existing:return {'state':'confirmed','reservation':existing,'message':'本人已有该场地和时段的预约，已核对，未重复预约'}
        begin,end,title,memberids=validate_library(payload,room,client)
        mark_sent()
        result=client.add_reservation(dev_id=room['id'],begin=begin,end=end,title=title,
                  member_kind=2 if len(memberids)>1 else 1,resv_member=memberids,
                  memo=str(payload.get('memo') or '')[:1000],dry_run=False,enforce_policy=False)
        mine=library_mine(begin.date(),begin.date())
        match=next((r for r in mine if any(int(d.get('devId',0))==room['id'] for d in r.get('resvDevInfoList') or []) and r['begin']==begin.isoformat() and r['end']==end.isoformat()),None)
    elif system=='ehall':
        client=eh_client();rid=str(payload['room_id'])
        room=next((r for r in eh_rooms(client) if r['MeetingRoomID']==rid),None)
        if not room or not room.get('IsAvailable'):raise ValueError('场地不可预约')
        mine=eh_mine(begin.date(),begin.date())
        existing=next((r for r in mine if r.get('MeetingRoomID')==rid and parse_dt(r['begin'])==begin and parse_dt(r['end'])==end and not r.get('IsCancel')),None)
        if existing:return {'state':'confirmed','reservation':existing,'message':'本人已有该场地和时段的预约，已核对，未重复预约'}
        if begin<=now():raise ValueError('预约开始时间须在未来')
        checked=client._call_raw('CheckMeetingTime',{'MeetingRoomID':rid,'StartDateTime':begin.isoformat(),'EndDateTime':end.isoformat()})['Data']
        if not checked.get('CanBook'):raise ValueError(checked.get('Reason') or '时段不可预约')
        title=str(payload.get('title') or '').strip();description=str(payload.get('memo') or '').strip()
        if not title or len(title)>80 or not description:raise ValueError('请填写预约主题和活动内容')
        me=client.whoami();uid=me.get('WID') or me.get('UserID') or me.get('id')
        if not uid:raise ValueError('当前账户信息缺少预约所需的用户编号')
        people=list(dict.fromkeys([str(uid)]+[str(v) for v in payload.get('members') or []]))
        options=eh_options(client,room)
        typ=int(payload.get('meeting_type') or 0);style=int(payload.get('meeting_style') or 0)
        if typ not in {x['id'] for x in options['types']}:raise ValueError('请选择官方提供的活动类型')
        if options['styles'] and style not in {x['id'] for x in options['styles']}:raise ValueError('请选择官方提供的活动方式')
        if len(files or [])>10:raise ValueError('每次最多添加 10 个申请资料附件')
        documents=[]
        for f in files or []:
            if not f.filename or re.search(r'[\\/:?*"<>|]',f.filename):raise ValueError('申请资料文件名无效')
        for f in files or []:
            f.stream.seek(0)
            mark_sent()
            response=client.s.post(client.API_BASE+'/UploadMeetingDocument',
                data={'MessageType':5005,'MessageID':str(uuid.uuid4())},files={'Files':(f.filename,f.stream,f.mimetype or 'application/octet-stream')},timeout=(15,180))
            uploaded=response.json()
            if not uploaded.get('IsSuccess'):return {'state':'rejected','message':'官方未接受申请资料上传，尚未创建预约'}
            doc=uploaded.get('Data') or {}
            if not doc.get('FilePath'):raise RuntimeError('申请附件上传回执不完整')
            documents.append({'FileName':doc.get('FileName') or f.filename,'FilePath':doc['FilePath']})
        model={'MeetingID':str(uuid.uuid4()),'MeetingRoomID':rid,'Topical':title,'MeetingContext':description,
               'MeetingData':description,'StartDateTime':begin.isoformat(),'EndDateTime':end.isoformat(),
               'CreateUserID':uid,'CreateUserDept':me.get('DeptID') or me.get('DWDm') or me.get('SZDW') or me.get('groups'),
               'MeetingRoomName':room.get('MeetingRoomName'),'MeetingRoomLocal':room.get('MeetingRoomLocal'),
               'CapacityNumber':room.get('CapacityNumber'),'DeptID':room.get('DeptID'),
               'DegreeOfUrgency':1,'MeetingType':typ,'MeetingStyle':style,
               'NotifyType':666,'State':1,'MeetingRoomModel':room,
               'MeetingParticipantModels':[{'UserID':p} for p in people],
               'MeetingParticipants':[{'MeetingParticipantID':str(uuid.uuid4()),'MeetingID':'','UserID':p} for p in people],
               'MeetingHosterModels':[],'MeetingPresenterModels':[],'MeetingDocumentModels':documents,
               'BookReason':description,'meetingroomtype':room.get('MeetingRoomType'),
               'IsEmailNotify':False,'IsSMSNotify':False,'CreateUserName':me.get('name')}
        for p in model['MeetingParticipants']:p['MeetingID']=model['MeetingID']
        mark_sent()
        result=client._call_raw('AddMeeting',{'Model':model})['Data']
        mine=eh_mine(begin.date(),begin.date())
        match=next((r for r in mine if r.get('MeetingRoomID')==rid and parse_dt(r['begin'])==begin and parse_dt(r['end'])==end),None)
    else:raise ValueError('预约系统无效')
    return {'state':'confirmed' if match else 'needs_review','reservation':match,
            'message':'官方预约记录已回读确认（需审批的场地仍以审批状态为准）' if match else '已发送预约，但未回读到对应记录，请核对我的预约，暂不重复提交'}


def reservation_detail(system,reservation_id):
    start=now().date()-timedelta(days=365);end=now().date()+timedelta(days=365)
    rows=library_mine(start,end) if system=='library' else eh_mine(start,end) if system=='ehall' else []
    record=next((r for r in rows if str(r.get('resvId') or r.get('id'))==str(reservation_id)),None)
    if not record:raise ValueError('该预约不在本人预约记录中')
    if system=='library':return record
    data=eh_client()._call_raw('GetMeetingByID',{'ID':reservation_id})['Data'] or {}
    model=data.get('Model') or (data if data.get('MeetingID') else None)
    if not model:raise ValueError('官方预约详情暂不可读取')
    return {**record,**model}


def reservation_action(payload):
    system=payload['system'];action=payload['action']
    start=now().date()-timedelta(days=30);end=now().date()+timedelta(days=365)
    if system=='library':
        client=lib_client();rows=library_mine(start,end)
        r=next((r for r in rows if int(r['resvId'])==int(payload['reservation_id'])),None)
        if not r:raise ValueError('该预约不在本人预约记录中')
        if action=='cancel':
            if not r['can_cancel']:raise ValueError('当前预约不能取消')
            dev=r['resvDevInfoList'][0];kind=dev.get('kindId')
            if kind:
                room=next((x for x in library_rooms(client,parse_dt(r['begin']).date(),[int(kind)]) if x['id']==dev['devId']),None)
                minutes=(room or {}).get('rules',{}).get('cancelTime')
                if minutes is not None and parse_dt(r['begin'])-now()<timedelta(minutes=int(minutes)):raise ValueError(f'该场地须至少提前 {minutes} 分钟取消')
            mark_sent()
            client.cancel_reservation(uuid=r['uuid'],dry_run=False,enforce_policy=False)
        elif action=='end':
            if not r['can_end']:raise ValueError('当前预约不能提前结束')
            mark_sent()
            client._call('POST','/reserve/endAhaed',json_body={'uuid':r['uuid']})
        elif action=='extend':
            if not r['can_extend']:raise ValueError('当前预约不能续时')
            duration=int(payload.get('duration') or 0)
            allowed=extension_options(r['resvId'])
            if duration not in allowed:raise ValueError('所选续时长度不在官方允许范围内')
            mark_sent()
            client._call('POST','/reserve/time/expand',json_body={'resvId':r['resvId'],'duration':duration})
        elif action in {'agree','reject'}:
            if not r['can_agree']:raise ValueError('该预约当前不需要本人同意')
            mark_sent()
            client._call('POST','/resvMember/operate',json_body={'resvId':r['resvId'],'resvMemberKind':64 if action=='agree' else 128})
        elif action=='edit':
            if not r['can_edit']:raise ValueError('官方状态不允许修改此预约')
            begin,finish=interval(payload);roomid=r['resvDevInfoList'][0]['devId']
            kindid=r['resvDevInfoList'][0].get('kindId') or payload.get('kind_id')
            if not kindid:raise ValueError('当前预约缺少场地类型，请在原页面修改')
            room=next(x for x in library_rooms(client,begin.date(),[int(kindid)]) if x['id']==roomid)
            room['busy']=[s for s in room['busy'] if s['start']!=r['begin'] or s['end']!=r['end']]
            _,_,title,ids=validate_library(payload,room,client)
            mark_sent()
            client._call('POST','/reserve/update',json_body={'resvId':r['resvId'],'resvMember':ids,
                        'resvBeginTime':begin.strftime('%Y-%m-%d %H:%M:00'),'resvEndTime':finish.strftime('%Y-%m-%d %H:%M:00'),
                        'testName':title,'memo':str(payload.get('memo') or ''),'addServices':r.get('addServices') or []})
        else:raise ValueError('操作不受支持')
        fresh=library_mine(start,end)
        record=next((x for x in fresh if x['resvId']==r['resvId']),None)
        verified=(record is None or bool(int(record.get('resvStatus',0))&128)) if action=='cancel' else record is not None
        if action=='edit':verified=record is not None and parse_dt(record['begin'])==begin and parse_dt(record['end'])==finish
        if action=='extend':verified=record is not None and parse_dt(record['end'])>parse_dt(r['end'])
        if action=='end':verified=record is not None and (bool(int(record.get('resvStatus',0))&8) or parse_dt(record['end'])<=now())
        if action in {'agree','reject'}:
            me=client.whoami()
            own=next((m for m in (record or {}).get('resvMemberInfoList') or [] if str(m.get('accNo'))==str(me.acc_no)),{})
            verified=bool(int(own.get('kind') or 0)&(64 if action=='agree' else 128))
            if action=='reject' and record is None:verified=True
        return {'state':'confirmed' if verified else 'needs_review','message':'预约操作已回读核对' if verified else '操作已发送，变化尚未核对，请重新查询官方记录','reservation':record}
    if system=='ehall':
        client=eh_client();rows=eh_mine(start,end);r=next((x for x in rows if str(x['id'])==str(payload['reservation_id'])),None)
        if not r:raise ValueError('该预约不在本人预约记录中')
        if action=='cancel':
            reason=str(payload.get('reason') or '').strip()
            if not reason:raise ValueError('请填写取消原因')
            model={**r,'MeetingID':r['id'],'CancelReason':reason,'CancelDateTime':now().isoformat(),'cancelUser':client.whoami().get('name')}
            mark_sent()
            client._call_raw('CancelMeeting',{'Model':model})
        elif action=='edit':
            details=client._call_raw('GetMeetingByID',{'ID':r['id']})['Data'] or {}
            model=details.get('Model') or (details if details.get('MeetingID') else None)
            if not model:raise ValueError('官方预约详情暂不可读取')
            begin,finish=interval(payload)
            uid=client.whoami().get('id') or client.whoami().get('WID')
            if not uid:raise ValueError('当前账户缺少预约所需的用户编号')
            people=list(dict.fromkeys([str(uid)]+[str(v) for v in payload.get('members') or []]))
            model.update(Topical=str(payload.get('title') or model.get('Topical')),MeetingContext=str(payload.get('memo') or model.get('MeetingContext')),
                         StartDateTime=begin.isoformat(),EndDateTime=finish.isoformat(),
                         MeetingParticipantModels=[{'UserID':p} for p in people],
                         MeetingType=int(payload.get('meeting_type') or model.get('MeetingType')),
                         MeetingStyle=int(payload.get('meeting_style') or model.get('MeetingStyle')))
            mark_sent()
            client._call_raw('UpdateMeeting',{'Model':model})
        else:raise ValueError('操作不受支持')
        fresh=eh_mine(start,end)
        record=next((x for x in fresh if x['id']==r['id']),None)
        verified=(record is None or bool(record.get('IsCancel') or record.get('CancelDateTime'))) if action=='cancel' else record is not None and parse_dt(record['begin'])==begin and parse_dt(record['end'])==finish
        return {'state':'confirmed' if verified else 'needs_review','message':'预约操作已回读核对' if verified else '操作已发送，变化尚未核对，请重新查询官方记录','reservation':record}
    raise ValueError('预约系统无效')


def extension_options(reservation_id):
    client=lib_client()
    rows=library_mine(now().date()-timedelta(days=30),now().date()+timedelta(days=365))
    if not any(str(r['resvId'])==str(reservation_id) and r['can_extend'] for r in rows):raise ValueError('该预约当前不可续时')
    values=client._call('GET','/reserve/time/expand/duration',params={'resvId':int(reservation_id)}) or []
    return [int(v) for v in values if str(v).isdigit()]
