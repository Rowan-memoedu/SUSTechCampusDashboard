import json
import re
from urllib.parse import urlencode

import pytest

from sustech_dashboard.desktop_handoff import Handoffs, digest
from sustech_dashboard.entry import create_app


def test_wait_channel_reveals_nothing_before_login_and_delivers_once(tmp_path):
    store = Handoffs(tmp_path/'handoffs.sqlite3')
    key = store.issue()
    assert key.encode() not in store.path.read_bytes()
    assert store.claim(key) == {'state': 'waiting'}
    assert store.publish(key, 'fixture-one-use-grant')
    assert not store.publish(key, 'overwrite')
    assert store.claim(key) == {'uri': 'fixture-one-use-grant'}
    assert store.claim(key) == {'state': 'expired'}
    with pytest.raises(ValueError): store.claim('guess')


def test_only_successful_login_can_authorize_its_own_channel_and_space(tmp_path):
    prefix = '/spaces/' + 'a'*24
    host = 'dashboard.test'
    config = tmp_path/'routes.json'
    config.write_text(json.dumps({'host': host, 'owner_name': '', 'routes': [
        {'prefix': prefix, 'port': 18900, 'username': 'member'}]}))
    def exchange(route, host, endpoint, fields, peer):
        return (303 if fields['password'] == 'fixture-password' else 401), []
    app = create_app(tmp_path/'state', config, exchange)
    import sqlite3
    with sqlite3.connect(tmp_path/'state/accounts.sqlite3') as db:
        db.execute('INSERT INTO accounts VALUES (?,?)', ('fixture-user', prefix))
    browser, machine = app.test_client(), app.test_client()
    base = 'https://'+host+'/app/'
    page = browser.get('/', base_url=base)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    key = re.search(r'name="desktop_key" value="([^"]+)"', page.text)[1]
    headers = {'Origin': 'https://'+host, 'X-CSRF-Token': csrf}
    uri = 'sustech-campus://connect#'+urlencode({'server':'https://'+host+prefix,'ticket':'t'*43})
    payload = {'key': key, 'uri': uri}
    assert browser.post('/desktop/authorize', base_url=base, headers=headers, json=payload).status_code == 403
    assert machine.post('/desktop/poll', base_url=base, headers={'X-Campus-Agent':'1'}, json={'key':key}).json == {'state':'waiting'}
    assert browser.post('/', base_url=base, headers=headers, data={'username':'fixture-user','password':'wrong','desktop_key':key}).status_code == 401
    assert browser.post('/desktop/authorize', base_url=base, headers=headers, json=payload).status_code == 403
    assert browser.post('/', base_url=base, headers=headers, data={'username':'fixture-user','password':'fixture-password','desktop_key':key}).status_code == 303
    assert browser.post('/desktop/authorize', base_url=base, headers=headers, json={**payload,'uri':uri.replace('a'*24,'b'*24)}).status_code == 400
    assert browser.post('/desktop/authorize', base_url=base, headers=headers, json=payload).status_code == 200
    assert machine.post('/desktop/poll', base_url=base, headers={'X-Campus-Agent':'1','Origin':'https://evil.test'}, json={'key':key}).status_code == 403
    assert machine.post('/desktop/poll', base_url=base, headers={'X-Campus-Agent':'1'}, json={'key':key}).json == {'uri':uri}
    assert machine.post('/desktop/poll', base_url=base, headers={'X-Campus-Agent':'1'}, json={'key':key}).json == {'state':'expired'}
