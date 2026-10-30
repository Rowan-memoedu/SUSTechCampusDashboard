"""Homework UI endpoints inherit the dashboard's account, Host, Origin and CSRF guards."""
from pathlib import Path
import json
import re

from flask import jsonify, request, send_file

from .homework_backend import confined
from .homework_sources import file_sha, text
from .homework_store import digest


def public_job(job):
    data = job['data']
    return {k: job[k] for k in ('id', 'course_id', 'name', 'state', 'stage', 'revision', 'error', 'updated')} | {
        'course': data['assignment']['course'], 'content_id': data['assignment']['content_id'],
        'phase': data.get('active_phase', job['stage']),
        'recovery': {k: data.get('recovery', {}).get(k) for k in ('attempts', 'next_retry_at', 'phase')},
        'plan': data.get('plan'), 'requirement': data.get('input', {}).get('requirement', ''),
        'artifacts': [{k: x[k] for k in ('module', 'name', 'pages')} for x in data.get('artifacts', [])],
        'homework_copies': data.get('homework_copies', data.get('plan', {}).get('subject') in {'大学物理（上）', '线性代数'})}


def register_homework(app, engine, guard, public_error):
    @app.get('/api/homework')
    def homework_status():
        guard()
        settings = engine.settings()
        return jsonify(enabled=engine.store.setting('enabled', False),
                       baseline_at=engine.store.setting('baseline_at'), backend=settings.get('backend', 'codex'),
                       jobs=[public_job(j) for j in engine.store.list()])

    @app.post('/api/homework/settings')
    def homework_settings():
        try:
            guard(write=True)
            payload = request.get_json() or {}
            engine.enable(payload.get('enabled'))
            engine.wake.set()
            return jsonify(ok=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            return jsonify(error=public_error(exc)), 503

    @app.post('/api/homework/enqueue')
    def homework_enqueue():
        try:
            guard(write=True)
            payload = request.get_json() or {}
            match = next((x for x in engine.snapshot().get('assignments', [])
                          if x['course_id'] == payload.get('course_id') and x['content_id'] == payload.get('content_id')), None)
            if not match:
                raise ValueError('该作业不在当前已同步的课程范围内')
            job = engine.store.enqueue(match, explicit=True)
            engine.wake.set()
            return jsonify(public_job(job))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post('/api/homework/<jid>/decision')
    def homework_decision(jid):
        try:
            guard(write=True)
            payload = request.get_json() or {}
            job = engine.store.get(jid)
            action = payload.get('action')
            if action == 'accept_source':
                if job['state'] != 'awaiting_source' or job['revision'] != payload.get('revision'):
                    raise ValueError('处理状态已变化，请刷新后核对')
                if engine.source_digest(engine.workspace(job), job['data']) != job['data']['source_digest']:
                    raise ValueError('教材、题面或原图已变化，请重新提取后确认')
                # Teacher edits during the human review interval invalidate the old proposal.
                with engine.bb_lock:
                    content = engine.bb_factory().json('/learn/api/public/v1/courses/' + job['course_id'] +
                        '/contents/' + job['data']['assignment']['content_id'])
                if digest(text(content.get('body'))) != job['data']['requirement_digest']:
                    raise ValueError('老师已修改作业要求，请选择“修正后重新识别”读取最新内容')
                copies = payload.get('homework_copies', False)
                if type(copies) is not bool:
                    raise ValueError('本地双册副本设置无效')
            if action == 'accept_pdf':
                for artifact in job['data'].get('artifacts', []):
                    if file_sha(Path(artifact['path'])) != artifact['sha256']:
                        raise ValueError('核对中的成品已变化，请重新生成并核对；现有文件会保留')
            job = engine.store.decide(jid, payload.get('revision'), action, payload.get('feedback', ''), payload.get('homework_copies', False))
            engine.wake.set()
            return jsonify(public_job(job))
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc) if isinstance(exc, ValueError) else '文件缺失或不可读，未确认成功'), 400
        except Exception as exc:
            return jsonify(error=public_error(exc)), 503

    @app.get('/api/homework/<jid>/file/<kind>')
    def homework_file(jid, kind):
        guard()
        try:
            job = engine.store.get(jid)
            if kind == 'english':
                path = confined(engine.workspace(job), 'work/答案册-英文.md')
                mime = 'text/markdown; charset=utf-8'
            else:
                artifact = next((x for x in job['data'].get('artifacts', []) if x['module'] == kind), None)
                if artifact is None:
                    raise ValueError('此文件尚未通过审核')
                path = Path(artifact['path']).resolve()
                if not path.is_relative_to(engine.output_root().resolve()):
                    raise ValueError('文件位置无效')
                mime = 'application/pdf'
            return send_file(path, mimetype=mime, as_attachment=request.args.get('download') == '1', download_name=path.name)
        except (ValueError, OSError):
            return jsonify(error='文件尚未就绪或已不存在'), 404
