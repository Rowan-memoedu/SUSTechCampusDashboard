"""On-demand public campus services; no background crawling or school writes."""
import re
from urllib.parse import quote, urljoin, urlparse, unquote

from bs4 import BeautifulSoup

from .provider import _jsonable
from .study import now


def integer(args, key, default=1, maximum=1000):
    value = int(args.get(key, default))
    if not 0 <= value <= maximum:
        raise ValueError('查询范围无效')
    return value


def text_arg(args, key='q', limit=200):
    value = str(args.get(key, '')).strip()
    if len(value) > limit or '\x00' in value:
        raise ValueError('查询内容过长或包含无效字符')
    return value


def result(items=(), **kw):
    return dict(items=list(items), updated_at=now(), **kw)


def transit(args):
    from sustech_survival.transit.transit import TransitClient, LIVE_API
    from sustech_survival.transit.schema import LiveBus
    c = TransitClient()
    op = args.get('op', 'lines')
    day = args.get('day', 'workday')
    try:
        if op == 'lines':
            return result(_jsonable(c.list_bus_lines(day)))
        if op == 'schedule':
            return result([_jsonable(c.get_schedule(text_arg(args, 'line'), integer(args, 'route', 0, 30), day))])
        if op == 'stops':
            code = args.get('line', 'XYBS1')
            if code not in {'XYBS1', 'XYBS2'}:
                raise ValueError('线路无效')
            return result(_jsonable(c.get_bus_stops(code, integer(args, 'direction', 0, 1))))
        if op == 'live':
            # Upstream swallows both network failures as []; retain the distinction.
            buses, warnings, successful = [], [], 0
            for endpoint in ('monitor_osm', 'monitor_sev_osm'):
                try:
                    response = c.session.get(f'{LIVE_API}/api/v2/{endpoint}/', timeout=(10, 20))
                    response.raise_for_status()
                    buses.extend(_jsonable(LiveBus.from_api(b)) for b in response.json())
                    successful += 1
                except Exception:
                    warnings.append('校巴' if endpoint == 'monitor_osm' else '电瓶车')
            if not successful:
                raise RuntimeError('实时车辆源暂不可用')
            return result(buses, warnings=[w + '实时位置暂不可用' for w in warnings])
        if op == 'places':
            return result(_jsonable(c.find_facility(text_arg(args))) if text_arg(args) else _jsonable(c.list_facilities()))
        if op == 'route':
            mode = args.get('mode', 'transit')
            if mode not in {'walk', 'bus', 'transit'}:
                raise ValueError('路线方式无效')
            route = c.shortest_path(text_arg(args, 'from'), text_arg(args, 'to'), mode=mode)
            return result([_jsonable(route)], note='路线为站点间连接估算，步行连接不代表实际道路；班次和候车时间请另行核对。精确步行导航在上游已停用。')
        raise ValueError('查询类型无效')
    finally:
        c.session.close()


def nces(args):
    from sustech_survival.nces.scraper import NCESScraper
    c = NCESScraper()
    op = args.get('op', 'search')
    if op == 'detail':
        detail = c.course_detail(integer(args, 'id', maximum=100000000))
        if detail is None:
            raise ValueError('该社区课程不存在')
        return result([detail], note='社区个人评价，仅供选课参考；不是学校正式评教。')
    q, teacher = text_arg(args), text_arg(args, 'teacher')
    if teacher:
        rows = _jsonable(c.teacher_courses(teacher))
        if q:
            rows = [r for r in rows if q.casefold() in (str(r.get('code', '')) + ' ' + str(r.get('name', ''))).casefold()]
        return result(rows, total=len(rows))
    data = c.search_code(q) if q else c.browse(page=max(1, integer(args, 'page')), per_page=20)
    rows = data.get('items', [])
    return result(rows, total=data.get('total', len(rows)), page=data.get('page', 1),
                  note='社区个人评价；搜索展示上游返回的候选，评分和评论数量以对应课程详情为准。')


def faculty(args):
    from sustech_survival.faculty.faculty import FacultyClient
    c = FacultyClient()
    try:
        if args.get('op') == 'detail':
            slug = text_arg(args, 'id')
            if not re.fullmatch(r'[\w-]{1,160}', slug):
                raise ValueError('教师标识无效')
            profile = c.get(slug)
            return result([dict(_jsonable(profile), profile_text=profile.to_markdown(), profile_url=profile.profile_url_resolved)])
        depts = c.departments
        dept = text_arg(args, 'department')
        if not dept:
            return result(departments=depts)
        if dept not in depts:
            raise ValueError('请选择目录中的院系')
        q = text_arg(args)
        rows = c.search(q, dept=dept, limit=30, full=True) if q else c.list(dept, limit=200)
        return result(_jsonable(rows), departments=depts, note='检索范围为所选院系；研究方向以教师公开主页为准。')
    finally:
        c.close()


