"""Lab discovery reuses Blackboard sessions and the homework download receipts."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader
from PIL import Image

from .blackboard_work import resource_path
from .homework_sources import download, text, file_sha
from .homework_backend import command
from .homework_recovery import StageFailure


def source_images(reader, root):
    """Extract original embedded image streams; never rasterize a PDF page."""
    result = []
    for number, page in enumerate(reader.pages, 1):
        for image in getattr(page, 'images', []):
            raw = image.data
            suffix = Path(image.name).suffix.lower()
            if suffix not in ('.png', '.jpg', '.jpeg') or not 0 < len(raw) <= 10 * 1024 * 1024:
                raise StageFailure('Lab 原图格式或大小暂不支持，需要核对来源', kind='blocked')
            with Image.open(io.BytesIO(raw)) as decoded:
                if decoded.width * decoded.height > 40_000_000:
                    raise StageFailure('Lab 原图尺寸超出来源预算', kind='blocked')
                decoded.verify()
            if len(result) >= 32:
                raise StageFailure('Lab 原图数量超出来源预算', kind='blocked')
            digest = hashlib.sha256(raw).hexdigest()
            path = Path(root) / 'images' / (digest + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and file_sha(path) != digest:
                raise StageFailure('Lab 原图缓存发生变化', kind='blocked')
            if not path.exists():
                path.write_bytes(raw)
            result.append({'path': str(path), 'sha256': digest, 'page': number})
    return result


def source_code(record):
    """Read bounded teacher source files from an attachment without extracting/executing it."""
    path = Path(record['path'])
    if file_sha(path) != record['sha256']:
        raise StageFailure('Lab 教师源码附件发生变化', kind='blocked')
    files = []
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(p.file_size for p in entries) > 32 * 1024 * 1024:
                raise StageFailure('Lab 教师源码包超出来源预算', kind='blocked')
            for item in entries:
                name = item.filename.replace('\\', '/')
                if name.startswith('/') or '..' in name.split('/') or ':' in name:
                    raise StageFailure('Lab 教师源码包路径无效', kind='blocked')
                if not item.is_dir() and Path(name).suffix.lower() in ('.java', '.md', '.txt'):
                    if item.file_size > 1024 * 1024:
                        raise StageFailure('Lab 单个教师源文件过大', kind='blocked')
                    raw = archive.read(item)
                    files.append({'name': name, 'text': raw.decode('utf-8-sig'),
                                  'sha256': hashlib.sha256(raw).hexdigest()})
    else:
        if path.stat().st_size > 1024 * 1024:
            raise StageFailure('Lab 单个教师源文件过大', kind='blocked')
        raw = path.read_bytes()
        files.append({'name': record['name'], 'text': raw.decode('utf-8-sig'),
                      'sha256': hashlib.sha256(raw).hexdigest()})
    if not files:
        raise StageFailure('Lab 教师附件中未找到可读源码', kind='blocked')
    if file_sha(path) != record['sha256']:
        raise StageFailure('读取期间 Lab 教师源码附件发生变化', kind='blocked')
    return {'name': record['name'], 'sha256': record['sha256'], 'files': files}


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
        links, supporting = [], []
        for a in BeautifulSoup(detail.get('body', ''), 'html.parser').select('a[href]'):
            name = a.get_text(' ', strip=True)
            if re.fullmatch(r'Exercise\s*0*' + str(week) + r'(?:\s*\(\d+\))?\.pdf', name, re.I):
                links.append((name, resource_path(a['href'])))
            elif Path(name).suffix.lower() in ('.zip', '.java', '.md', '.txt'):
                supporting.append((name, resource_path(a['href'])))
        for a in bb.results(prefix + '/' + item['id'] + '/attachments'):
            name = a.get('fileName', '')
            if re.fullmatch(r'Exercise\s*0*' + str(week) + r'(?:\s*\(\d+\))?\.pdf', name, re.I):
                links.append((name, prefix + '/' + item['id'] + '/attachments/' + a['id'] + '/download'))
            elif Path(name).suffix.lower() in ('.zip', '.java', '.md', '.txt'):
                supporting.append((name, prefix + '/' + item['id'] + '/attachments/' + a['id'] + '/download'))
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
        reader = PdfReader(pdf)
        body = '\n'.join(p.extract_text() or '' for p in reader.pages)
        if not body.strip():
            raise StageFailure('Lab PDF 暂无可提取正文，需要人工确认来源', kind='blocked')
        images = source_images(reader, Path(root) / 'sources')
        code = []
        for source_name, source_path in dict.fromkeys(supporting):
            key = hashlib.sha256((urlsplit(source_path).path + str(detail.get('modified'))).encode()).hexdigest()
            code.append(source_code(download(bb, {'key': key[:32], 'path': source_path,
                'name': source_name, 'title': detail.get('title', source_name)}, Path(root) / 'sources')))
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
        bundle_sha = hashlib.sha256(json.dumps({'sources': code,
            'images': [{k: p[k] for k in ('sha256', 'page')} for p in images]}, sort_keys=True).encode()).hexdigest()
        out.append({'course_id': cid, 'course': course['name'], 'name': f'CS109 Week {week}',
                    'content_id': item['id'] + '@' + fingerprint[:20], 'source_id': item['id'],
                    'week': week, 'fingerprint': fingerprint, 'pdf': record, 'requirement': body,
                    'oj': oj, 'supporting_sources': code, 'source_images': images, 'bundle_sha': bundle_sha,
                    'status': 'not_submitted', 'due': None})
    return out
