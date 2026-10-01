"""Free classrooms share the venue UI; timetable freedom is not booking confirmation."""
from datetime import date
import threading
import time

from .core import DATA_ROOT, load_json, save_json
from .study import semester, now
from .academic_calendar import daily_view, load_recent_calendar

lock=threading.RLock()
_clients={}


def room_inventory(c,sem):
    path=DATA_ROOT/'classroom-cache'/f'rooms_{sem.xn}_{sem.xq}.json'
    stored=load_json(path,{})
    if time.time()-stored.get('at',0)<1800 and isinstance(stored.get('rooms'),list):return stored['rooms']
    rows=[{'name':r.name,'capacity':r.capacity} for r in c.rooms()]
    save_json(path,{'at':time.time(),'rooms':rows})
    return rows


def client(sem):
    from sustech_survival.tis.classroom.classroom import ClassroomOccupancy
    from sustech_survival.tis.classroom.live import LiveOccupancyClient
    key=(sem.xn,sem.xq)
    if key not in _clients or time.time()-_clients[key][0]>1800:
        live=LiveOccupancyClient(max_age=120,cache_dir=DATA_ROOT/'classroom-live')
        _clients[key]=(time.time(),ClassroomOccupancy(semester=sem,max_age=1800,
            cache_dir=DATA_ROOT/'classroom-cache',live_client=live))
    return _clients[key][1]


def calendar_day(sem,target):
    y=int(sem.xn[:4])+(sem.xq!='1')
    cal=load_recent_calendar(y)
    if target.year!=y:
        other=load_recent_calendar(target.year)
        cal.holidays.extend(other.holidays)
    term=next((t for t in (cal.fall,cal.spring,cal.summer) if t and t.xn==sem.xn and t.xq==sem.xq),None)
    if not term or not term.teaching_start<=target<=term.final_end:raise ValueError('查询日期不在所选学期内')
    meta=daily_view(term,[],target)['calendar']
    source=date.fromisoformat(meta['source_date']) if meta.get('source_date') else target
    return meta,term.week_of(source),source.isoweekday(),term.week_of(target)


def query(args):
    sem=semester(args.get('xn'),args.get('xq'))
    target=date.fromisoformat(args.get('date') or now()[:10])
    start,end=int(args.get('start',1)),int(args.get('end',2))
    minimum=int(args.get('capacity',0));page=max(1,int(args.get('page',1)))
    if not 1<=start<=end<=12 or not 0<=minimum<=10000:raise ValueError('节次或容量条件无效')
    meta,week,weekday,actual_week=calendar_day(sem,target)
    c=client(sem);keyword=args.get('q','').casefold()
    rooms=[r for r in room_inventory(c,sem) if keyword in r['name'].casefold()]
    course_off=meta['kind'] in {'holiday','break','final','vacation'}
    results=[]
    for r in rooms:
        if minimum and (r['capacity'] is None or r['capacity']<minimum):continue
        busy=[] if course_off else [s for s in c.occupancy(r['name'],week,weekday) if s.overlaps(start,end)]
        if args.get('free','1')=='1' and busy:continue
        results.append({'name':r['name'],'capacity':r['capacity'],'course_free':not busy,
                        'status':'课表空闲 · 借用待核验' if not busy else '有课程占用',
                        'busy':[{'title':s.course_name,'start':s.period_start,'end':s.period_end} for s in busy]})
    from sustech_survival.tis.classroom.booking import VenueBorrowClient
    try:permitted=VenueBorrowClient().check_permission(sem).allowed
    except Exception:permitted=None
    return {'rooms':results[(page-1)*24:page*24],'total':len(results),'page':page,'calendar':meta,
        'xn':sem.xn,'xq':sem.xq,'date':target.isoformat(),'start':start,'end':end,'permission':permitted,
        'updated_at':now(),'note':'结果按教学课表和校历筛选。点击教室再核对临时借用；没有课不代表已获使用权或现场开门。'}


def detail(args):
    sem=semester(args.get('xn'),args.get('xq'));target=date.fromisoformat(args['date'])
    c=client(sem);name=args.get('name','')
    if not any(r['name']==name for r in room_inventory(c,sem)):raise ValueError('教室名称无效')
    meta,week,weekday,actual_week=calendar_day(sem,target)
    # Do not inherit upstream's [] fallback when the room-code catalog fails.
    code=c._room_code_for_name(name)
    if not code:return {'verified':False,'message':'教务系统未返回该教室的场地编码，临时借用情况无法确认；课表空闲不能直接作为预约依据。','entries':[]}
    entries=c._live_client.query_room(code,xn=sem.xn,xq=sem.xq,use_cache=False)
    rows=[]
    for e in entries:
        if e.is_borrowing:
            active=e.active_on(actual_week,target.isoweekday())
        elif e.is_course:
            active=meta['kind'] not in {'holiday','break','final','vacation'} and e.active_on(week,weekday)
        else:
            active=e.active_on(actual_week,target.isoweekday())
        if active:rows.append({'type':e.type,'title':e.course_name or e.purpose or '临时借用','period':e.period_start})
    return {'verified':True,'entries':rows,'message':'已核对官方场地课表；正式使用仍需遵循学校借用审批。','updated_at':now()}
