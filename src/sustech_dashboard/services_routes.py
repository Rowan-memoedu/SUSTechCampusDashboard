"""Private routes for the additional campus service panels."""
from functools import wraps
import secrets
import threading
import time

from flask import jsonify, render_template, request, Response

from . import study, printing, classrooms
from .actions import Actions


def register_services(app, guard, csrf, public_error):
    previews={}
    preview_lock=threading.Lock()

    def api(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            try:
                guard(write=request.method=='POST')
                result=fn(*args,**kwargs)
                if isinstance(result,Response):return result
                return jsonify(result)
            except Exception as exc:
                return jsonify({'error':public_error(exc)}),400
        return wrapped

    for path,template,title in [('grades','grades.html','成绩与学分'),('printing','printing.html','校园打印'),('selection','selection.html','选课辅助'),('venues/classroom/free','classrooms.html','空闲教室')]:
        def page(template=template,title=title):
            guard()
            return render_template(template,csrf_token=csrf,api_base=request.script_root,page_title=title)
        app.add_url_rule('/'+path,endpoint='service_'+path.replace('/','_'),view_func=page)

    @app.get('/api/grades')
    @api
    def grades():
        with study.lock:return study.grades()

    @app.get('/api/grades/export')
    @api
    def grades_export():
        with study.lock:data=study.grades()
        rows=data['rows']
        if request.args.get('semester'):rows=[r for r in rows if r['semester']==request.args['semester']]
        return Response(study.grade_csv(rows),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="sustech-grades.csv"','Cache-Control':'no-store'})

    @app.get('/api/selection/info')
    @api
    def selection_info():
        with study.lock:return study.selection_info(request.args.get('xn'),request.args.get('xq'))

    @app.get('/api/selection/courses')
    @api
    def selection_courses():
        with study.lock:return study.search_courses(request.args)

    @app.post('/api/selection/solve')
    @api
    def selection_solve():
        with study.lock:return study.plan(request.get_json() or {})

    @app.post('/api/selection/preview')
    @api
    def selection_preview():
        with study.lock:prepared=study.prepare_selection(request.get_json() or {})
        key=secrets.token_urlsafe(24)
        with preview_lock:
            for k in list(previews):
                if time.time()-previews[k][0]>300:previews.pop(k)
            previews[key]=(time.time(),prepared)
        return {'review_id':key,'summary':prepared['summary'],'expires_in':300}

    @app.post('/api/selection/ical')
    @api
    def selection_ical():
        from .course_export import export
        with study.lock:result=export(request.get_json() or {})
        headers={'Content-Disposition':'attachment; filename="sustech-courses.ics"','Cache-Control':'no-store'}
        if result['through']:headers['X-Campus-Calendar-Through']=result['through']
        return Response(result['text'],mimetype='text/calendar',headers=headers)

    @app.post('/api/selection/commit')
    @api
    def selection_commit():
        body=request.get_json() or {}
        if body.get('confirmed') is not True:raise ValueError('请先确认操作预览')
        with preview_lock:preview=previews.get(body.get('review_id',''))
        if not preview or time.time()-preview[0]>300:raise ValueError('预览已过期，请重新核对')
        request_data=preview[1]['request']
        def work():
            with study.lock:return study.commit_selection(preview[1])
        # Every enrollment change for one section shares the same uncertainty lock.
        return Actions().run(body.get('operation_id'),'selection:'+request_data['rwh'],work)

    @app.get('/api/printing')
    @api
    def print_overview():
        with printing.lock:return printing.overview()

    @app.get('/api/printing/history')
    @api
    def print_history():
        with printing.lock:return printing.history(request.args)

    @app.post('/api/printing/upload')
    @api
    def print_upload():
        if request.form.get('confirmed')!='true':raise ValueError('请先核对打印设置')
        file=request.files.get('file')
        if not file:raise ValueError('请选择要上传的文档')
        content=file.read(50*1024*1024+1)
        if len(content)>50*1024*1024:raise ValueError('单个打印文件不能超过 50 MB')
        opts=printing.options(request.form)
        def work():
            with printing.lock:return printing.upload(file.filename,content,opts)
        return Actions().run(request.form.get('operation_id'),printing.upload_target(file.filename,content,opts),work)

    @app.post('/api/printing/delete')
    @api
    def print_delete():
        body=request.get_json() or {}
        if body.get('confirmed') is not True:raise ValueError('请先确认删除的文档')
        kind=body.get('kind');job_id=int(body.get('job_id',0))
        def work():
            with printing.lock:return printing.delete(kind,job_id)
        return Actions().run(body.get('operation_id'),f'print:delete:{kind}:{job_id}',work)

    @app.get('/api/classrooms')
    @api
    def classroom_query():
        with classrooms.lock:return classrooms.query(request.args)

    @app.get('/api/classrooms/detail')
    @api
    def classroom_detail():
        with classrooms.lock:return classrooms.detail(request.args)
