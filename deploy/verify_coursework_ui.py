"""Verify coursework presentation against fixtures; no campus credentials or writes."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix='coursework-ui-', dir=args.cache))
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(private),
                      SUSTECH_DOWNLOAD_ROOT=str(private / 'downloads'),
                      SUSTECH_REGISTER_PROTOCOL='0', SUSTECH_EXECUTION_MODE='local')
    for key in ('SUSTECH_CLOUD', 'SUSTECH_PUBLIC_HOST', 'CREDENTIALS_DIRECTORY'):
        os.environ.pop(key, None)
    from sustech_dashboard.app import create_app
    from sustech_dashboard.runtime import Runtime
    from sustech_dashboard.browser_services import browser_context
    from werkzeug.serving import make_server, WSGIRequestHandler
    runtime = Runtime()
    runtime.configured.set()
    class Quiet(WSGIRequestHandler):
        def log(self, *args, **kwargs):
            pass
    server = make_server('127.0.0.1', 0, create_app(runtime), threaded=True, request_handler=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    stamp = '2026-10-12T12:00:00+08:00'
    date_task = dict(course='样例课程', course_id='c', content_id='date', name='只提供日期的作业',
                     column_id='g', status='draft', due=None, due_date_only='2026-10-20',
                     due_precision='date', due_source='gradebook', due_conflict=False,
                     submission_evidence='attempt_status', latest_attempt_state='draft', fetched_at=stamp)
    cached_task = dict(date_task, content_id='cache', name='缓存中的作业', stale=True)
    conflict = dict(date_task, content_id='conflict', name='时间冲突的作业', status='submitted',
                    latest_attempt_state='draft', due='2026-10-20T10:00:00Z', due_precision='time',
                    due_date_only=None, due_conflict=True, due_candidates=[
                        {'value': '2026-10-20T10:00:00Z', 'precision': 'time', 'source': 'gradebook'},
                        {'value': '2026-10-21T10:00:00Z', 'precision': 'time', 'source': 'calendar'}])
    tasks = [date_task, cached_task, conflict]
    coverage = [{'course': '样例课程', 'state': 'failed', 'checked_at': stamp,
                 'received': 2, 'retained': 1, 'errors': ['attempts_unavailable']}]
    state = dict(updated_at=stamp, tis={}, warnings=[], errors={}, assignments=tasks,
                 assignment_coverage=coverage)
    report = {'school_writes': 0, 'errors': [], 'widths': [320, 375, 768, 1280]}
    def api(route):
        path = route.request.url.split(origin, 1)[-1]
        if path == '/api/status':
            return route.fulfill(json=state)
        if path.startswith('/api/assignment/'):
            task = next(t for t in tasks if path.endswith('/' + t['content_id']))
            return route.fulfill(json=dict(task, column={}, content={}, attempts=[],
                attachments=[], description_html='<p>隔离样例要求</p>', warnings=[],
                can_submit=False, official_url='https://example.org/assignment'))
        if path == '/api/instance':
            return route.fulfill(json={'configured': True, 'managed': True, 'mode': 'local',
                'version': 'fixture', 'update': {'state': 'current', 'available': False}})
        return route.fulfill(json={'items': [], 'courses': [], 'records': [], 'jobs': [],
            'library': [], 'ehall': [], 'errors': [], 'baseline_ready': True,
            'agent': {'online': False}})
    try:
        with browser_context() as context:
            assert context.request.post(origin + '/auth/unlock', headers={'Origin': origin},
                                        data={'token': runtime.token}).status == 200
            context.route('**/api/**', api)
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            page.goto(origin)
            page.locator('.assignment').first.wait_for()
            assert '读取不完整' in page.locator('#metrics').inner_text()
            assert '≥ 2' in page.locator('#metrics').inner_text()
            assert '仅日期' in page.locator('.assignment').first.inner_text()
            assert '具体时刻未提供' in page.locator('.assignment').first.inner_text()
            assert '缓存' in page.locator('.assignment').nth(1).inner_text()
            page.locator('.assignment').nth(2).locator('summary').click()
            page.get_by_text('截止时间来源：评分列 · 具体时刻', exact=True).wait_for()
            assert '课程日历' in page.locator('.assignment').nth(2).inner_text()
            assert '最新一次是草稿' in page.locator('.assignment').nth(2).inner_text()
            page.locator('#assignment-coverage summary').click()
            assert '读取失败' in page.locator('#assignment-coverage').inner_text()
            for width in report['widths']:
                page.set_viewport_size({'width': width, 'height': 900})
                page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                dimensions = page.evaluate('({inner:innerWidth,scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
                overflow = page.evaluate('Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,id:e.id,className:e.className,text:e.textContent.slice(0,70)})).slice(0,8)')
                widths = page.evaluate('Array.from(document.querySelectorAll("body *")).filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>({tag:e.tagName,id:e.id,className:e.className,scroll:e.scrollWidth,client:e.clientWidth,right:e.getBoundingClientRect().right,overflow:getComputedStyle(e).overflowX})).slice(0,12)')
                page.screenshot(path=str(args.output / f'coursework-{width}.png'), full_page=True)
                assert dimensions['scroll'] <= dimensions['inner'], (width, dimensions, overflow, widths)
            state.update(assignments=[], assignment_coverage=[dict(coverage[0], state='complete', received=0, retained=0, errors=[])])
            page.evaluate('renderDashboard({updated_at:"2026-10-12T12:00:00+08:00",tis:{},assignments:[],assignment_coverage:[{course:"样例",state:"complete",received:0,retained:0,checked_at:"2026-10-12T12:00:00+08:00"}]})')
            assert page.locator('.assignment').count() == 0
            assert '暂无作业' in page.locator('#assignments').inner_text()
            assert not report['errors'], report['errors']
        report['passed'] = True
    finally:
        server.shutdown()
        (args.output / 'coursework-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
