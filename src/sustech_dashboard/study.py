"""TIS grades, credits and course planning. Account writes use reviewed payloads."""
from __future__ import annotations

import csv
import io
import math
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from .core import CHINA_TZ, DATA_ROOT
from .provider import COURSE_CUTOFF  # Retained for callers of the older API.
from .actions import mark_sent

lock = threading.RLock()
_clients = {}
_cache = {}
HANDBOOK = 'https://welcome.sustech.edu.cn/uploads/file/ufHB2uuBKsigIj84jzCt.pdf'
POINTS = dict(zip('A+ A A- B+ B B- C+ C C- D+ D D- F'.split(),
                  [4,3.94,3.85,3.73,3.55,3.32,3.09,2.78,2.42,2.08,1.63,1.15,0]))


def now():
    return datetime.now(CHINA_TZ).isoformat()


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def auth():
    from sustech_survival.sso import TISAuth
    a = TISAuth()
    ok, _ = a.ensure()
    if not ok:
        raise ValueError('教务系统认证失败，请重新连接校园账号')
    return a


def post(path, data=None, **kwargs):
    r = auth().post(path, data=data, timeout=(10, 45), **kwargs)
    r.raise_for_status()
    if not r.content:
        raise ValueError('教务系统未返回数据，本次查询不能确认结果')
    d = r.json()
    if isinstance(d, dict) and 'code' in d and str(d['code']) not in {'0','200'}:
        raise ValueError('教务系统拒绝查询，请稍后重试或检查账户权限')
    return d


def semester(xn=None, xq=None):
    from sustech_survival.semester import Semester
    current = Semester.current()
    xn, xq = str(xn or current.xn), str(xq or current.xq)
    if not re.fullmatch(r'20\d{2}-20\d{2}', xn) or xq not in {'1','2','3'}:
        raise ValueError('学期格式无效')
    y = int(xn[:4])
    start = date(y,9,1) if xq == '1' else date(y+1,2 if xq == '2' else 7,1)
    if (xn, xq) != (str(current.xn), str(current.xq)):
        raise ValueError('只支持当前学期')
    return Semester(xn, xq)


def client(sem):
    from sustech_survival.selectcourse.selectcourse import SelectCourseClient
    key = (sem.xn, sem.xq)
    old = _clients.get(key)
    if old is None or time.time()-old[0] > 600:
        old = (time.time(), SelectCourseClient(semester=sem, max_age=600, cache_dir=DATA_ROOT/'selection-cache'))
        _clients[key] = old
    return old[1]


def course_dict(c):
    return {k:getattr(c,k) for k in ('code','name','name_en','section_name','class_group','rwh',
        'college','category','nature','campus','credits','total_hours','capacity','enrolled',
        'rooms','teachers','slots_raw','task_type','language','grading','conflicts','requirement','note')}


def member(raw):
    from sustech_survival.selectcourse.course import Course
    c = course_dict(Course.from_api(raw))
    c.update(bid=number(raw.get('xkxs')), round_code=raw.get('xkfsdm',''))
    return c


def round_dict(r):
    fields = ('xkfsdm','xkfsmc','lcmc','ksrq','jsrq','xkms','jfxs','sfkf','sfkx','sfkt','xkgwcsfkf')
    out = {k:r.get(k) for k in fields}
    out['open'] = round_open(r)
    return out


def round_open(r):
    try:
        start = datetime.fromisoformat(r['ksrq']).replace(tzinfo=CHINA_TZ)
        end = datetime.fromisoformat(r['jsrq']).replace(tzinfo=CHINA_TZ)
        return str(r.get('sfkf','1')) == '1' and start <= datetime.now(CHINA_TZ) <= end
    except (TypeError, ValueError, KeyError):
        return False


def enrollment(sem, refresh=False):
    key = ('enrolled',sem.xn,sem.xq)
    if not refresh and key in _cache and time.time()-_cache[key][0] < 30:
        return _cache[key][1]
    d = post('/Xsxk/queryYxkc', {'p_xn':sem.xn,'p_xq':sem.xq})
    if not isinstance(d,dict) or not isinstance(d.get('yxkcList'),list) or not isinstance(d.get('xkgwcList'),list):
        raise ValueError('教务系统未返回完整的已选课程和购物车')
    _cache[key] = (time.time(),d)
    return d


