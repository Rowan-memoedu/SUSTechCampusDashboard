"""Calendar export with the same SUSTech period times and holiday engine as the dashboard."""
import hashlib
from datetime import date, datetime, timedelta, timezone
from .core import CHINA_TZ
from .study import semester, enrollment, member, client
from .academic_calendar import load_recent_calendar

STARTS={1:(8,0),2:(9,0),3:(10,20),4:(11,20),5:(14,0),6:(15,0),7:(16,20),8:(17,20),9:(19,0),10:(20,0)}


def escape(value):
    return str(value).replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')


def serialize(term,courses,through=None):
    from sustech_survival.calendar import ClassTime
    label='南科大课表'+(f'（截至 {through}）' if through else '')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//SUSTech Campus Dashboard//Courses//ZH','CALSCALE:GREGORIAN','X-WR-CALNAME:'+label]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    seen=set()
    for c in courses:
        if not c['slots_raw']:raise ValueError('所选课程缺少排课信息，不能完整导出')
        for s in c['slots_raw']:
            ct=ClassTime(weeks=tuple(s['weeks']),weekday=s['day']-1,periods=tuple(range(s['period_start'],s['period_end']+1)),title=c['name'],room=s.get('room',''),teacher='、'.join(c['teachers']))
            for day in term.dates(ct):
                if through and day>through:continue
                for p in ct.periods:
                    if p not in STARTS:raise ValueError('课程包含尚未核实起止时间的节次，暂不能导出')
                    ident=f'{c["rwh"]}:{day}:{p}'
                    if ident in seen:continue
                    seen.add(ident)
                    start=datetime(day.year,day.month,day.day,*STARTS[p],tzinfo=CHINA_TZ)
                    end=start+timedelta(minutes=50)
                    fmt=lambda t:t.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    lines+=['BEGIN:VEVENT',f'UID:{hashlib.sha256(ident.encode()).hexdigest()[:32]}@sustech-campus',f'DTSTAMP:{stamp}',f'DTSTART:{fmt(start)}',f'DTEND:{fmt(end)}',f'SUMMARY:{escape(c["name"])}',f'LOCATION:{escape(ct.room)}',f'DESCRIPTION:{escape(c["code"]+" · "+ct.teacher+" · 第 "+str(p)+" 节")}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    folded=[]
    for line in lines:
        part=''
        for char in line:
            if len((part+char).encode())>75:folded.append(part);part=' '
            part+=char
        folded.append(part)
    return '\r\n'.join(folded)+'\r\n'


def export(data):
    sem=semester(data.get('xn'),data.get('xq'))
    y=int(sem.xn[:4])+(sem.xq!='1')
    cal=load_recent_calendar(y)
    term=next((t for t in (cal.fall,cal.spring,cal.summer) if t and t.xn==sem.xn and t.xq==sem.xq),None)
    if not term:raise ValueError('校历不包含所选学期')
    through=None
    if term.final_end.year!=y:
        from sustech_survival.calendar import CalendarError
        try:
            other=load_recent_calendar(term.final_end.year);cal.holidays.extend(other.holidays)
        except CalendarError:
            # Never infer unpublished next-year holidays. Label and limit the exported range.
            through=date(y,12,31)
    ids=data.get('rwhs')
    if ids:
        if not isinstance(ids,list) or len(ids)>30:raise ValueError('导出课程数量无效')
        from .study import course_dict
        courses=[course_dict(c) for c in client(sem).list_courses() if c.rwh in ids]
        if len(courses)!=len(set(ids)):raise ValueError('部分课程不在当前目录中')
    else:courses=[member(r) for r in enrollment(sem)['yxkcList']]
    if not courses:raise ValueError('没有可导出的课程')
    return {'text':serialize(term,courses,through),'through':through.isoformat() if through else None}
