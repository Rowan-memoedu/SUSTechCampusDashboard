"""Current-term Blackboard announcements, content discovery and personal grades."""
from urllib.parse import quote, urlparse
import re

from bs4 import BeautifulSoup

from .blackboard_work import resource_path, safe_html
from .provider import BB_BASE
from .core import CHINA_TZ
from datetime import datetime


def course_in_scope(bb, cid):
    course = next((c for c in bb.current_courses() if c['id'] == cid), None)
    if course is None:
        raise ValueError('该课程不在当前学期范围内')
    return course


def announcements(bb, cid, prefix=''):
    course_in_scope(bb, cid)
    rows = bb.results(f'/learn/api/public/v1/courses/{cid}/announcements')
    items = []
    rendered = None
    for record in rows:
        if record.get('draft') is True:
            continue
        # Some Learn installations return only a summary in the collection.
        if 'body' not in record:
            record = bb.json(f'/learn/api/public/v1/courses/{cid}/announcements/{quote(record["id"], safe="")}')
        if record.get('draft') is True:
            continue
        body = record.get('body') or ''
        files, seen = [], set()
        soup = BeautifulSoup(body, 'html.parser')
        # Learn's REST body contains bare XIDs. The official course page adds
        # announcement permission context; following a bare XID can return 404.
        # Reuse the exact school-rendered URL, matched by announcement and XID.
        resources = [(tag, attr) for tag, attr in
                     [(t, 'href') for t in soup.select('a[href]')] +
                     [(t, 'src') for t in soup.select('img[src]')]
                     if re.fullmatch(r'/bbcswebdav/xid-\d+_\d+', urlparse(tag.get(attr, '')).path)]
        if resources:
            if rendered is None:
                page = bb.get('/webapps/blackboard/execute/announcement?method=search&context=course&course_id=' + quote(cid, safe=''))
                rendered = BeautifulSoup(page.text, 'html.parser')
            block = rendered.find(id=record['id'])
            resolved = {}
            if block:
                for tag in block.select('a[href],img[src]'):
                    value = tag.get('href') or tag.get('src')
                    try:
                        path = resource_path(value)
                    except ValueError:
                        continue
                    match = re.search(r'/xid-(\d+_\d+)$', urlparse(path).path)
                    if match:
                        resolved[match[1]] = path
            for tag, attr in resources:
                xid = urlparse(tag[attr]).path.rsplit('xid-', 1)[1]
                if xid not in resolved:
                    raise ValueError('公告附件的课程访问地址暂未读取成功，请刷新后重试')
                tag[attr] = resolved[xid]
        for anchor in soup.select('a[href]'):
            try:
                path = resource_path(anchor['href'])
            except ValueError:
                continue
            if path in seen:
                continue
            seen.add(path)
            name = anchor.get_text(' ', strip=True) or '公告附件'
            files.append({'name': name, 'url': prefix + '/api/blackboard-resource?path=' + quote(path, safe='')})
        # Preserve links in their original body position, without duplicate rows.
        items.append({'id': record['id'], 'title': record.get('title') or '课程公告',
                      'body_html': safe_html(str(soup), prefix), 'attachments': files,
                      'created': record.get('created'), 'modified': record.get('modified')})
    items.sort(key=lambda a: (a.get('created') or '', a['id']), reverse=True)
    return {'items': items, 'updated_at': datetime.now(CHINA_TZ).isoformat()}


def user_grades(bb, cid):
    cache = getattr(bb, '_user_grades', {})
    if cid not in cache:
        me = getattr(bb, '_me', None) or bb.json('/learn/api/public/v1/users/me')
        bb._me = me
        rows = bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/users/{me["id"]}')
        cache[cid] = {r['columnId']: r for r in rows if r.get('columnId')}
        bb._user_grades = cache
    return cache[cid]


def exempt(grade):
    return grade.get('exempt') is True or grade.get('exemption') is True


def gradebook(bb, prefix=''):
    rows, warnings = [], []
    for course in bb.current_courses():
        cid = course['id']
        try:
            columns = bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/columns')
            own = user_grades(bb, cid)
        except Exception:
            warnings.append(f'{course["name"]} 的成绩簿暂未读取成功')
            continue
        for col in columns:
            grade = own.get(col['id'], {})
            rows.append({'course': course['name'], 'course_id': cid, 'column_id': col['id'],
                         'name': col.get('name') or '成绩项', 'score': grade.get('score'),
                         'display_grade': grade.get('displayGrade'), 'status': grade.get('status'),
                         'exempt': exempt(grade), 'possible': col.get('score', {}).get('possible'),
                         'feedback_html': safe_html(grade.get('feedback'), prefix), 'source': 'gradebook_user_grade'})
    return {'rows': rows, 'warnings': warnings, 'updated_at': datetime.now(CHINA_TZ).isoformat(),
            'note': '本人列成绩来自 Blackboard 成绩簿；尝试成绩保留在作业详情中。未公布与免交分别显示，不将这些项目折算为 TIS 成绩或 GPA。'}


def content_search(bb, args, prefix=''):
    query = str(args.get('q', '')).strip().casefold()
    kind = args.get('kind', '')
    cid = args.get('course', '')
    page = max(1, int(args.get('page', 1)))
    courses = bb.current_courses()
    if cid:
        course_in_scope(bb, cid)
    items, counts, warnings = [], {}, []
    for course in courses:
        if cid and cid != course['id']:
            continue
        try:
            contents = list(bb._contents(course['id']))
        except Exception:
            warnings.append(f'{course["name"]} 的内容暂未读取成功')
            continue
        for item in contents:
            content_kind = item.get('contentHandler', {}).get('id', 'other').removeprefix('resource/x-bb-')
            counts[content_kind] = counts.get(content_kind, 0) + 1
            body = BeautifulSoup(item.get('body') or '', 'html.parser').get_text(' ', strip=True)
            if kind and kind != content_kind:
                continue
            if query and query not in (' '.join([course['name'], item.get('title', ''), body, *item.get('_folder_path', [])])).casefold():
                continue
            items.append({'id': item['id'], 'course_id': course['id'], 'course': course['name'],
                          'title': item.get('title') or '未命名内容', 'kind': content_kind,
                          'folders': item.get('_folder_path', []), 'excerpt': body[:400],
                          'url': BB_BASE + '/webapps/blackboard/execute/displayIndividualContent?' +
                          f'course_id={quote(course["id"], safe="")}&content_id={quote(item["id"], safe="")}'})
    return {'items': items[(page-1)*20:page*20], 'total': len(items), 'page': page,
            'counts': counts, 'courses': courses, 'warnings': warnings,
            'updated_at': datetime.now(CHINA_TZ).isoformat()}