def selection_info(xn=None,xq=None):
    sem = semester(xn,xq)
    d = enrollment(sem)
    courses = [member(r) for r in d['yxkcList']]
    return {'xn':sem.xn,'xq':sem.xq,'enrolled':courses,'cart':[member(r) for r in d['xkgwcList']],
        'rounds':[round_dict(r) for r in d.get('xkgzszList',[])],
        'credits':round(sum(c['credits'] for c in courses),2),'updated_at':now()}


def search_courses(args):
    sem = semester(args.get('xn'),args.get('xq'))
    c = client(sem)
    page = max(1,int(args.get('page',1)))
    mode = args.get('mode','campus')
    if mode == 'personal':
        # Separate clients: personal results must not poison the full catalog cache.
        from sustech_survival.selectcourse.selectcourse import SelectCourseClient
        c = SelectCourseClient(semester=sem, cache_dir=DATA_ROOT/'selection-cache')
        res = c.search_personal(keyword=args.get('q',''), teacher=args.get('teacher',''),
            category=args.get('category'),language=args.get('language'),round_code=args.get('round_code'),page=page,page_size=30)
        return {'courses':[course_dict(x) for x in res['courses']], 'total':res['total'],'page':page,
                'available':res['ok'],'message':res.get('message',''),'round':round_dict(res.get('current_type') or {}),'updated_at':now()}
    rows = c.search_campus(keyword=args.get('q',''),teacher=args.get('teacher',''),
        category=args.get('category'),language=args.get('language'))
    return {'courses':[course_dict(x) for x in rows[(page-1)*30:page*30]],
        'total':len(rows),'page':page,'available':True,'updated_at':now()}


def overlap(a,b):
    return (a.get('day') == b.get('day') and max(a['period_start'],b['period_start']) <= min(a['period_end'],b['period_end'])
            and bool(set(a.get('weeks',[])) & set(b.get('weeks',[]))))


def conflicts(a,b):
    return any(overlap(x,y) for x in a.get('slots_raw',[]) for y in b.get('slots_raw',[]))


def solve_groups(groups, fixed, limit=5):
    """Choose exactly one section for every requested course; never silently drop one."""
    solutions, visits = [], 0
    def walk(i, selected):
        nonlocal visits
        visits += 1
        if visits > 20000 or len(solutions) >= limit:return
        if i == len(groups):
            solutions.append(selected[:]);return
        for course in groups[i]:
            if not course['slots_raw']:continue
            if any(conflicts(course, other) for other in fixed+selected):continue
            walk(i+1, selected+[course])
    walk(0,[])
    return solutions, visits > 20000


def plan(data):
    sem = semester(data.get('xn'),data.get('xq'))
    codes = list(dict.fromkeys(str(x).strip().upper() for x in data.get('codes',[]) if str(x).strip()))
    if not 1 <= len(codes) <= 8:raise ValueError('每次选择 1–8 门课程进行排课')
    catalog = [course_dict(x) for x in client(sem).list_courses()]
    groups = [[c for c in catalog if c['code'].upper()==code] for code in codes]
    if any(not g for g in groups):raise ValueError('有课程代码不在本学期目录中')
    fixed = [member(x) for x in enrollment(sem)['yxkcList']] if data.get('keep_enrolled',True) else []
    fixed = [c for c in fixed if c['code'].upper() not in codes]
    if any(not c['slots_raw'] for c in fixed):raise ValueError('已有课程缺少时间信息，不能保证方案无冲突；可取消保留已选课，仅生成备选方案')
    solutions, truncated = solve_groups(groups,fixed)
    return {'solutions':solutions,'truncated':truncated,'fixed':fixed,
        'message':'按周次、星期和节次检测时间冲突；名额、先修要求和选课资格仍以提交时教务系统校验为准。',
        'unscheduled':[g[0]['code'] for g in groups if all(not c['slots_raw'] for c in g)]}


