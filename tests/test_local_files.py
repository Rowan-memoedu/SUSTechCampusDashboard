import json
from pathlib import Path

import pytest

from sustech_dashboard import local_files


def test_receipt_resolves_only_existing_document_and_parent(monkeypatch, tmp_path):
    root = tmp_path/'downloads'; folder = root/'course'; folder.mkdir(parents=True)
    file = folder/'fixture.pdf'; file.write_bytes(b'%PDF-fixture')
    monkeypatch.setattr(local_files, 'DOWNLOAD_ROOT', root)
    monkeypatch.setattr(local_files, 'DATA_ROOT', tmp_path)
    (tmp_path/'downloaded-files.json').write_text(json.dumps({'key':{'path':'course/fixture.pdf'}}))
    assert local_files.target('key','file') == file
    assert local_files.target('key','folder') == folder
    assert local_files.target(kind='root') == root
    file.unlink()
    with pytest.raises(ValueError, match='不存在'):local_files.target('key','file')


@pytest.mark.parametrize('relative', ['../private.pdf', '/private.pdf', 'course/run.exe'])
def test_open_cannot_escape_root_or_execute_program(monkeypatch, tmp_path, relative):
    root = tmp_path/'downloads'; (root/'course').mkdir(parents=True)
    (tmp_path/'private.pdf').write_bytes(b'private')
    (root/'course/run.exe').write_bytes(b'program')
    monkeypatch.setattr(local_files, 'DOWNLOAD_ROOT', root)
    monkeypatch.setattr(local_files, 'DATA_ROOT', tmp_path)
    (tmp_path/'downloaded-files.json').write_text(json.dumps({'key':{'path':relative}}))
    with pytest.raises(ValueError):local_files.target('key','file')


def test_file_launcher_requires_auth_and_csrf(monkeypatch, tmp_path):
    from sustech_dashboard import app, runtime, authentication
    monkeypatch.setattr(app,'CLOUD',False)
    monkeypatch.delenv('SUSTECH_CLOUD',raising=False)
    monkeypatch.delenv('SUSTECH_EXECUTION_MODE',raising=False)
    monkeypatch.setattr(runtime,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(authentication,'load_owner_credentials',lambda:False)
    rt=runtime.Runtime(); rt.configured.set()
    client=app.create_app(rt).test_client()
    headers={'Host':'127.0.0.1:18765','Origin':'http://127.0.0.1:18765'}
    calls=[]; monkeypatch.setattr(local_files,'open_target',calls.append)
    assert client.post('/api/materials/open',headers=headers,json={'kind':'root'}).status_code==401
    client.post('/auth/unlock',headers=headers,json={'token':rt.token})
    assert client.post('/api/materials/open',headers=headers,json={'kind':'root'}).status_code==400
    assert not calls
    import re
    page=client.get('/files',headers=headers)
    headers['X-CSRF-Token']=re.search(r'const roomCsrf="([^"]+)"',page.text).group(1)
    monkeypatch.setattr(local_files,'DOWNLOAD_ROOT',tmp_path/'downloads')
    assert client.post('/api/materials/open',headers=headers,json={'kind':'root'}).status_code==200
    assert calls==[tmp_path/'downloads']
