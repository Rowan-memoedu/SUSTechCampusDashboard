"""Verify coursework presentation against fixtures; no campus credentials or writes."""
import argparse
import json
import os
import re
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
    report = {'school_writes': 0, 'errors': [], 'widths': [320, 375, 414, 768, 1280]}
    update = {'state':'current','available':False,'latest':'0.5.5','message':'已与正式版本同步'}
    def api(route):
        if route.request.url==origin+'/api/assignment/preview-pdf':
            return route.continue_()
        path = route.request.url.split(origin, 1)[-1]
        if path == '/api/sync' and route.request.method == 'POST':
            # This local read trigger has no school write. Stub it explicitly;
            # every other non-GET remains forbidden, including submissions.
            report['local_sync_requests'] = report.get('local_sync_requests', 0) + 1
            return route.fulfill(status=202, json={'accepted': True, 'syncing': True})
        assert route.request.method=='GET', 'UI acceptance must not submit to school'
        if path == '/api/status':
            return route.fulfill(json=state)
        if path.startswith('/api/assignment/'):
            task = next(t for t in tasks if path.endswith('/' + t['content_id']))
            return route.fulfill(json=dict(task, column={}, content={}, attempts=[{'status':'NeedsGrading','review_files':[
                {'name':'已提交.PDF','url':origin+'/api/blackboard-resource?path=%2Fwebapps%2Fassignment%2Fdownload%3Ffile_id%3Dfixture&name=submitted.PDF'},
                {'name':'说明.txt','url':origin+'/api/blackboard-resource?path=%2Fwebapps%2Fassignment%2Fdownload%3Ffile_id%3Dfixture&name=notes.txt'}]}],
                attachments=[], description_html='<p>隔离样例要求</p>', warnings=[],
                can_submit=True, official_url='https://example.org/assignment'))
        if path == '/api/instance':
            return route.fulfill(json={'configured': True, 'managed': True, 'mode': 'local',
                'version': '0.5.5', 'update': update})
        return route.fulfill(json={'items': [], 'courses': [], 'records': [], 'jobs': [],
            'library': [], 'ehall': [], 'errors': [], 'baseline_ready': True,
            'agent': {'online': False}})
    try:
        with browser_context() as context:
            assert context.request.post(origin + '/auth/unlock', headers={'Origin': origin},
                                        data={'token': runtime.token}).status == 200
            context.route(re.compile(re.escape(origin)+r'/api/(?!assignment/preview-pdf(?:$|\?)|blackboard-resource(?:$|\?))'), api)
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            page.goto(origin)
            page.locator('.assignment').first.wait_for()
            page.wait_for_function("document.querySelector('#campus-sync')?.getAttribute('aria-busy')==='false'")
            assert report.get('local_sync_requests', 0) >= 1
            assert '读取不完整' in page.locator('#metrics').inner_text()
            assert '≥ 2' in page.locator('#metrics').inner_text()
            assert '仅日期' in page.locator('.assignment').first.inner_text()
            assert '具体时刻未提供' in page.locator('.assignment').first.inner_text()
            assert '缓存' in page.locator('.assignment').nth(1).inner_text()
            page.locator('.assignment').nth(2).locator('summary').click()
            page.get_by_text('截止时间来源：评分列 · 具体时刻', exact=True).wait_for()
            # Preview echoes to the user's instance, never to Blackboard.
            import io
            from pypdf import PdfWriter
            pdf=io.BytesIO(); writer=PdfWriter();writer.add_blank_page(width=595,height=842);writer.write(pdf)
            from types import SimpleNamespace
            from sustech_dashboard import app as app_module
            def school_file(path,**kwargs):
                assert path=='/webapps/assignment/download?file_id=fixture'
                return SimpleNamespace(headers={'Content-Type':'application/octet-stream'},iter_content=lambda n:iter([pdf.getvalue()]),close=lambda:None)
            app_module.Blackboard=lambda:SimpleNamespace(get=school_file)
            selected=page.locator('.assignment').nth(2)
            picker=selected.locator('input[type=file]')
            picker.set_input_files([{'name':'作业.PDF','mimeType':'application/octet-stream','buffer':pdf.getvalue()},
                                    {'name':'说明.txt','mimeType':'text/plain','buffer':b'notes'}])
            preview=selected.get_by_role('button',name='预览 作业.PDF',exact=True)
            assert selected.get_by_role('button',name='预览',exact=False).count()==1
            with context.expect_event('response',predicate=lambda r:r.url==origin+'/api/assignment/preview-pdf') as response:
                with page.expect_popup() as opened:
                    preview.click()
            assert response.value.status==200
            assert response.value.headers['content-type']=='application/pdf'
            assert response.value.headers['content-disposition'].startswith('inline;')
            opened.value.wait_for_load_state('domcontentloaded')
            assert opened.value.url==origin+'/api/assignment/preview-pdf'
            # Chrome exposes either an embedded document or only the native
            # extension shell (its reader has a closed DOM). Do not pin a
            # private extension ID/path. Retain a screenshot for visual review.
            try:
                opened.value.wait_for_function("""() =>
                    !!document.querySelector('embed[type="application/pdf"],object[type="application/pdf"],pdf-viewer') ||
                    [...document.querySelectorAll('link[href]')].some(e=>{
                        const u=new URL(e.href);return u.protocol==='chrome-extension:' && /pdf/i.test(u.pathname);
                    })""", timeout=30000)
                report['pdf_reader_elements'] = opened.value.locator('embed,object,pdf-viewer').evaluate_all(
                    "nodes=>nodes.map(e=>({tag:e.tagName,type:e.getAttribute('type')}))")
            finally:
                (args.output/'pdf-reader.html').write_text(opened.value.content(), encoding='utf-8')
                opened.value.screenshot(path=str(args.output/'pdf-reader.png'))
            opened.value.close()
            report['pdf_preview']={'new_tab':True,'local_only':True,'inline_pdf':True,'native_reader':True,'school_writes':0}
            submitted=selected.get_by_role('link',name='预览已提交的 已提交.PDF',exact=True)
            assert selected.get_by_role('link',name='预览已提交的',exact=False).count()==1
            with context.expect_event('response',predicate=lambda r:'/api/blackboard-resource?' in r.url and 'preview=1' in r.url) as reviewed:
                with page.expect_popup() as opened:
                    submitted.click()
            assert reviewed.value.status==200 and reviewed.value.headers['content-type']=='application/pdf'
            assert reviewed.value.headers['content-disposition'].startswith('inline;')
            opened.value.wait_for_load_state('domcontentloaded');opened.value.close()
            report['submitted_pdf_preview']={'new_tab':True,'inline_pdf':True,'preserved_downloads':selected.get_by_role('link',name='下载',exact=True).count()==2,'school_writes':0}
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
            selected.locator('.file-chip').first.get_by_role('button',name='移除',exact=True).click()
            assert selected.get_by_role('button',name='预览',exact=False).count()==0
            page.evaluate('renderDashboard({updated_at:"2026-10-12T12:00:00+08:00",tis:{},assignments:[],assignment_coverage:[{course:"样例",state:"complete",received:0,retained:0,checked_at:"2026-10-12T12:00:00+08:00"}]})')
            assert page.locator('.assignment').count() == 0
            assert '暂无作业' in page.locator('#assignments').inner_text()
            report['update_states'] = []
            for name,message in [('ahead','本机候选版领先正式版，尚未同步'),
                                 ('mismatch','版本号相同，但本机构建与正式版不同，尚未同步'),
                                 ('current','已与正式版本同步')]:
                update.update(state=name,message=message,latest='0.4.3' if name=='ahead' else '0.5.5')
                page.goto(origin+'/settings')
                page.get_by_text(message,exact=False).wait_for()
                assert page.locator('#update-install').is_disabled()
                assert page.locator('#update-panel').get_attribute('data-state')==('success' if name=='current' else 'default')
                for width in (320,375,414,768):
                    page.set_viewport_size({'width':width,'height':900})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(args.output/f'update-{name}.png'),full_page=True)
                report['update_states'].append(name)
            assert not report['errors'], report['errors']
        report['passed'] = True
    finally:
        server.shutdown()
        (args.output / 'coursework-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