def prepare_selection(data):
    sem = semester(data.get('xn'),data.get('xq'))
    action, rwh = data.get('action'),str(data.get('rwh',''))
    methods = {'add':'add_course','drop':'drop_course','cart_add':'add_to_cart','cart_remove':'remove_from_cart','bid':'update_bid'}
    if action not in methods:raise ValueError('不支持的选课操作')
    e = enrollment(sem,refresh=True)
    code = str(data.get('round_code',''))
    cfg = next((r for r in e.get('xkgzszList',[]) if r.get('xkfsdm')==code),None)
    if not cfg or not round_open(cfg):raise ValueError('该选课轮次尚未开放或已经结束')
    if action=='drop' and str(cfg.get('sfkt','0'))!='1':raise ValueError('当前轮次不允许退课')
    if action in {'add','cart_add'} and str(cfg.get('sfkx','0'))!='1':raise ValueError('当前轮次不允许选课')
    if action in {'cart_add','cart_remove'} and str(cfg.get('xkgwcsfkf','0'))!='1':raise ValueError('当前轮次购物车未开放')
    from sustech_survival.selectcourse.selectcourse import SelectCourseClient
    c = SelectCourseClient(semester=sem, cache_dir=DATA_ROOT/'selection-cache')
    where = data.get('where','enrolled')
    if where not in {'enrolled','cart','catalog'} or action=='bid' and where=='catalog':raise ValueError('操作对象无效')
    source = e['xkgwcList'] if action=='cart_remove' or action in {'bid','add'} and where=='cart' else e['yxkcList']
    raw = next((r for r in source if r.get('rwh')==rwh),None)
    current = None
    if action in {'add','cart_add'}:
        # Requery the exact personal candidate instead of trusting a catalog id from the browser.
        search = c.search_personal(keyword=rwh.split('-')[3] if len(rwh.split('-'))>=5 else rwh,round_code=code,page_size=500)
        hit = next((x for x in search['courses'] if x.rwh==rwh),None)
        if not search['ok']:raise ValueError('当前轮次中该教学班不可选')
        if hit is None and action=='add' and where=='cart' and raw:
            if raw.get('xkfsdm') and raw['xkfsdm']!=code:raise ValueError('所选轮次与该课程所属轮次不一致')
            details,write_id=member(raw),raw.get('id')
        elif hit is not None:
            details, write_id = course_dict(hit),hit.id
        else:raise ValueError('当前轮次中该教学班不可选')
        current = search.get('current_type')
        if any(r.get('rwh')==rwh for r in e['yxkcList']):raise ValueError('该教学班已经选入')
        if action=='cart_add' and any(r.get('rwh')==rwh for r in e['xkgwcList']):raise ValueError('该教学班已经在购物车中')
        if any(conflicts(details, member(r)) for r in e['yxkcList']):raise ValueError('与已选课程存在时间冲突')
    else:
        if not raw:raise ValueError('该教学班已不在对应列表中，请刷新')
        if raw.get('xkfsdm') and raw['xkfsdm']!=code:raise ValueError('所选轮次与该课程所属轮次不一致')
        details,write_id = member(raw),raw.get('id')
        if action=='bid':
            search=c.search_personal(round_code=code,page_size=1)
            current=search.get('current_type')
    if not write_id:raise ValueError('教务系统未给出该课程的操作标识')
    kw={'dry_run':True,'id_field':write_id}
    if action in {'add','cart_add','bid'}:
        bid=int(data.get('bid',1))
        if not 1<=bid<=100000:raise ValueError('积分必须为正整数')
        kw['bid']=bid
        if current and number(current.get('jfxs')) is not None:
            baseline=number(raw.get('xkxs')) or 0 if raw else 0
            if max(0,bid-baseline)>number(current['jfxs']):raise ValueError('新增积分超过本轮剩余积分')
    if action in {'add','cart_add'}:kw['xkfsdm']=code
    if action=='bid':kw['where']=where
    prepared=getattr(c,methods[action])(rwh,**kw)
    if not prepared['would_post'].get('p_dqxn') or not prepared['would_post'].get('p_dqxq'):
        raise ValueError('教务系统当前选课上下文未能取得，请重试')
    return {'summary':{'action':action,'course':details,'round':round_dict(cfg),'bid':kw.get('bid'),'where':where},
        'request':dict(data),'wire':prepared,'sem':(sem.xn,sem.xq)}


