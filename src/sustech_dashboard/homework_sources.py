"""Reuse authenticated Blackboard readers; stage only teacher inputs for AI."""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup

from .blackboard_work import resource_path
from .core import safe_name


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def text(html_body):
    soup = BeautifulSoup(html_body or '', 'html.parser')
    for node in soup.select('script,style,input,form'):
        node.decompose()
    return soup.get_text('\n', strip=True)


def collect(bb, assignment):
    from .academic_extras import announcements, course_in_scope
    cid, iid = assignment['course_id'], assignment['content_id']
    course_in_scope(bb, cid)
    content = bb.json(f'/learn/api/public/v1/courses/{cid}/contents/{iid}')
    resources = {}
    contexts = []

    def add(name, path, title, origin):
        path = resource_path(path)
        key = hashlib.sha256(path.encode()).hexdigest()[:24]
        resources.setdefault(key, {'key': key, 'name': name or '课程附件', 'path': path,
                                   'title': title, 'origin': origin})

    for item in bb.attachments(cid):
        path = f'/learn/api/public/v1/courses/{cid}/contents/{item["content_id"]}/attachments/{item["id"]}/download'
        add(item['file_name'], path, item.get('title', ''), ' / '.join(item.get('folders', [])))
    for item in bb._contents(cid):
        body = item.get('body') or ''
        if body:
            contexts.append({'title': item.get('title'), 'body': text(body)})
        for anchor in BeautifulSoup(body, 'html.parser').select('a[href],img[src]'):
            try:
                add(anchor.get_text(' ', strip=True) or anchor.get('alt') or '内嵌图片',
                    anchor.get('href') or anchor.get('src'), item.get('title', ''), '课程正文')
            except ValueError:
                pass  # General external links remain source text, not authenticated downloads.
    for item in announcements(bb, cid)['items']:
        contexts.append({'title': item['title'], 'origin': '公告', 'body': text(item['body_html'])})
        soup = BeautifulSoup(item['body_html'], 'html.parser')
        for anchor in soup.select('a[href],img[src]'):
            url = anchor.get('href') or anchor.get('src')
            path = parse_qs(urlparse(url).query).get('path', [None])[0]
            if path:
                add(anchor.get_text(' ', strip=True) or anchor.get('alt') or '公告图片', path, item['title'], '公告')
    result = {'course': assignment['course'], 'assignment': assignment['name'],
              'requirement': text(content.get('body')), 'contexts': contexts,
              'resources': [{k: v for k, v in r.items() if k != 'path'} for r in resources.values()]}
    if not result['requirement'] and not result['contexts'] and not result['resources']:
        raise ValueError('未找到教师作业要求或可用附件，需要补充来源')
    return result, resources


def download(bb, resource, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    name = safe_name(resource['name'])[:160]
    path = root / (resource['key'] + '-' + name)
    receipt = path.with_name(path.name + '.receipt.json')
    candidates = [path] + ([path.with_suffix(ext) for ext in ('.pdf', '.png', '.jpg', '.gif')] if not path.suffix else [])
    for cached in candidates:
        cached_receipt = cached.with_name(cached.name + '.receipt.json')
        if cached.exists() and cached_receipt.exists():
            old = json.loads(cached_receipt.read_text(encoding='utf-8'))
            if old['key'] == resource['key'] and file_sha(cached) == old['sha256']:
                return old
            raise ValueError('缓存的源文件发生变化，已保留原文件，请核对后再处理')
    part = path.with_name(path.name + '.part')
    total = 0
    with bb.get(resource['path'], stream=True) as response, part.open('wb') as target:
        for chunk in response.iter_content(1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ValueError('课程附件超过单文件 512 MB 上限，请选择准确的原件')
            target.write(chunk)
    with part.open('rb') as f:
        header = f.read(16)
    if not total or header.lstrip().lower().startswith((b'<!doctype', b'<html')):
        raise ValueError('附件未返回有效文件，可能需要重新登录校园账号')
    # Announcement links often have descriptive names without an extension.
    suffix = next((ext for magic, ext in ((b'%PDF', '.pdf'), (b'\x89PNG\r\n\x1a\n', '.png'),
                  (b'\xff\xd8\xff', '.jpg'), (b'GIF8', '.gif')) if header.startswith(magic)), '')
    if not Path(name).suffix and suffix:
        path = path.with_suffix(suffix)
        receipt = path.with_name(path.name + '.receipt.json')
    if path.exists():
        raise ValueError('目标源文件已存在，未覆盖')
    part.replace(path)
    result = {'key': resource['key'], 'name': resource['name'], 'title': resource['title'],
              'path': str(path), 'sha256': file_sha(path), 'size': total}
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
