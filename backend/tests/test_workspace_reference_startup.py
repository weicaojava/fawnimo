"""Actual child entry rejects malformed private reference policies before creating identity/workspace storage."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.contracts.workspace_reference_delivery import WorkspaceReferencePolicy


@pytest.mark.parametrize('invalid',['scope','key','origin','fields'])
def test_actual_worker_entry_rejects_foreign_or_malformed_delivery_before_root_creation(tmp_path,invalid):
    """The real stdin launcher neither opens data nor echoes private key material when startup scope is invalid."""
    workspace='ws_'+'a'*32
    policy=WorkspaceReferencePolicy(workspace,'https://studio.example.com',bytes(range(32))).private_message()
    if invalid=='scope':policy['workspace_id']='ws_'+'b'*32
    if invalid=='key':policy['signing_key']=True
    if invalid=='origin':policy['origin']='http://127.0.0.1:19001'
    if invalid=='fields':policy['root']='untrusted-private-root'
    root=tmp_path/'not-created';backend=Path(__file__).resolve().parents[1]
    gate={'root':str(root.resolve()),'workspace_id':workspace,'port':19001,'internal_token':'authored-private-worker-token-'*3,'reference_delivery':policy}
    result=subprocess.run([sys.executable,str(backend/'run_tenant_worker.py')],input=json.dumps(gate),cwd=backend,
        env={**os.environ,'JELLYFISH_SKIP_ENV_FILE':'1','PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf8',timeout=20)
    assert result.returncode!=0 and not root.exists()
    assert bytes(range(32)).hex() not in result.stdout+result.stderr
