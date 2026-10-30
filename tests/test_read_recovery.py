import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import requests


@pytest.mark.parametrize('persistent', [False, True])
def test_slow_assignment_read_retries_once_and_isolates_failure(persistent):
    from sustech_dashboard.provider import Blackboard
    bb = Blackboard.__new__(Blackboard)
    bb.in_current_term = lambda _: True
    bb.results = lambda path: [{'id': x, 'contentId': x, 'grading': {'type': 'Attempts', 'due': 'current'}}
                               for x in ('slow', 'healthy')]
    calls = []
    def read(path):
        slow = path.endswith('/slow')
        budget = getattr(bb, '_read_timeout', 20)
        calls.append((slow, budget))
        if slow and (persistent or budget == 20):
            raise requests.ConnectionError('private request data')
        return {'created': 'current'}
    bb.json = read
    rows = bb.current_assignment_columns('course')
    assert calls == [(True, 20), (True, 60), (False, 20)]
    assert len(rows) == (1 if persistent else 2)
    assert bb._assignment_content_failures['course'] == (['slow'] if persistent else [])
    assert bb._read_timeout == 20


def test_explicit_content_access_denial_is_not_retried_or_cached():
    from sustech_dashboard.provider import Blackboard
    bb = Blackboard.__new__(Blackboard)
    bb.in_current_term = lambda _: True
    bb.results = lambda path: [{'id':'x','contentId':'x','grading':{'type':'Attempts','due':'current'}}]
    calls = []
    def read(path):
        calls.append(path)
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError(response=response)
    bb.json = read
    assert bb.current_assignment_columns('course') == []
    assert len(calls) == 1 and bb._assignment_restrictions['course'] == 1
    assert bb._assignment_content_failures['course'] == []


def test_blackboard_expiry_refreshes_read_once_and_uses_independent_sessions(monkeypatch):
    from sustech_dashboard.provider import Blackboard
    import sustech_survival.sso as sso
    class Auth:
        _session_time = 1
        refreshes = 0
        def ensure(self): return True, ''
        def refresh(self):
            self.refreshes += 1
            self._session_time += 1
            return True
        @property
        def session(self):
            generation = self._session_time
            session = requests.Session()
            def get(url, **kwargs):
                response = requests.Response()
                response.status_code = 401 if generation == 1 else 200
                response.url = url
                response._content = b'{}'
                response._content_consumed = True
                return response
            session.get = get
            return session
    auth = Auth()
    monkeypatch.setattr(sso, 'BBAuth', lambda: auth)
    first, second = Blackboard(), Blackboard()
    assert first.session is not second.session
    assert first.get('/learn/api/public/v1/users/me').status_code == 200
    assert second.get('/learn/api/public/v1/users/me').status_code == 200
    assert auth.refreshes == 1
    retries = first.session.get_adapter('https://bb.sustech.edu.cn/').max_retries
    assert retries.allowed_methods == {'GET'} and retries.read == 0


def test_academic_results_are_published_before_slow_independent_sources(monkeypatch, tmp_path):
    from sustech_dashboard import app, quotas
    import sustech_survival
    monkeypatch.setattr(app, 'SNAPSHOT_PATH', tmp_path / 'snapshot.json')
    monkeypatch.setattr(quotas, 'check_private_quota', lambda *args: None)
    monkeypatch.setattr(app, 'Blackboard', lambda: SimpleNamespace(
        current_courses=lambda: [], assignments=lambda **kw: [{'id': 'new-work'}], warnings=[]))
    def tis():
        published = app.load_json(app.SNAPSHOT_PATH, {})
        assert published['assignments'] == [{'id': 'new-work'}]
        assert published['source_updated_at']['blackboard']
        return {'week': 9}
    def bookings():
        assert app.load_json(app.SNAPSHOT_PATH, {})['tis'] == {'week': 9}
        return {}
    monkeypatch.setattr(app, 'read_tis', tis)
    monkeypatch.setattr(app, 'read_bookings', bookings)
    monkeypatch.setattr(app, 'scan_materials', lambda: {})
    monkeypatch.setattr(sustech_survival, 'Context', lambda **kw: SimpleNamespace(weather_cond='', temperature=None, aqi_value=None))
    assert app.sync_all()['errors'] == {}


@pytest.mark.skipif(shutil.which('node') is None, reason='JavaScript runtime required')
def test_stalled_browser_read_aborts_but_submission_is_not_cancelled_or_replayed():
    source = Path(__file__).parents[1] / 'src/sustech_dashboard/static/common.js'
    script = r'''
    const vm=require('vm'),fs=require('fs'),assert=require('assert');
    let expire,timers=0,calls=0;
    const scope={apiBase:'',location:{hash:''},AbortController,AbortSignal,
      setTimeout:fn=>{expire=fn;timers++;return 1;},clearTimeout:()=>{},
      fetch:(_url,init)=>{calls++;return new Promise((resolve,reject)=>{
        if(init.signal)init.signal.addEventListener('abort',()=>reject(Error('aborted')));
      });}};
    vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
    (async()=>{
      const pending=vm.runInContext("campusFetch('/read')",scope);expire();
      await assert.rejects(pending,/读取超时/);
      vm.runInContext("campusFetch('/submit',{method:'POST'})",scope);
      assert.equal(timers,1);assert.equal(calls,2);
      console.log(JSON.stringify({read_aborted:true,post_replayed:false}));
    })().catch(e=>{console.error(e);process.exitCode=1;});
    '''
    result = subprocess.run(['node', '-e', script, str(source)], capture_output=True,
                            text=True, encoding='utf-8', check=True, timeout=20)
    assert json.loads(result.stdout)['read_aborted']
