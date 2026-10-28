"""PMS adapter. Uploads create a queue item; physical printing remains at the station."""
import hashlib
import json
import tempfile
import threading
import time
from urllib.parse import urlparse,parse_qs,urljoin
from datetime import date
from pathlib import Path

from .actions import mark_sent,record_evidence,Actions,PendingAction
from .core import DATA_ROOT, safe_name
from .provider import _jsonable
from .study import now

lock=threading.RLock()
_auth=None
NETWORK_MESSAGE='联创打印仅限校园网或学校 VPN。当前服务连接到了校外提示页，无法读取打印队列；这不表示队列为空。'


def check_network():
    import requests
    r=requests.get('https://pms.sustech.edu.cn/api/client/Auth/PublicKey',timeout=(8,20))
    if '仅限校内访问' in r.text or 'available only from the campus network' in r.text:
        raise ValueError(NETWORK_MESSAGE)
    r.raise_for_status()
    if r.json().get('code') != 0:raise ValueError('本机暂时无法访问学校打印接口')


def client():
    global _auth
    import requests
    from sustech_survival.pms import PMSClient
    r=requests.get('https://pms.sustech.edu.cn/client/new/cprintPc/',timeout=(8,20))
    if '仅限校内访问' in r.text or 'available only from the campus network' in r.text:
        raise ValueError(NETWORK_MESSAGE)
    r.raise_for_status()
    if _auth is not None:
        try:
            response=_auth.post('https://pms.sustech.edu.cn/api/client/Auth/Check',timeout=(10,30))
            if response.json().get('code')==0:return PMSClient(_auth)
        except Exception:pass
    from .print_auth import login
    _auth=login()
    return PMSClient(_auth)


def overview():
    c=client()
    jobs=c.list_print_jobs()
    reconciled=reconcile_pending(jobs)
    return {'stations':_jsonable(c.list_stations()),'groups':_jsonable(c.list_server_groups()),
            'jobs':_jsonable(jobs),'scans':_jsonable(c.list_scan_jobs()),'updated_at':now(),
            'reconciled_uploads':reconciled}


def history(args):
    start=date.fromisoformat(args.get('start') or '2026-09-01')
    end=date.fromisoformat(args.get('end') or now()[:10])
    kind=int(args.get('type',1)); page=int(args.get('page',1))
    if start>end or kind not in (1,2,3) or not 1<=page<=10000:raise ValueError('记录查询条件无效')
    rows,pages=client().history(begin=start,end=end,type=kind,page=page,page_size=20)
    values=_jsonable(rows)
    for row in values:
        for key in ('sz_card_no','sz_logon_name','sz_true_name'):row.pop(key,None)
    return {'rows':values,'pages':pages,'page':page,'updated_at':now()}


def options(data):
    opts={k:int(data.get(k,default)) for k,default in [('color',1),('paper',9),('duplex',1),('copies',1),('page_from',0),('page_to',0)]}
    if opts['color'] not in (1,2) or opts['paper'] not in (-1,8,9) or opts['duplex'] not in (1,2,3):raise ValueError('打印设置无效')
    if not 1<=opts['copies']<=100 or not 0<=opts['page_from']<=10000 or not 0<=opts['page_to']<=10000:raise ValueError('份数或页码超出范围')
    if opts['page_from'] and opts['page_to']<opts['page_from']:raise ValueError('结束页不能早于开始页')
    if opts['page_from']==0:opts['page_to']=0
    return opts


def upload_target(filename, content, opts):
    return 'print:upload:'+hashlib.sha256(content+filename.encode()+json.dumps(opts,sort_keys=True).encode()).hexdigest()


def upload(filename, content, opts):
    filename=validate_upload(filename,content)
    phase='connect'
    try:
        c=client();before=[j.dw_job_id for j in c.list_print_jobs()]
        phase='prepare'
        root=DATA_ROOT/'print-staging';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            path=Path(tmp)/filename;path.write_bytes(content)
            c.upload_print(path,**opts,dry_run=True)
            receipt={'filename':filename,'options':opts,'before_ids':before,'at':time.time()}
            receipt['guard']='print-name:'+hashlib.sha256((filename+json.dumps(opts,sort_keys=True)).encode()).hexdigest()
            record_evidence(receipt)
            phase='send';mark_sent()
            try:result=send_upload(c,path,opts)
            except Exception as exc:
                result={'state':'needs_review','message':'上传连接中断，正在核对官方队列；不会自动重新上传','error_type':type(exc).__name__}
            receipt['upload_state']=result['state'];record_evidence(receipt)
    except Exception as exc:
        if isinstance(exc,PendingAction):return {'state':'rejected','message':str(exc),'phase':phase}
        if phase=='send':return {'state':'needs_review','message':'上传已发送，本机回执处理未完成；请刷新队列核对，暂不重复上传','phase':phase,'error_type':type(exc).__name__}
        return {'state':'rejected','message':'文件尚未上传：连接或准备失败，请检查校园打印连接后重试','phase':phase,'error_type':type(exc).__name__}
    if result['state']=='rejected':return result
    for attempt in range(4):
        try:
            match=match_upload(c.list_print_jobs(),receipt)
            if match:return confirmed_upload(match)
        except Exception as exc:
            result['readback_error']=type(exc).__name__
        if attempt<3:time.sleep(1)
    return {**result,'state':'needs_review','message':'上传结果待队列确认；系统只复查队列，不会重新上传。请稍后刷新打印队列。'}