def commit_selection(preview):
    # Fresh preflight, including round, membership and server-side write key.
    fresh=prepare_selection(preview['request'])
    if fresh['wire']['would_post'] != preview['wire']['would_post']:
        # Volatile query context may change; asking for a new review is safer than silently changing it.
        raise ValueError('选课状态已变化，请重新预览操作')
    a=auth()
    endpoint=fresh['wire']['endpoint']
    from urllib.parse import urlparse
    if endpoint.startswith('http') and urlparse(endpoint).netloc != 'tis.sustech.edu.cn':raise ValueError('选课地址无效')
    url=endpoint if endpoint.startswith('https://') else 'https://tis.sustech.edu.cn'+endpoint
    mark_sent()
    r=a.session.post(url,data=fresh['wire']['would_post'],timeout=(10,45),headers={'X-Requested-With':'XMLHttpRequest'})
    r.raise_for_status(); receipt=r.json()
    if str(receipt.get('jg')) != '1':return {'state':'rejected','message':str(receipt.get('message') or '教务系统拒绝该操作')[:300]}
    s=fresh['summary'];d=fresh['request'];e=enrollment(semester(*fresh['sem']),refresh=True)
    rows=e['xkgwcList'] if s['action'] in {'cart_add','cart_remove'} or s['action']=='bid' and s['where']=='cart' else e['yxkcList']
    found=next((x for x in rows if x.get('rwh')==d['rwh']),None)
    ok=(found is None) if s['action'] in {'drop','cart_remove'} else (found is not None)
    if s['action']=='bid':ok=found is not None and number(found.get('xkxs'))==s['bid']
    return {'state':'confirmed' if ok else 'needs_review','message':'已回读教务系统，操作成功' if ok else '官方已接收，但列表尚未匹配，请核对官方记录后再操作'}


def grade_term(raw):
    xn,xq=str(raw.get('xn','')),str(raw.get('xq',''))
    if re.fullmatch(r'20\d{2}-20\d{2}',xn) and xq in {'1','2','3'}:
        return date(int(xn[:4]),9,1) if xq=='1' else date(int(xn[:4])+1,2 if xq=='2' else 7,1)
    label=str(raw.get('xnxqmc',''))
    m=re.search(r'(20\d{2}).*?(秋|春|夏)',label)
    return date(int(m[1]),{'秋':9,'春':2,'夏':7}[m[2]],1) if m else None


def normalize_grade(r):
    grade=str(r.get('xscj') or r.get('djcj') or '').strip().upper()
    score=number(r.get('zzcj'))
    credit=number(r.get('xf'))
    name=r.get('kcmc') or r.get('kcmc_en') or '未命名课程'
    grading=str(r.get('jfzlbmc') or r.get('jfzmc') or r.get('cjfzmc') or r.get('jzfsmc') or '')
    flag=str(r.get('cjbzmc') or r.get('cjbs') or '')
    point=next((number(r[k]) for k in ('jd','cjjd','djcjjd') if number(r.get(k)) is not None),None)
    source='TIS 返回' if point is not None else ''
    excluded=grade in {'P','PASS','通过','合格'} or '二级' in grading or '两级' in grading or '毕业设计' in name or '毕业论文' in name
    if point is not None and not 0<=point<=4:point=None;source=''
    if not grade and score is None:point=None;source='未公布'
    elif excluded:point=None;source='不计 GPA'
    elif grade=='F' and not ('十三' in grading or '13' in grading):point=None;source='F 的计分制未明确，待核对'
    elif point is None and grade in POINTS and grade!='F':point=POINTS[grade];source='等级换算'
    elif point is None and grade=='F':
        if '十三' in grading or '13' in grading:point=0;source='等级换算'
        else:source='F 的计分制未明确，待核对'
    elif point is None and score is not None and 0<=score<=100 and '百分' in grading:
        point=float(Decimal(str(0 if score<60 else 4-3*(100-score)**2/1600)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP));source='百分制换算'
    passed=True if grade in POINTS and grade!='F' or grade in {'P','PASS','通过','合格'} else (False if grade in {'F','FAIL','不通过','不合格'} else (score>=60 if score is not None and '百分' in grading else None))
    return {'code':r.get('kcdm',''),'name':name,'semester':r.get('xnxqmc','未知学期'),'credits':credit,
        'grade':grade,'score':score,'point':point,'point_source':source,'passed':passed,'grading':grading,
        'nature':r.get('kcxzmc') or r.get('kcxz',''),'department':r.get('yxmc') or r.get('kkyxmc',''),
        'flag':flag,'special':bool(re.search('旷考|违纪|作弊',flag))}


