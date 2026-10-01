"""Read Original Learn assignment details and submit through its current official form."""
import html
import re
from urllib.parse import urljoin, urlparse,unquote
from bs4 import BeautifulSoup
from .core import CHINA_TZ, attachment_key, parse_dt, submission_status
from .provider import BB_BASE, _api_path


def resource_path(value):
    parsed=urlparse(urljoin(BB_BASE,value))
    if parsed.scheme!='https' or parsed.netloc!='bb.sustech.edu.cn':
        raise ValueError('资源地址无效')
    if any(p in {'.','..'} for p in unquote(parsed.path).replace('\\','/').split('/')):raise ValueError('资源路径无效')
    if not (parsed.path.startswith('/bbcswebdav/') or
            parsed.path.startswith('/webapps/blackboard/execute/downloadFile') or
            parsed.path == '/webapps/assignment/download' or
            (parsed.path.startswith('/learn/api/public/') and parsed.path.endswith('/download'))):
        raise ValueError('该资源须在 Blackboard 原页面查看')
    return parsed.path + ('?'+parsed.query if parsed.query else '')


def safe_html(body, prefix=''):
    from urllib.parse import quote
    soup=BeautifulSoup(body or '', 'html.parser')
    allowed={'p','div','span','strong','b','em','i','u','br','hr','ul','ol','li','table','thead','tbody','tr','th','td','h1','h2','h3','h4','h5','blockquote','pre','code','a','img','sub','sup'}
    for tag in list(soup.find_all(True)):
        if tag.name in {'script','style','iframe','object','embed','form','input','button','svg'}:
            tag.decompose();continue
        if not tag.name: continue
        if tag.name not in allowed: tag.unwrap();continue
        attrs={k:v for k,v in tag.attrs.items() if k in {'colspan','rowspan','alt','title'}}
        for attr in ('href','src'):
            if tag.name == ('a' if attr=='href' else 'img') and tag.get(attr):
                url=urljoin(BB_BASE,tag[attr]); parsed=urlparse(url)
                if parsed.scheme not in {'https','http'}: continue
                if parsed.netloc == 'bb.sustech.edu.cn':
                    try: url=prefix+'/api/blackboard-resource?path='+quote(resource_path(url),safe='')
                    except ValueError:
                        if attr=='src': continue
                elif attr=='src': continue
                attrs[attr]=url
        if tag.name=='a': attrs.update(target='_blank',rel='noopener noreferrer')
        tag.attrs=attrs
    return str(soup)


