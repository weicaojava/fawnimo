"""A fixed worker selects only its own operator price file, never inherited host pricing or another space."""
from contextlib import closing
import json
import os
import sqlite3

import pytest

from app.core.workspace_boundary import prepare_workspace_boundary
from run_tenant_worker import configure_worker

SPACE_A='ws_'+'a'*32
SPACE_B='ws_'+'b'*32


def marked(root,space,*,imported=False):
    """Create a new authored empty database and fixed marker, with no production account data."""
    directory=root/space;directory.mkdir(parents=True)
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        if imported:
            db.execute('CREATE TABLE workspace_import(id INTEGER PRIMARY KEY,runtime_locked INTEGER)')
        db.commit()
    prepare_workspace_boundary(directory/'studio.db',space,allow_initialize=True)
    if imported:
        with closing(sqlite3.connect(directory/'studio.db')) as db:
            db.execute('INSERT INTO workspace_import(id,runtime_locked) VALUES(1,1)');db.commit()
    return directory


def startup(root,space):
    """Select a private loopback worker configuration without opening any server or app lifespan."""
    return {'root':str(root.resolve()),'workspace_id':space,'port':19452,'internal_token':'synthetic-internal-token-'+'x'*32}


@pytest.mark.parametrize('space',[SPACE_A,SPACE_B])
def test_marked_workspace_gets_only_its_fixed_private_price_file(tmp_path,monkeypatch,space):
    """The fixed startup file cannot be supplied by HTTP/startup fields or inherited environment values."""
    for identity in (SPACE_A,SPACE_B):
        directory=marked(tmp_path,identity)
        private=directory/'private';private.mkdir()
        (private/'model-prices.json').write_text(json.dumps({'schema_version':1,'enabled':False,'rules':[]}),encoding='utf-8')
    monkeypatch.setattr(os,'environ',{**os.environ,'MODEL_PRICE_CONFIG':'untrusted-inherited-pricing','VIDEO_RUNTIME_CONFIG':'untrusted-inherited-native','FUTURE_MODEL_KEY':'inert-fixture-key'})
    database,workspace,port=configure_worker(startup(tmp_path,space))
    assert workspace==space and database.parent==tmp_path/space
    assert os.environ['MODEL_PRICE_CONFIG']==str(tmp_path/space/'private'/'model-prices.json')
    assert os.environ['VIDEO_RUNTIME_CONFIG']==str(tmp_path/space/'private'/'native-video.json')
    assert 'FUTURE_MODEL_KEY' not in os.environ and os.environ['WORKSPACE_RUNTIME_LOCKED']=='false'
    with pytest.raises(ValueError):
        configure_worker({**startup(tmp_path,space),'model_price_config':'another.json'})


def test_imported_execution_lock_does_not_load_even_a_present_private_price_file(tmp_path,monkeypatch):
    """Operator pricing cannot implicitly unlock old imported queues or host credentials."""
    directory=marked(tmp_path,SPACE_A,imported=True)
    (directory/'private').mkdir();(directory/'private'/'model-prices.json').write_text('{}',encoding='utf-8')
    monkeypatch.setattr(os,'environ',{**os.environ,'MODEL_PRICE_CONFIG':'inherited'})
    configure_worker(startup(tmp_path,SPACE_A))
    assert os.environ['WORKSPACE_RUNTIME_LOCKED']=='true' and os.environ['MODEL_PRICE_CONFIG']==''
    assert 'VIDEO_RUNTIME_CONFIG' not in os.environ


def test_redirected_private_pricing_directory_is_rejected_before_environment_or_file_use(tmp_path,monkeypatch):
    """Static directory links cannot turn a per-space price policy into another root's file."""
    root=tmp_path/'root'
    directory=marked(root,SPACE_A)
    outside=tmp_path/'outside';outside.mkdir()
    try:
        (directory/'private').symlink_to(outside,target_is_directory=True)
    except OSError:
        pytest.skip('Creating a directory symlink requires platform permission')
    monkeypatch.setattr(os,'environ',{**os.environ,'MODEL_PRICE_CONFIG':'preserved-before-rejection'})
    with pytest.raises(ValueError,match='pricing cannot redirect'):
        configure_worker(startup(root,SPACE_A))
    assert os.environ['MODEL_PRICE_CONFIG']=='preserved-before-rejection' and not list(outside.iterdir())
