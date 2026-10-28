"""Exercise delayed print receipts in an isolated browser with no school writes."""
import argparse,json,os,re,tempfile,threading
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);a.cache.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='print-ui-',dir=a.cache))
    os.environ.update(SUSTECH_DASHBOARD_DATA_ROOT=str(root),SUSTECH_DOWNLOAD_ROOT=str(root/'downloads'),SUSTECH_EXECUTION_MODE='local',SUSTECH_REGISTER_PROTOCOL='0')
    for key in ('SUSTECH_CLOUD','SUSTECH_PUBLIC_HOST','CREDENTIALS_DIRECTORY'):os.environ.pop(key,None)
    from sustech_dashboard.app import create_app
    from sustech_dashboard.runtime import Runtime
    from sustech_dashboard.browser_services import browser_context
    from werkzeug.serving import make_server,WSGIRequestHandler
    runtime=Runtime();runtime.configured.set()
    class Quiet(WSGIRequestHandler):
        def log(self,*args,**kwargs):pass
    server=make_server('127.0.0.1',0,create_app(runtime),threaded=True,request_handler=Quiet)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}';writes=[];reads=[];operation=None;errors=[]
    def api(route):
        nonlocal operation
        path=route.request.url.split(origin)[-1]
        if path=='/api/printing/upload':
            writes.append(path)
            operation=re.search(r'name="operation_id"\r\n\r\n([^\r]+)',route.request.post_data)[1]
            return route.fulfill(json={'state':'needs_review','message':'上传结果待队列确认；系统只复查队列，不会重新上传。请稍后刷新打印队列。'})
        assert route.request.method=='GET','Unexpected simulated write'
        if path=='/api/printing':
            if operation:reads.append(path)
            confirmed=len(reads)>1
            return route.fulfill(json={'stations':[],'groups':[],'scans':[],'updated_at':'2026-10-28T12:00:00+08:00',
                'jobs':[{'dw_job_id':1,'file_name':'回执样例.pdf','date_str':'2026.10.28','time_str':'12:00','dw_total_pages':2,'dw_copies':1,'paper':'A4','is_color':False,'duplex_label':'双面短边'}] if confirmed else [],
                'reconciled_uploads':[{'operation_id':operation,'state':'confirmed','message':'已在官方打印队列核对文件和打印设置'}] if confirmed else []})
        if path=='/api/printing/relay':return route.fulfill(json={'enabled':False,'agents':[],'pending':[]})
        return route.fulfill(json={'configured':True,'managed':False,'version':'0.5.23','update':{},'agents':[]})
    try:
        with browser_context() as context:
            context.request.post(origin+'/auth/unlock',data={'token':runtime.token},headers={'Origin':origin})
            context.route('**/api/**',api);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
            page.goto(origin+'/printing');page.locator('#print-fields').wait_for(state='visible')
            page.locator('#print-file').set_input_files({'name':'回执样例.pdf','mimeType':'application/pdf','buffer':b'%PDF-fixture'})
            page.locator('#print-form button').click()
            page.get_by_text('上传结果待队列确认',exact=False).wait_for()
            assert len(writes)==1
            page.locator('#print-refresh').click();page.get_by_text('已在官方打印队列核对文件和打印设置',exact=True).wait_for()
            assert page.locator('#print-filename').inner_text()=='尚未选择文件'
            assert len(writes)==1 and not errors
            for width in (320,375,414,768):
                page.set_viewport_size({'width':width,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(a.output/f'printing-{width}.png'),full_page=True)
        report={'passed':True,'fake_upload_calls':len(writes),'delayed_confirmation':True,'cleared_confirmed_selection':True,'script_errors':len(errors),'school_writes':0}
        (a.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
    finally:server.shutdown()

if __name__=='__main__':main()