def assignment_record(bb, cid, iid):
    course=next((c for c in bb.current_courses() if c['id']==cid),None)
    if not course: raise ValueError('该课程不在当前学期范围内')
    col=next((c for c in bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/columns') if c.get('contentId')==iid and c.get('grading',{}).get('type')=='Attempts'),None)
    if not col: raise ValueError('作业不存在或不可访问')
    content=bb.json(f'/learn/api/public/v1/courses/{cid}/contents/{iid}')
    attempts=bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/columns/{col["id"]}/attempts')
    return course,col,content,attempts


def current_form(bb,cid,iid):
    page=bb.get(f'/webapps/assignment/uploadAssignment?content_id={iid}&course_id={cid}')
    soup=BeautifulSoup(page.text,'html.parser')
    form=soup.find('form',action=re.compile(r'^/webapps/assignment/uploadAssignment\?action=submit$'))
    return soup,form


def review_files(soup,prefix=''):
    from urllib.parse import quote,parse_qs
    result=[]
    for link in soup.select('#currentAttempt_submissionList a.dwnldBtn[href]'):
        try:path=resource_path(link['href'])
        except ValueError:continue
        anchor=link.parent.select_one('a.attachment')
        name=anchor.get_text(strip=True) if anchor else parse_qs(urlparse(path).query).get('fileName',['附件'])[0]
        result.append({'name':name,'url':prefix+'/api/blackboard-resource?path='+quote(path,safe='')})
    return result


def detail(bb,cid,iid,prefix=''):
    from urllib.parse import quote
    course,col,content,attempts=assignment_record(bb,cid,iid)
    warnings=[];atts=[];history=[];page_text='';can_submit=False;reviews=[];review_html=''
    kind=content.get('contentHandler',{}).get('id')
    if kind=='resource/x-bb-assignment':
        try:
            atts=bb.results(f'/learn/api/public/v1/courses/{cid}/contents/{iid}/attachments')
        except Exception: warnings.append('附件清单暂未成功读取，请核对原页面')
        try:
            soup,form=current_form(bb,cid,iid)
            reviews=review_files(soup,prefix)
            panel=soup.select_one('#gradingPanel')
            if panel:review_html=safe_html(str(panel),prefix)
            area=soup.select_one('#contentPanel') or soup.select_one('#content')
            if area:
                for el in area.select('script,style,form input[type=hidden]'):el.decompose()
                page_text=area.get_text('\n',strip=True)
            can_submit=form is not None and submission_status(attempts) in {'not_submitted','draft'}
        except Exception: warnings.append('官方提交表单暂未读取成功，文件提交暂不可用')
    else:
        warnings.append('此条目为测验或其他任务，须在 Blackboard 原页面完成答题')
    for attempt in attempts:
        record=dict(attempt)
        aid=attempt['id']
        root=f'/learn/api/public/v1/courses/{cid}/gradebook/columns/{col["id"]}/attempts/{aid}'
        try: record.update(bb.json(root))
        except Exception: warnings.append('部分提交明细或反馈暂不可读取')
        try: record['attachments']=bb.results(root+'/files')
        except Exception: record['attachments']=[]
        for field in ('text','feedback','studentComments','instructorNotes'):
            if isinstance(record.get(field),str):record[field]=safe_html(record[field],prefix)
        history.append(record)
    if history and reviews:history[-1]['review_files']=reviews
    assessment={}
    assessment_id=content.get('contentHandler',{}).get('assessmentId')
    if assessment_id:
        try: assessment=bb.json(f'/learn/api/public/v1/courses/{cid}/assessments/{assessment_id}')
        except Exception: warnings.append('该测验的更多信息受 Blackboard 权限限制')
    return {'course':course['name'],'course_id':cid,'content_id':iid,'column_id':col['id'],
            'name':col.get('name'), 'status':submission_status(attempts),'due':col.get('grading',{}).get('due'),
            'description_html':safe_html(content.get('body'),prefix),'content':content,'column':col,
            'attempts':history,'assessment':assessment,'official_text':page_text[:30000],
            'review_html':review_html,
            'attachments':[dict(a,key=attachment_key(cid,iid,a['id']),download_url=prefix+'/api/blackboard-resource?path='+quote(f'/learn/api/public/v1/courses/{cid}/contents/{iid}/attachments/{a["id"]}/download',safe='')) for a in atts],
            'can_submit':can_submit and not content.get('contentHandler',{}).get('groupContent'),
            'official_url':BB_BASE+f'/webapps/blackboard/execute/displayIndividualContent?course_id={cid}&content_id={iid}',
            'warnings':warnings}


def submit(bb,cid,iid,files,text='',comments=''):
    _,col,content,before=assignment_record(bb,cid,iid)
    if submission_status(before) not in {'not_submitted','draft'}:raise ValueError('该作业已提交或状态不能确认；请先核对原页面')
    handler=content.get('contentHandler',{})
    if handler.get('id')!='resource/x-bb-assignment' or handler.get('groupContent'):
        raise ValueError('测验或小组作业请在 Blackboard 原页面提交')
    if not files and not text.strip():raise ValueError('请添加文件或填写作业正文')
    names=[f.filename for f in files]
    if len(names)!=len(set(names)) or len(names)>20:raise ValueError('文件名不能重复，每次最多添加 20 个文件')
    if any(not n or re.search(r'[\\/:?*"<>|]',n) for n in names):raise ValueError('请修改文件名中的无效字符')
    soup,form=current_form(bb,cid,iid)
    if form is None:raise ValueError('Blackboard 当前不允许提交此作业')
    if form.select('input[name=newFile_fileId]') or form.select('input[name=newFile_attachmentType]'):
        raise ValueError('Blackboard 中已有草稿附件，请在原页面继续，避免覆盖草稿')
    data=[(x['name'],x.get('value','')) for x in form.select('input[type=hidden][name]') if x['name']!='dispatch']
    data.extend([('dispatch','submit'),('studentSubmission.text',html.escape(text).replace('\n','<br>')),
                 ('student_commentstext',html.escape(comments).replace('\n','<br>')),('baseElementName','newFile')])
    uploaded=[]
    for index,f in enumerate(files):
        data.extend([('newFile_attachmentType','L'),('newFile_fileId','new'),('newFile_artifactFileId','undefined'),
                     ('newFile_artifactType','undefined'),('newFile_artifactTypeResourceKey','undefined'),('newFile_linkTitle',f.filename)])
        f.stream.seek(0)
        uploaded.append((f'newFile_LocalFile{index}',(f.filename,f.stream,f.mimetype or 'application/octet-stream')))
    # POST is deliberately not retried. Its nonce is freshly read in this session.
    from .actions import mark_sent
    multipart=[(k,(None,v)) for k,v in data]+uploaded
    mark_sent()
    response=bb.session.post(_api_path(form['action']),files=multipart,
                headers={'Referer':BB_BASE+f'/webapps/assignment/uploadAssignment?content_id={iid}&course_id={cid}',
                         'Origin':BB_BASE,'X-Requested-With':'XMLHttpRequest'},timeout=(15,240))
    body={}
    try:body=response.json()
    except Exception:pass
    try:
        after=bb.results(f'/learn/api/public/v1/courses/{cid}/gradebook/columns/{col["id"]}/attempts')
    except Exception:after=None
    if after is not None and submission_status(after)=='submitted':
        try:
            review,_=current_form(bb,cid,iid)
            confirmed_files=review_files(review)
            matched=all(n in {f['name'] for f in confirmed_files} for n in names)
        except Exception:matched=False
        if matched:
            return {'state':'confirmed','status':'submitted','attempts':after,'message':'Blackboard 已回读确认提交状态和所选附件'}
        return {'state':'needs_review','message':'Blackboard 有已提交记录，但附件尚未全部核对；请展开最新详情核对文件，暂不重复提交'}
    if body.get('errorMap'):
        # Treat explicit field validation as a rejection; omit URLs and auth internals.
        messages='；'.join(BeautifulSoup(str(v),'html.parser').get_text()[:200] for v in body['errorMap'].values())
        return {'state':'rejected','message':'Blackboard 未接受提交：'+messages}
    return {'state':'needs_review','status':'unknown','message':'已发送提交请求，但未取得已提交记录；请刷新详情核对，暂不重复提交'}
