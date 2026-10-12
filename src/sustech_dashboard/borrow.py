"""TIS classroom application: exact current-term room/time preview and one-shot send."""
from datetime import date, datetime

from . import classrooms, study
from .actions import mark_sent
from .core import CHINA_TZ


def prepare(data):
    from sustech_survival.tis.classroom.booking import VenueBorrowClient
    from sustech_survival.tis.classroom.booking_schema import BorrowApplication, BorrowDetail, BorrowTimeSlot
    sem = study.semester()
    target = date.fromisoformat(data.get('date', ''))
    if target < datetime.now(CHINA_TZ).date():
        raise ValueError('不能申请过去的日期')
    start, end, count = int(data.get('start', 0)), int(data.get('end', 0)), int(data.get('headcount', 0))
    if not 1 <= start <= end <= 12 or not 1 <= count <= 10000:
        raise ValueError('节次或使用人数无效')
    name = str(data.get('name', ''))
    for key, limit in [('applicant', 100), ('phone', 40), ('purpose', 1000)]:
        if not isinstance(data.get(key), str) or not 1 <= len(data[key].strip()) <= limit:
            raise ValueError('请完整填写申请人、联系电话和用途')
    c = classrooms.client(sem)
    room = next((r for r in classrooms.room_inventory(c, sem) if r['name'] == name), None)
    if not room:
        raise ValueError('请先从当前学期的教室目录选择场地')
    if room.get('capacity') is not None and count > room['capacity']:
        raise ValueError('使用人数超过教室容量')
    code = c._room_code_for_name(name)
    if not code:
        raise ValueError('学校未返回场地编码，不能创建申请')
    borrow = VenueBorrowClient()
    if not borrow.check_permission(sem).allowed:
        raise ValueError('当前账号未获教室借用权限')
    meta, week, weekday, actual_week = classrooms.calendar_day(sem, target)
    if meta['kind'] not in {'holiday', 'break', 'final', 'vacation'} and any(s.overlaps(start, end) for s in c.occupancy(name, week, weekday)):
        raise ValueError('该教室在目标时段有课程占用')
    busy = borrow.query_venue_occupancy(semester=sem, room_codes=[code], weeks=[actual_week], weekday=target.isoweekday())
    if any(not s.period_start or not s.period_end or max(start, s.period_start) <= min(end, s.period_end) for s in busy):
        raise ValueError('官方场地占用记录与目标时段冲突或不能确认，暂不申请')
    auth = study.auth()
    slot = BorrowTimeSlot(weekday=target.isoweekday(), period_start=start, period_end=end)
    detail = BorrowDetail(seq=1, room_code=code, room_name=name, capacity=count, purpose=data['purpose'].strip(),
                          start_date=target.isoformat(), end_date=target.isoformat(),
                          week_bitmask=str(actual_week), week_range=f'{actual_week},{actual_week}', time_slots=[slot])
    form = BorrowApplication(semester=sem, applicant_name=data['applicant'].strip(), applicant_phone=data['phone'].strip(),
                             applicant_employee_id=auth.username, user_name=data['applicant'].strip(),
                             user_phone=data['phone'].strip(), user_employee_id=auth.username, headcount=count,
                             purpose=data['purpose'].strip(), audit_office='1', campus='1', details=[detail],
                             weeks=str(actual_week), start_end_weeks=f'{actual_week},{actual_week}')
    summary = {'教室': name, '日期': target.isoformat(), '节次': f'{start}–{end}', '人数': count,
               '申请人': form.applicant_name, '联系电话': form.applicant_phone, '用途': form.purpose,
               '说明': '提交借用审批申请；收到申请编号不代表审批通过。'}
    return {'request': dict(data), 'form': form, 'summary': summary,
            'target': f'borrow:{code}:{target}:{start}:{end}'}


def commit(prepared):
    from sustech_survival.tis.classroom.booking import VenueBorrowClient
    fresh = prepare(prepared['request'])
    if fresh['summary'] != prepared['summary'] or fresh['target'] != prepared['target']:
        raise ValueError('场地信息变化，请重新核对')
    mark_sent()
    response = VenueBorrowClient().create_borrow_application(fresh['form'], dry_run=False)
    # Upstream has no reliable application-detail readback. Never call creation approval.
    return {'state': 'needs_review', 'receipt_id': response.id, 'receipt_number': response.jhdh,
            'message': '申请已发送' + ('，学校返回申请编号' if response.id or response.jhdh else '，回执尚不完整') + '；请在教务系统核对申请与审批状态，暂不重复提交。',
            'official_url': 'https://tis.sustech.edu.cn/#/cdjy'}
