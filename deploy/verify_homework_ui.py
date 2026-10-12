"""Render homework controls in an isolated account. No school requests or real approvals."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='homework-ui-', dir=args.cache))
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(root), SUSTECH_EXECUTION_MODE='local', SUSTECH_REGISTER_PROTOCOL='0')
    for name in ('SUSTECH_CLOUD', 'SUSTECH_PUBLIC_HOST', 'CREDENTIALS_DIRECTORY'):
        os.environ.pop(name, None)
    from sustech_dashboard.app import create_app
    from sustech_dashboard.runtime import Runtime
    from sustech_dashboard.browser_services import browser_context
    from werkzeug.serving import make_server, WSGIRequestHandler
    runtime = Runtime()
    runtime.configured.set()
    class Quiet(WSGIRequestHandler):
        def log(self, *args, **kw): pass
    server = make_server('127.0.0.1', 0, create_app(runtime), threaded=True, request_handler=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    job = {'id': 'fixture', 'course_id': 'c', 'content_id': 'a', 'name': 'Ch 3（隔离样例）',
           'state': 'awaiting_source', 'stage': 'produce', 'revision': 1, 'error': '', 'artifacts': [],
           'course': '物理课程（界面测试）', 'homework_copies': True,
           'plan': {'ids': ['3.1', '3.8', '3.21'], 'books': [{'title': 'Principles of Physics', 'edition': '10th edition',
              'evidence': '课程公告指定的教材；已核对章节、题型、相邻题和跨页内容。'}]},
           'requirement': 'Chapter 3, Problems 1, 8, 21.\n<img src=x onerror=alert(1)>', 'updated': ''}
    state = {'enabled': True, 'backend': 'codex', 'jobs': [job]}
    common = dict(updated_at='2026-10-12T12:00:00+08:00', source_updated_at={}, errors={}, warnings=[], tis={},
                  assignments=[], courses=[], items=[], jobs=[], records=[], library=[], ehall=[], agent={'online': False},
                  baseline_ready=True)
    report = {'widths': [320, 375, 414, 768, 1280], 'errors': [], 'decisions': []}
    fail_next = [False]

    def route_api(route):
        path = urlparse(route.request.url).path
        if path == '/api/homework':
            return route.fulfill(json=state)
        if path.endswith('/decision'):
            data = route.request.post_data_json
            if fail_next[0]:
                fail_next[0] = False
                return route.fulfill(status=400, json={'error': '状态已变化，请刷新后核对（隔离样例）'})
            report['decisions'].append(data['action'])
            job.update(state='complete' if data['action'] == 'accept_pdf' else 'queued', revision=job['revision'] + 1)
            return route.fulfill(json=job)
        if path == '/api/instance':
            return route.continue_()
        if path == '/api/venues':
            return route.fulfill(json=dict(common, errors=[]))
        return route.fulfill(json=common)

    try:
        with browser_context() as context:
            assert context.request.post(origin + '/auth/unlock', headers={'Origin': origin}, data={'token': runtime.token}).status == 200
            context.route('**/api/**', route_api)
            page = context.new_page()
            page.on('pageerror', lambda err: report['errors'].append(str(err)))
            page.goto(origin)
            dialog = page.locator('.homework-dialog')
            dialog.wait_for(state='visible')
            assert not report['decisions']
            dialog.get_by_text('查看教师原始要求').click()
            assert dialog.locator('img').count() == 0
            for width in report['widths']:
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'Page overflow {width}'
                assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth'), f'Dialog overflow {width}'
                page.screenshot(path=str(args.output / f'source-{width}.png'), full_page=True)
            dialog.get_by_role('button', name='稍后核对').click()
            assert not report['decisions']
            page.locator('.homework-panel>summary').click()
            page.get_by_role('button', name='核对', exact=True).click()
            fail_next[0] = True
            dialog.get_by_role('button', name='确认，生成三册').click()
            dialog.get_by_text('状态已变化，请刷新后核对（隔离样例）').wait_for()
            assert not report['decisions']
            page.screenshot(path=str(args.output / 'source-error.png'), full_page=True)
            dialog.get_by_role('button', name='确认，生成三册').click()
            dialog.wait_for(state='hidden')
            assert report['decisions'] == ['accept_source']
            job.update(state='awaiting_pdf', stage='done', revision=job['revision'] + 1,
                       artifacts=[{'module': name, 'name': name + '.pdf', 'pages': pages}
                                  for name, pages in [('questions', 2), ('writing', 6), ('solutions', 5)]])
            page.reload()
            dialog.wait_for(state='visible')
            assert dialog.locator('a').count() == 4
            for width in report['widths']:
                page.set_viewport_size({'width': width, 'height': 900})
                assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth'), f'PDF dialog overflow {width}'
                page.screenshot(path=str(args.output / f'pdf-{width}.png'), full_page=True)
            assert report['decisions'] == ['accept_source']
            dialog.get_by_role('button', name='确认正确，开启自动化').click()
            dialog.wait_for(state='hidden')
            assert report['decisions'] == ['accept_source', 'accept_pdf']
            job.update(state='needs_attention', error='题号不明确（隔离样例）', revision=job['revision'] + 1, artifacts=[])
            page.reload()
            page.locator('.homework-panel>summary').click()
            page.get_by_role('button', name='补充信息', exact=True).click()
            assert dialog.get_by_role('button', name='确认，生成三册').count() == 0
            dialog.get_by_role('textbox').fill('请从教师附件的选题表读取题号。')
            page.screenshot(path=str(args.output / 'needs-attention.png'), full_page=True)
            dialog.get_by_role('button', name='提交补充，重新识别').click()
            dialog.wait_for(state='hidden')
            assert report['decisions'][-1] == 'correct_source'
            assert not report['errors'], report['errors']
            report.update(no_implicit_approval=True, two_explicit_confirmations=True, correction_visible=True,
                          stale_confirmation_rejected=True, teacher_html_is_text=True)
    finally:
        server.shutdown()
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__': main()
