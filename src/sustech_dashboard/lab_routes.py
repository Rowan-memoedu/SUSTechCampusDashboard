"""Lab status and controls inherit the dashboard's existing local access guards."""
from pathlib import Path
from flask import jsonify, request


def public_job(job):
    data = job['data']
    return {k: job[k] for k in ('id', 'name', 'state', 'stage', 'revision', 'error', 'updated')} | {
        'week': data['assignment']['week'], 'written': data.get('written', False),
        'connected': bool(data.get('idea_receipt', {}).get('passed')),
        'preserved_count': len(data.get('installation', {}).get('preserved_files', [])),
        'next_retry_at': data.get('recovery', {}).get('next_retry_at')}


def register_labs(app, engine, guard, public_error):
    @app.get('/api/labs')
    def lab_status():
        guard()
        config = engine.configuration()
        ready = all(config.get(k) for k in ('course_id', 'project_root', 'idea_executable', 'java_home', 'node', 'oj_config'))
        return jsonify(enabled=config.get('enabled', False), configured=ready, min_week=config.get('min_week', 9),
                       discovery_error=engine.store.setting('discovery_error', ''),
                       jobs=[public_job(j) for j in engine.store.list()])

    @app.post('/api/labs/settings')
    def lab_settings():
        try:
            guard(write=True)
            payload = request.get_json() or {}
            if type(payload.get('enabled')) is not bool:
                raise ValueError('启用状态无效')
            config = engine.configuration()
            if payload['enabled'] and not all(config.get(k) for k in ('course_id', 'project_root', 'idea_executable', 'java_home', 'node', 'oj_config')):
                raise ValueError('尚未配置 CS109 课程、学习工程和本机 IDEA 工具')
            config['enabled'] = payload['enabled']
            engine.store.set_setting('configuration', config)
            engine.next_scan = 0
            engine.shared.wake.set()
            return jsonify(ok=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post('/api/labs/<jid>/retry')
    def lab_retry(jid):
        try:
            guard(write=True)
            payload = request.get_json() or {}
            job = engine.store.decide(jid, payload.get('revision'), 'retry')
            engine.shared.wake.set()
            return jsonify(public_job(job))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            return jsonify(error=public_error(exc)), 503
