"""PMS adapter. Uploads create a queue item; physical printing remains at the station."""
import hashlib
import json
import tempfile
import threading
from datetime import date
from pathlib import Path

from .actions import mark_sent
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
    return {'stations':_jsonable(c.list_stations()),'groups':_jsonable(c.list_server_groups()),
            'jobs':_jsonable(c.list_print_jobs()),'scans':_jsonable(c.list_scan_jobs()),'updated_at':now()}


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
    c=client();before={j.dw_job_id for j in c.list_print_jobs()}
    root=DATA_ROOT/'print-staging';root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        path=Path(tmp)/filename;path.write_bytes(content)
        c.upload_print(path,**opts,dry_run=True)
        mark_sent()
        result=c.upload_print(path,**opts)
    if not result.ok:return {'state':'rejected','message':str(result.message or '打印系统拒绝上传')[:300]}
    new=[j for j in c.list_print_jobs() if j.dw_job_id not in before and j.file_name==filename]
    valid=[j for j in new if j.dw_copies==opts['copies'] and j.is_color==(opts['color']==2) and j.is_duplex==(opts['duplex']!=1)]
    if len(valid)==1:return {'state':'confirmed','message':'已在官方打印队列中核对到新文档；请到打印点刷卡确认并打印','job_id':valid[0].dw_job_id}
    return {'state':'needs_review','message':'上传已被接收，队列可能正在转换文档；请刷新队列核对，暂不重复上传'}


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