def send_upload(c,path,opts):
    """Use the official form and inspect its redirect without resending a POST."""
    base='https://pms.sustech.edu.cn'
    result_page=base+'/client/new/cprintPc/printDoc.html'
    data={key:str(opts[value]) for key,value in {'dwColor':'color','dwPaperId':'paper','dwDuplex':'duplex','dwFrom':'page_from','dwTo':'page_to','dwCopies':'copies'}.items()}
    data['BackURL']=result_page+'?t='+str(int(time.time()*1000))
    with path.open('rb') as stream:
        response=c.session.post(base+'/api/client/CloudPrint/Upload',files={'szPath':(path.name,stream)},data=data,
            headers={'Origin':base,'Referer':base+'/client/new/cprintPc/cprint.html'},timeout=(10,120),allow_redirects=False)
    with response:
        if response.status_code==413:return {'state':'rejected','message':'学校打印系统拒绝此文件：文件过大'}
        code=None;message=''
        if response.status_code in (301,302,303,307,308):
            redirect=urlparse(urljoin(base,response.headers.get('Location','')))
            if redirect.scheme=='https' and redirect.netloc=='pms.sustech.edu.cn' and redirect.path=='/client/new/cprintPc/printDoc.html':
                query=parse_qs(redirect.query);code=query.get('code',[None])[0];message=query.get('message',[''])[0]
        else:
            try:body=response.json()
            except (ValueError,TypeError):body=None
            if isinstance(body,dict):code=body.get('code');message=body.get('message','')
        if str(code)=='0':return {'state':'accepted','message':'官方已接收上传，正在核对打印队列'}
        if code is not None:
            from bs4 import BeautifulSoup
            detail=BeautifulSoup(str(message),'html.parser').get_text()[:200]
            return {'state':'rejected','message':'学校打印系统拒绝上传'+('：'+detail if detail else '')}
        return {'state':'needs_review','message':'上传回执未包含可核对的结果，正在复查队列','http_status':response.status_code}


def match_upload(jobs,receipt):
    if time.time()-receipt['at']>900:return None
    before={str(value) for value in receipt['before_ids']}
    opts=receipt['options'];expected={1:'单面',2:'双面短边',3:'双面长边'}[opts['duplex']]
    matches=[j for j in jobs if str(j.dw_job_id) not in before and j.file_name==receipt['filename']
        and int(j.dw_copies)==opts['copies'] and j.is_color==(opts['color']==2)
        and j.duplex_label==expected and (opts['paper']==-1 or j.paper=={8:'A3',9:'A4'}[opts['paper']])]
    return matches[0] if len(matches)==1 else None


def confirmed_upload(job):
    return {'state':'confirmed','message':'已在官方打印队列核对文件和打印设置；请到打印点刷卡确认并打印','job_id':job.dw_job_id}


def reconcile_pending(jobs,ledger=None):
    ledger=ledger or Actions();results=[]
    with ledger.connect() as db:
        used={str(json.loads(row[0] or '{}').get('job_id')) for row in db.execute("SELECT result FROM actions WHERE object LIKE 'print:upload:%' AND state='confirmed'")}
        rows=db.execute("SELECT id,object,result FROM actions WHERE object LIKE 'print:upload:%' AND state IN ('sending','needs_review')").fetchall()
        candidates=[]
        for token,target,raw in rows:
            receipt=json.loads(raw or '{}').get('receipt')
            if not receipt or receipt.get('upload_state')=='rejected':continue
            job=match_upload([j for j in jobs if str(j.dw_job_id) not in used],receipt)
            if job:
                candidates.append((token,receipt,job))
        for token,receipt,job in candidates:
            if sum(str(other.dw_job_id)==str(job.dw_job_id) for _,_,other in candidates)!=1:continue
            if str(job.dw_job_id) in used:continue
            used.add(str(job.dw_job_id))
            result={**confirmed_upload(job),'receipt':receipt}
            db.execute("UPDATE actions SET state='confirmed',result=? WHERE id=? AND state IN ('sending','needs_review')",(json.dumps(result,ensure_ascii=False),token))
            results.append({**result,'operation_id':token})
    return results


def validate_upload(filename,content):
    if not filename or not content:raise ValueError('请选择要打印的文件')
    if len(content)>50*1024*1024:raise ValueError('单个打印文件不能超过 50 MB')
    filename=safe_name(filename)
    if Path(filename).suffix.lower() not in {'.pdf','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.jpg','.jpeg','.png','.txt'}:
        raise ValueError('不支持该文件格式，请优先使用 PDF')
    return filename


def delete(kind, job_id):
    if kind not in {'print','scan'}:raise ValueError('文档类型无效')
    c=client();get=c.list_print_jobs if kind=='print' else c.list_scan_jobs
    if not any(j.dw_job_id==job_id for j in get()):raise ValueError('该文档已不在官方队列中，请刷新')
    mark_sent()
    ok=(c.delete_print_job if kind=='print' else c.delete_scan_job)(job_id)
    if not ok:return {'state':'rejected','message':'打印系统拒绝删除'}
    if any(j.dw_job_id==job_id for j in get()):return {'state':'needs_review','message':'官方队列尚未确认删除，请刷新核对'}
    return {'state':'confirmed','message':'已回读官方队列，文档已删除'}
