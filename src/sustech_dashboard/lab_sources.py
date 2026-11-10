"""Lab discovery reuses Blackboard sessions and the homework download receipts."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .blackboard_work import resource_path
from .homework_sources import download, text
from .homework_backend import command
from .homework_recovery import StageFailure


def collect(bb, settings, root, stop=None):
    cid = settings['course_id']
    course = next((c for c in bb.current_courses() if c['id'] == cid), None)
    if course is None:
        raise StageFailure('Lab 课程不在当前学期，需重新选择课程', kind='blocked')
    prefix = f'/learn/api/public/v1/courses/{cid}/contents'
    folders = bb.results(prefix)
    labs = [r for r in folders if r.get('title', '').strip().lower() == 'labs']
    if len(labs) != 1:
        raise StageFailure('未找到唯一的本学期 Labs 目录', kind='blocked')
    weeks = bb.results(prefix + '/' + labs[0]['id'] + '/children')
    out = []
    for item in weeks:
        match = re.fullmatch(r'Week\s*(\d+)', item.get('title', '').strip(), re.I)
        if not match or int(match[1]) < settings['min_week']:
            continue
        week = int(match[1])
        detail = bb.json(prefix + '/' + item['id'])
        links = []
        for a in BeautifulSoup(detail.get('body', ''), 'html.parser').select('a[href]'):
            name = a.get_text(' ', strip=True)
            if re.fullmatch(r'Exercise\s*0*' + str(week) + r'(?:\s*\(\d+\))?\.pdf', name, re.I):
                links.append((name, resource_path(a['href'])))
        for a in bb.results(prefix + '/' + item['id'] + '/attachments'):
            name = a.get('fileName', '')
            if re.fullmatch(r'Exercise\s*0*' + str(week) + r'(?:\s*\(\d+\))?\.pdf', name, re.I):
                links.append((name, prefix + '/' + item['id'] + '/attachments/' + a['id'] + '/download'))
        links = list({(name, urlsplit(path).path): (name, path) for name, path in links}.values())
        if not links:
            continue  # A teacher may publish the week heading before its exercise.
        if len(links) != 1:
            raise StageFailure(f'Week {week} 的 Exercise PDF 不唯一或尚未发布', kind='blocked')
        name, path = links[0]
        # Query strings may contain short-lived Blackboard credentials. They stay
        # in this host call and never become AI context or public status fields.
        identity = hashlib.sha256((urlsplit(path).path + str(detail.get('modified'))).encode()).hexdigest()
        record = download(bb, {'key': identity[:32], 'path': path, 'name': name,
                              'title': detail.get('title', name)}, Path(root) / 'sources')
        pdf = Path(record['path'])
        body = '\n'.join(p.extract_text() or '' for p in PdfReader(pdf).pages)
        if not body.strip():
            raise StageFailure('Lab PDF 暂无可提取正文，需要人工确认来源', kind='blocked')
        oj = []
        if re.search(r'\b(?:OJ|Hydro)\b', body, re.I):
            target = Path(root) / 'oj' / ('week%02d.json' % week)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                command([settings['node'], str(Path(__file__).with_name('lab_assets') / 'read_oj.mjs'),
                         settings['oj_config'], str(week), str(target)], root,
                        target.with_suffix('.log'), stop, timeout=180, idle_timeout=90)
            except StageFailure as exc:
                error_file = target.with_suffix('.stderr.log')
                if error_file.is_file():
                    reason = next((line for line in error_file.read_text(encoding='utf-8', errors='replace').splitlines()
                                   if line.startswith('OJ read failed: ')), '')
                    if reason:
                        raise StageFailure(reason[:800], kind='blocked') from exc
                raise
            oj = json.loads(target.read_text(encoding='utf-8'))['problems']
            expected = re.search(r'\b(\d+)\s+in\s+total\b', body, re.I)
            if expected and len(oj) != int(expected[1]):
                raise StageFailure('Lab PDF 与 OJ training 的题目数量不一致', kind='blocked')
        fingerprint = hashlib.sha256(json.dumps({'pdf': record['sha256'], 'oj': oj},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        out.append({'course_id': cid, 'course': course['name'], 'name': f'CS109 Week {week}',
                    'content_id': item['id'] + '@' + fingerprint[:20], 'source_id': item['id'],
                    'week': week, 'fingerprint': fingerprint, 'pdf': record, 'requirement': body,
                    'oj': oj, 'status': 'not_submitted', 'due': None})
    return out
