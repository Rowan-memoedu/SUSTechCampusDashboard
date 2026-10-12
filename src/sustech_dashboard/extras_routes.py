"""Private, on-demand routes sharing the existing authentication and action ledger."""
from functools import wraps
import secrets
import threading
import time

from flask import jsonify, render_template, request, abort

from . import academic_extras, campus_extras, browser_services, borrow
from .actions import Actions

PAGES = {'transit': '校巴与校园导航', 'nces': '牛蛙课程评价', 'cle': '语言中心指导',
         'programs': '国际交流项目', 'faculty': '教师与研究方向', 'library': '书目与文献检索',
         'mirror': '教学大纲与公开资料', 'content': '课程内容检索', 'evaluations': '正式评教', 'borrow': '教室借用申请'}


def register_extras(app, guard, csrf, public_error):
    previews = {}
    mutex = threading.RLock()

    def api(fn):
        @wraps(fn)
        def wrapped(*args, **kw):
            try:
                guard(write=request.method == 'POST')
                response = jsonify(fn(*args, **kw))
                response.headers['Cache-Control'] = 'private, no-store'
                return response
            except Exception as exc:
                return jsonify(error=public_error(exc)), 400
        return wrapped

    @app.get('/explore')
    @app.get('/explore/<kind>')
    def extras_page(kind='transit'):
        guard()
        if kind not in PAGES:
            abort(404)
        return render_template('extras.html', page_title=PAGES[kind], kind=kind, pages=PAGES,
                               csrf_token=csrf, api_base=request.script_root)

    @app.get('/api/announcements/<cid>')
    @api
    def announcements(cid):
        from .app import Blackboard, _bb_lock
        with _bb_lock:
            return academic_extras.announcements(Blackboard(), cid, request.script_root)

    @app.get('/api/blackboard/grades')
    @api
    def extras_bb_grades():
        from .app import Blackboard, _bb_lock
        with _bb_lock:
            return academic_extras.gradebook(Blackboard(), request.script_root)

    @app.get('/api/extras/<kind>')
    @api
    def extras_query(kind):
        if kind == 'content':
            from .app import Blackboard, _bb_lock
            with _bb_lock:
                return academic_extras.content_search(Blackboard(), request.args, request.script_root)
        functions = {'transit': campus_extras.transit, 'nces': campus_extras.nces,
                     'programs': campus_extras.programs, 'faculty': campus_extras.faculty,
                     'mirror': campus_extras.mirror, 'library': browser_services.library,
                     'cle': browser_services.cle, 'evaluations': browser_services.evaluations}
        if kind not in functions:
            raise ValueError('服务不存在')
        # Playwright sync objects always start, execute and close on the same thread.
        with browser_services.lock:
            return functions[kind](request.args)

    @app.post('/api/extras/<kind>/preview')
    @api
    def extras_preview(kind):
        methods = {'cle': browser_services.prepare_cle, 'evaluations': browser_services.prepare_evaluation,
                   'borrow': borrow.prepare}
        if kind not in methods:
            raise ValueError('该服务没有写入操作')
        body = request.get_json() or {}
        from .classrooms import lock as classroom_lock
        with classroom_lock, browser_services.lock:
            prepared = methods[kind](body)
        if Actions().pending(prepared['target']):
            raise ValueError('上次操作结果尚未确认，请先在学校系统核对，暂不重复提交')
        key = secrets.token_urlsafe(24)
        with mutex:
            for token in list(previews):
                if time.monotonic() - previews[token][0] > 300:
                    previews.pop(token)
            if len(previews) >= 30:
                raise ValueError('待确认操作过多，请稍后重试')
            previews[key] = (time.monotonic(), kind, prepared, None)
        return {'review_id': key, 'summary': prepared['summary'], 'expires_in': 300}

    @app.post('/api/extras/<kind>/commit')
    @api
    def extras_commit(kind):
        methods = {'cle': browser_services.commit_cle, 'evaluations': browser_services.commit_evaluation,
                   'borrow': borrow.commit}
        body = request.get_json() or {}
        if kind not in methods or body.get('confirmed') is not True:
            raise ValueError('请先核对并确认操作预览')
        with mutex:
            preview = previews.get(body.get('review_id'))
            if not preview or preview[1] != kind or time.monotonic() - preview[0] > 300:
                raise ValueError('预览已过期，请重新核对')
            token = body.get('operation_id')
            if not isinstance(token, str) or len(token) != 36:
                raise ValueError('操作标识无效，请重新预览')
            if preview[3] is not None and preview[3] != token:
                raise ValueError('本次预览已经发送，请核对原操作结果')
            previews[body['review_id']] = (*preview[:3], token)
        def work():
            from .classrooms import lock as classroom_lock
            with classroom_lock, browser_services.lock:
                return methods[kind](preview[2])
        return Actions().run(body.get('operation_id'), preview[2]['target'], work)
