import importlib
from pathlib import Path
import sys

import pytest


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'deploy'))
    return importlib.import_module('hosted_routes')


def test_retired_owner_has_no_proxy_or_session_authority(operator):
    old=(Path(__file__).resolve().parents[1]/'deploy/nginx-campus-browser.conf').read_text()
    retired=operator.owner_route(old,False)
    assert 'proxy_pass' not in retired and 'auth_request' not in retired
    assert 'return 410' in retired and '/app/?login=1' in retired
    assert 'client_max_body_size 256m' in operator.owner_route(old,True)


def test_reset_recovery_path_cannot_move_data_to_live_or_existing_directory(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'deploy'))
    module=importlib.import_module('retire_trial')
    with pytest.raises(ValueError):module.recovery_path(tmp_path/'unapproved')
    with pytest.raises(ValueError):module.recovery_path('/var/lib/campus-reset-recovery')


def test_archive_refuses_symlink_without_moving_source(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'deploy'))
    module=importlib.import_module('retire_trial')
    source=tmp_path/'source';source.mkdir()
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda path: True if path==source else original(path))
    with pytest.raises(ValueError):module.archive(source,tmp_path,'destination')
    assert source.is_dir() and not (tmp_path/'destination').exists()
