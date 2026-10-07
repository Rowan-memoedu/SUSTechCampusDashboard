import json
from types import SimpleNamespace

import pytest

from sustech_dashboard import pairing, download_agent, dpapi_store


@pytest.mark.parametrize('url',['http://dashboard.test/app/','https://u:p@dashboard.test/app/',
    'https://dashboard.test/app/#token','https://dashboard.test/other','https://dashboard.test:8080/app/'])
def test_connection_url_does_not_accept_passwords_or_insecure_targets(url):
    with pytest.raises(ValueError):pairing.server_url(url)


@pytest.mark.parametrize('outcome',['success','wrong_identity','external_redirect'])
def test_public_login_pairs_internal_account_without_cas_password(monkeypatch,tmp_path,outcome):
    monkeypatch.setattr(pairing,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(pairing,'DATA_ROOT',tmp_path)
    path=tmp_path/'cloud-access.dpapi.json'; monkeypatch.setattr(download_agent,'CONFIG',path)
    monkeypatch.setattr(dpapi_store,'load_credentials',lambda:('fixture-student','never-upload-cas-password'))
    monkeypatch.setattr(dpapi_store,'protect_password',lambda value:'encrypted-'+value)
    monkeypatch.setattr(dpapi_store,'_restrict_directory',lambda path:None)
    calls=[]
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            calls.append((url,kwargs)); assert kwargs['allow_redirects'] is False
            if '/api/' not in url:
                return SimpleNamespace(text='<input name="csrf" value="fixture-csrf">',raise_for_status=lambda:None)
            return SimpleNamespace(status_code=400 if outcome=='wrong_identity' else 200,
                json=lambda:{'username':'member','same_identity':True})
        def post(self,url,**kwargs):
            calls.append((url,kwargs));assert kwargs['allow_redirects'] is False
            return SimpleNamespace(status_code=303,headers={'Location':
                'https://evil.test/steal' if outcome=='external_redirect' else '/spaces/'+'f'*24+'/'})
    monkeypatch.setattr(pairing.requests,'Session',Session)
    if outcome=='success':
        assert pairing.connect('https://dashboard.test/app/','chosen-public-name','fixture-panel-password')['ok']
        value=json.loads(path.read_text());assert value['username']=='member'
        assert value['password_dpapi']=='encrypted-fixture-panel-password'
    else:
        with pytest.raises(ValueError):pairing.connect('https://dashboard.test/app/','chosen-public-name','fixture-panel-password')
        assert not path.exists()
    assert 'never-upload-cas-password' not in str(calls)
    assert all(call[0].startswith('https://dashboard.test/') for call in calls)