def grade_summary(rows):
    # Count a repeated course once, choosing its best GPA attempt. Exceptional grades require official review.
    grouped={}
    for i,r in enumerate(rows):grouped.setdefault(r['code'] or f'row:{i}',[]).append(r)
    best=[max(g,key=lambda r:(r['point'] is not None,r['point'] or 0,r['passed'] is True)) for g in grouped.values()]
    earned=sum(max((r['credits'] or 0 for r in g if r['passed']),default=0) for g in grouped.values())
    counted=[r for r in best if r['point'] is not None and (r['credits'] or 0)>0]
    weight=sum(r['credits'] for r in counted)
    uncertain=any(r['special'] or (r['point'] is None and r['point_source']!='不计 GPA') for r in rows)
    gpa=round(sum(r['point']*r['credits'] for r in counted)/weight,3) if weight and not uncertain else None
    return {'courses':len(grouped),'earned_credits':round(earned,2),'gpa_credits':round(weight,2),
            'gpa':gpa,'incomplete':uncertain,'records':len(rows)}


def grades():
    all_rows=[]
    for page in range(1,101):
        d=post('/cjgl/grcjcx/grcjcx',json={'xn':None,'xq':None,'kcmc':None,'cxbj':'-1','pylx':'1','current':page,'pageSize':100})
        content=d.get('content')
        if not isinstance(content,dict) or not isinstance(content.get('list'),list):raise ValueError('成绩接口未返回有效列表')
        all_rows.extend(content['list'])
        if not content.get('hasNextPage') and len(all_rows)>=int(content.get('total',len(all_rows))):break
    else:raise ValueError('成绩分页超出范围，请到教务系统核对')
    from .academic_calendar import semester_scope
    current_term = semester_scope()['semester']
    y = int(current_term['XN'][:4])
    season = str(current_term['XQ'])
    term_date = date(y, 9, 1) if season == '1' else date(y+1, 2 if season == '2' else 7, 1)
    rows=[normalize_grade(r) for r in all_rows if grade_term(r) == term_date]
    terms=sorted({r['semester'] for r in rows},reverse=True)
    result={'rows':rows,'summary':grade_summary(rows),'semesters':[{**grade_summary([r for r in rows if r['semester']==s]),'name':s} for s in terms],
        'unclassified':sum(grade_term(r) is None for r in all_rows),'updated_at':now(),'source':HANDBOOK,
        'note':'仅统计当前学期可识别的成绩。参考 GPA 优先使用 TIS 绩点；通过制和毕业论文排除。计分制或特殊标识不明确时不计算 GPA，毕业要求及官方累计 GPA 以教务系统为准。'}
    try:
        info=selection_info();result['current']={'xn':info['xn'],'xq':info['xq'],'courses':len(info['enrolled']),'credits':info['credits']}
    except Exception:result['current']=None
    return result


def grade_csv(rows):
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['学期','课程代码','课程名称','学分','成绩等级','分数','绩点','绩点来源','课程性质','院系'])
    for r in rows:
        values=[r.get(k,'') for k in ('semester','code','name','credits','grade','score','point','point_source','nature','department')]
        writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for v in values])
    return '\ufeff'+out.getvalue()