def programs(args):
    from sustech_survival.ws.programs import list_programs, get_program_detail
    if args.get('op') == 'detail':
        data = get_program_detail(integer(args, 'id', maximum=100000000))
        if not data:
            raise RuntimeError('项目详情未能读取')
        return result([{'sections': data.get('sections', {}), 'tables': data.get('tables', [])}])
    data = list_programs(page=max(1, integer(args, 'page')), page_size=20, keywords=text_arg(args) or None)
    rows = [{k: v for k, v in row.items() if k != 'token'} for row in data['programs']]
    return result(rows, total=data.get('record_count', len(rows)), page=data.get('page', 1),
                  note='项目资格、截止时间和申请要求请核对详情；申请在学校系统办理。')


MIRROR_PATHS = {'program': '/courses/本科人才培养方案/',
                'handbook': '/site/sustech-online/documents/freshman-handbook/',
                'map': '/site/sustech-online/documents/campus-map/'}


def mirror(args):
    from sustech_survival.mirror import syllabus
    kind = args.get('kind', 'syllabus')
    if kind == 'syllabus':
        code = text_arg(args).upper()
        if not re.fullmatch(r'[A-Z]{2,10}[0-9]{2,8}[A-Z]?', code):
            raise ValueError('请输入课程代码，例如 CS109')
        # A successful fetch validates presence; do not label a generated URL as a found file.
        content = syllabus.fetch(code)
        text = syllabus.extract_text(content) if args.get('text') == '1' else ''
        return result([{'title': code + ' 教学大纲', 'url': syllabus.syllabus_url(code),
                        'html_url': syllabus.syllabus_html_url(code), 'text': text,
                        'size': len(content)}], note='公开资料镜像可能过时；扫描版 PDF 可能没有可提取文本。')
    if kind not in MIRROR_PATHS:
        raise ValueError('资料类型无效')
    base = syllabus.MIRROR_BASE
    if kind in {'handbook', 'map'}:
        if args.get('path'):
            raise ValueError('请选择公开资料目录中的文件')
        # These mirror directories prohibit listing, while the published files
        # remain available. Discover exact links from the maintaining site's index.
        source = 'https://sustech.online/'
        page = syllabus._session().get(source, timeout=(10, 30))
        page.raise_for_status()
        rows, seen = [], set()
        for a in BeautifulSoup(page.content, 'html.parser').select('a[href]'):
            url = urljoin(source, a['href'])
            parsed = urlparse(url)
            path = unquote(parsed.path)
            if parsed.scheme != 'https' or parsed.netloc != urlparse(base).netloc or url in seen:
                continue
            if not path.startswith(MIRROR_PATHS[kind]) or not path.lower().endswith('.pdf'):
                continue
            with syllabus._session().get(url, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                if not next(response.iter_content(8), b'').startswith(b'%PDF-'):
                    raise RuntimeError('公开资料未返回有效 PDF')
            seen.add(url)
            rows.append({'title': path.rsplit('/', 1)[-1] if kind == 'map' else a.get_text(' ', strip=True),
                         'url': url, 'directory': False})
        if not rows:
            raise ValueError('维护站点暂未列出此类公开资料，请稍后重试')
        return result(rows, source=source, note='公开镜像；请按文件标注的年份和版本使用，可能不是最新学校文件。')
    requested = text_arg(args, 'path', 1000) or MIRROR_PATHS[kind]
    path = unquote(requested)
    if not path.startswith(MIRROR_PATHS[kind]) or any(p in {'.', '..'} for p in path.split('/')) or '\\' in path:
        raise ValueError('资料路径无效')
    if not path.endswith('/'):
        raise ValueError('请选择资料目录')
    url = base + quote(path, safe='/')
    response = syllabus._session().get(url, timeout=(10, 30))
    response.raise_for_status()
    rows, seen = [], set()
    for a in BeautifulSoup(response.text, 'html.parser').select('a[href]'):
        target = urljoin(url, a['href'])
        parsed = urlparse(target)
        target_path = unquote(parsed.path)
        if parsed.netloc != urlparse(base).netloc or not target_path.startswith(path) or target_path == path or target in seen:
            continue
        if not target_path.endswith('/') and not target_path.lower().endswith(('.pdf', '.html', '.docx', '.png', '.jpg')):
            continue
        seen.add(target)
        rows.append({'title': a.get_text(' ', strip=True) or target_path.split('/')[-1], 'url': target,
                     'path': target_path, 'directory': target_path.endswith('/')})
    return result(rows, note='公开资料镜像，不代表本人培养方案或实时修读进度。', source=url)
