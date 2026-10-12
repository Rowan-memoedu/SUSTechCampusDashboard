"""Render the real application in isolated Chrome; all school APIs are fixtures."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlparse, parse_qs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    a = p.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    a.output.mkdir(parents=True, exist_ok=True)
    instance = Path(tempfile.mkdtemp(prefix='extras-ui-', dir=a.cache))
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(instance), SUSTECH_DOWNLOAD_ROOT=str(instance / 'downloads'),
                      SUSTECH_EXECUTION_MODE='local', SUSTECH_REGISTER_PROTOCOL='0')
    for name in ('SUSTECH_CLOUD', 'SUSTECH_PUBLIC_HOST', 'CREDENTIALS_DIRECTORY'):
        os.environ.pop(name, None)
    from sustech_dashboard.app import create_app
    from sustech_dashboard.runtime import Runtime
    from sustech_dashboard.browser_services import browser_context
    from werkzeug.serving import make_server, WSGIRequestHandler
    runtime = Runtime()
    runtime.configured.set()
    class Quiet(WSGIRequestHandler):
        def log(self, *args, **kw):
            pass
    server = make_server('127.0.0.1', 0, create_app(runtime), threaded=True, request_handler=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    stamp = '2026-10-12T12:00:00+08:00'
    common = dict(updated_at=stamp, source_updated_at={}, errors={}, warnings=[], tis={}, assignments=[],
                  courses=[], items=[], jobs=[], records=[], library=[], ehall=[], agent={'online': False}, baseline_ready=True)
    materials = dict(common, local_open=True, courses=[{'id': 'c', 'name': '示例课程 · 线性代数'}, {'id': 'd', 'name': '没有公告的课程'}],
                     items=[dict(course_id='c', key='file', file_name='第 2 讲.pdf', title='课堂资料', folders=[], local_status='not_downloaded', size=4096)])
    announcements = {'items': [dict(id='a', title='课程安排与参考资料', created=stamp,
        attachments=[{'name': '参考讲义.pdf', 'url': '/api/blackboard-resource?path=%2Fbbcswebdav%2Ffixture.pdf'}],
        body_html='<p>下次课请预习第二章。</p><p><a href="/api/blackboard-resource?path=%2Fbbcswebdav%2Ffixture.pdf" target="_blank">参考讲义.pdf</a></p>')], 'updated_at': stamp}
    rows = {
        'transit': {'items': [{'id': 'line1', 'title': '1 路校巴', 'routes': [{'name': '查看班次'}]}]},
        'nces': {'items': [{'id': 1, 'name': '示例课程', 'teacher_names': '示例教师', 'rate_average': 8.5}], 'total': 1},
        'faculty': {'items': [{'name': '示例教师', 'slug': 'fixture', 'department': '计算机系'}], 'departments': ['计算机系']},
        'programs': {'items': [{'ID': 1, 'Name': '示例交流项目', 'ApplyEndDate': '2026-12-01'}], 'total': 1},
        'mirror': {'items': [{'title': 'CS109 教学大纲', 'url': 'https://example.org/syllabus.pdf', 'html_url': 'https://example.org/syllabus.html'}]},
        'library': {'items': [{'id': 'alma123', 'title': 'Linear Algebra', 'body': '图书 · 电子版', 'url': 'https://example.org/book'}]},
        'content': {'items': [{'id': 'text', 'title': '课程介绍', 'course': '示例课程', 'kind': 'document', 'folders': [], 'excerpt': '正文检索结果', 'url': 'https://example.org/content'}], 'courses': [{'id': 'c', 'name': '示例课程'}], 'counts': {'document': 1}},
        'cle': {'items': [{'slot_id': 'slot', 'service': '写作指导', 'when': '2026-10-14 14:00', 'teacher': '示例教师', 'room': '指导室', 'status': 'free'}], 'quota': {'remaining': 3, 'limit': 5}},
        'evaluations': {'items': [{'id': 'eval', 'title': '示例课程', 'status': '0', 'task_type': '期末评教'}]},
    }
    details = {
        'nces': {'items': [{'id': 1, 'name': '示例课程', 'rating': 8.5, 'reviews': [{'text': '课程评价正文', 'semester': '2026 秋'}]}]},
        'faculty': {'items': [{'name': '示例教师', 'profile_text': '研究方向：数值分析', 'profile_url': 'https://example.org/faculty'}]},
        'programs': {'items': [{'sections': {'申请条件': {'年级': '本科生'}}, 'tables': []}]},
        'library': {'items': [{'title': 'Linear Algebra', 'authors': ['Sample Author'], 'subjects': ['Algebra'], 'detail_url': 'https://example.org/book'}]},
    }
    report = {'school_writes': 0, 'errors': [], 'widths': [320, 375, 414, 768, 1280], 'pages': []}
    calls = []
    def route_api(route):
        u = urlparse(route.request.url)
        path, q = u.path, parse_qs(u.query)
        calls.append((route.request.method, path))
        data = dict(common)
        if path == '/api/materials':
            data = materials
        elif path == '/api/venues':
            data = {'errors': [], 'library': [], 'ehall': []}
        elif path == '/api/instance':
            data = dict(managed=True, configured=True, mode='local', version='0.5.0', update={'state': 'current', 'available': False})
        elif path.startswith('/api/announcements/'):
            data = announcements if path.endswith('/c') else {'items': []}
        elif path == '/api/blackboard-resource':
            return route.fulfill(status=200, content_type='application/pdf', body=b'%PDF-1.4\nfixture\n%%EOF')
        elif path.startswith('/api/extras/'):
            kind = path.split('/')[3]
            data = dict(updated_at=stamp, **rows.get(kind, {}))
            op = q.get('op', [''])[0]
            if op == 'detail':
                data = dict(updated_at=stamp, **details[kind])
            if kind == 'transit' and op == 'schedule':
                data['items'] = [{'title': '1 路校巴', 'times': ['08:00', '08:30'], 'sub_route_name': '内环'}]
            if kind == 'transit' and op == 'places':
                data['items'] = [{'facility_id': 'building:a', 'name': '图书馆', 'routes': [], 'lat': 22.6, 'lng': 113.9}]
            if kind == 'evaluations' and op == 'form':
                data.update(items=[{'id': 'q1', 'text': '课程体验', 'type': 'rating', 'options': ['1', '2', '3', '4', '5']}], note='请自行选择')
            if path.endswith('/preview'):
                data = {'review_id': 'fixture-review', 'summary': {'对象': '隔离样例', '操作': '保存'}}
            if path.endswith('/commit'):
                data = {'state': 'confirmed', 'message': '隔离样例已回读'}
        elif path == '/api/grades':
            data = {'rows': [], 'semesters': [], 'summary': {}, 'warnings': [], 'updated_at': stamp}
        elif path == '/api/blackboard/grades':
            data = {'rows': [dict(name='零分样例', course='课程', score=0, possible=10, exempt=False),
                             dict(name='免交样例', course='课程', exempt=True)], 'warnings': [], 'note': '本人列成绩'}
        route.fulfill(json=data)
    try:
        with browser_context() as context:
            assert context.request.post(origin + '/auth/unlock', headers={'Origin': origin}, data={'token': runtime.token}).status == 200
            context.route('**/api/**', route_api)
            page = context.new_page()
            page.on('pageerror', lambda err: report['errors'].append(str(err)))
            page.goto(origin)
            page.locator('.course-announcements details').wait_for()
            assert page.locator('.service-nav>a').count() == 3
            assert page.locator('main>section').count() == 8
            assert not page.locator('#venues-message').inner_text()
            assert page.locator('.material-course').first.evaluate('(e)=>e.querySelector(".course-announcements").compareDocumentPosition(e.querySelector(".material-files")) & Node.DOCUMENT_POSITION_FOLLOWING')
            page.locator('.course-announcements summary').click()
            assert page.get_by_text('参考讲义.pdf', exact=True).is_visible()
            response = page.evaluate('async () => {const r=await fetch(document.querySelector(".course-announcements a").href);return [r.status,(await r.text()).slice(0,5)]}')
            assert response == [200, '%PDF-']
            for width in report['widths']:
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Homepage overflow {width}'
                page.screenshot(path=str(a.output / f'announcements-{width}.png'), full_page=True)
            page.wait_for_timeout(5200)
            assert page.locator('.course-announcements details').first.get_attribute('open') is not None
            assert calls.count(('GET', '/api/announcements/c')) == 1
            page.goto(origin + '/grades')
            page.locator('#bb-grade-fold summary').click()
            page.get_by_text('零分样例', exact=True).wait_for()
            assert page.locator('#bb-grade-results .pill').first.inner_text() == '0'
            assert '已免交' in page.locator('#bb-grade-results').inner_text()
            report['gradebook_zero_and_exempt'] = True
            for kind in (*rows, 'borrow'):
                page.goto(origin + '/explore/' + kind)
                if kind == 'borrow':
                    for name, value in [('name', '示例教室'), ('applicant', '隔离样例'), ('phone', '000'), ('purpose', '本机测试')]:
                        page.locator(f'[name={name}]').fill(value)
                    page.locator('#extra-form button[type=submit]').click()
                    page.locator('#extra-review:not([hidden])').wait_for()
                else:
                    if kind not in {'transit', 'faculty', 'evaluations'}:
                        if kind in {'nces', 'programs', 'library', 'content', 'mirror'}:
                            page.locator('[name=q]').fill('CS109')
                        page.locator('#extra-form button[type=submit]').click()
                    try:
                        page.locator('#extra-results .row').first.wait_for(timeout=8000)
                    except Exception:
                        page.screenshot(path=str(a.output / f'{kind}-failure.png'), full_page=True)
                        raise AssertionError(f'{kind}: {page.locator("#extra-message").inner_text()}; {report["errors"]}') from None
                    if kind in details:
                        page.locator('#extra-results button').first.click()
                        page.wait_for_function('() => !document.querySelector("#extra-detail").textContent.includes("正在读取")')
                        assert page.locator('#extra-detail').inner_text().strip()
                    if kind == 'nces':
                        assert '/ 10' in page.locator('#extra-results').inner_text()
                    if kind == 'evaluations':
                        page.get_by_role('button', name='读取问卷').click()
                        page.locator('[data-qid=q1]').select_option('4')
                        page.get_by_role('button', name='预览保存草稿').click()
                        page.locator('#extra-review:not([hidden])').wait_for()
                        assert not any(method == 'POST' and path.endswith('/commit') for method, path in calls)
                        page.get_by_role('button', name='确认发送').click()
                        page.get_by_text('隔离样例已回读', exact=True).wait_for()
                for width in report['widths']:
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{kind} overflow {width}'
                page.screenshot(path=str(a.output / f'{kind}-1280.png'), full_page=True)
                report['pages'].append(kind)
            assert not report['errors'], report['errors']
            report.update(announcement_first=True, attachment_bytes_verified=True, homepage_cards=3,
                          announcement_poll_deduplicated=True, preview_does_not_send=True)
    finally:
        server.shutdown()
    (a.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
