import pytest
from cryptography.exceptions import InvalidTag
from sustech_dashboard.credential_transfer import X25519PrivateKey, public_key, seal, unseal


def test_bootstrap_ciphertext_bound_to_native_key_ticket_and_device():
    private = X25519PrivateKey.generate()
    credentials = ('fixture-student', 'fixture-password-中文')
    envelope = seal(public_key(private), credentials, 't'*43, 'd'*32)
    assert unseal(private, envelope, 't'*43, 'd'*32) == credentials
    for owner, ticket, device in [(X25519PrivateKey.generate(),'t'*43,'d'*32),
                                   (private,'u'*43,'d'*32), (private,'t'*43,'e'*32)]:
        with pytest.raises(InvalidTag):unseal(owner, envelope, ticket, device)
    with pytest.raises(ValueError):seal('bad-key', credentials, 't'*43, 'd'*32)


def test_same_owner_reconnect_does_not_overwrite_credentials_and_password_refresh_is_atomic(monkeypatch,tmp_path):
    from sustech_dashboard import dpapi_store
    path=tmp_path/'credentials.dpapi.json'
    path.write_text('{"sid":"fixture-student","password_dpapi":"old-ciphertext"}')
    original=path.read_bytes()
    monkeypatch.setattr(dpapi_store,'CREDENTIALS_PATH',path)
    monkeypatch.setattr(dpapi_store,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(dpapi_store,'load_credentials',lambda:('fixture-student','old-password'))
    monkeypatch.setattr(dpapi_store,'protect_password',lambda value:'new-ciphertext')
    monkeypatch.setattr(dpapi_store,'_restrict_directory',lambda path:None)
    dpapi_store.save_paired_credentials('fixture-student','old-password')
    assert path.read_bytes()==original
    with pytest.raises(ValueError):dpapi_store.save_paired_credentials('another-student','new-password')
    assert path.read_bytes()==original
    dpapi_store.save_paired_credentials('fixture-student','new-password')
    assert 'new-ciphertext' in path.read_text() and 'new-password' not in path.read_text()
