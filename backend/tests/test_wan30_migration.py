"""Real fresh/fixed-owner and offline-claimed Wan schemas retain original conditions; changed owners/guards cannot grant input authority."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.core.workspace_boundary import claim_legacy_workspace
from app.models.wan30_reference_inputs import Wan30ReferenceInputSet,Wan30ReferenceInput
from tests.test_wan30_native import owned_native,accept

WORKSPACE='ws_'+'a'*32
CHILD='''
import asyncio,json,os
from pathlib import Path
from sqlalchemy import text
from app.config import settings
from app.core.db import init_db,close_db,engine
from app.core.workspace_boundary import prepare_workspace_boundary,assert_workspace_boundary
from app.models.wan30_reference_inputs import Wan30ReferenceInputSet,Wan30ReferenceInput
from app.services.worker.wan30_reference_schema import migrate_wan30_reference_inputs,require_wan30_input_schema
async def main():
    database=Path(os.environ['OWNED_DATABASE']);mode=os.environ['OWNED_MODE']
    if mode=='foreign-runtime':
        async with engine.connect() as db:
            try:await require_wan30_input_schema(db)
            except RuntimeError:print(json.dumps({'refused':True}));return
        raise AssertionError('Foreign runtime acquired schema authority')
    await init_db();await close_db()
    prepare_workspace_boundary(database,settings.workspace_id,allow_initialize=mode!='claimed')
    if mode=='existing-fixed-upgrade':
        async with engine.begin() as db:
            for name in ('wan30_reference_inputs','wan30_reference_input_sets'):
                assert await db.scalar(text('SELECT count(*) FROM '+name))==0
                await db.execute(text('DROP TABLE '+name))
        await close_db();assert_workspace_boundary(database,settings.workspace_id)
    await init_db();await close_db()
    prepare_workspace_boundary(database,settings.workspace_id)
    assert_workspace_boundary(database,settings.workspace_id)
    async with engine.connect() as db:await require_wan30_input_schema(db)
    if mode in {'missing-guard','redirected-guard'}:
        async with engine.begin() as db:
            await db.execute(text('DROP TRIGGER workspace_owner_wan30_reference_inputs_update'))
            if mode=='redirected-guard':await db.execute(text('CREATE TRIGGER workspace_owner_wan30_reference_inputs_update BEFORE UPDATE ON wan30_reference_inputs BEGIN SELECT 1; END'))
        async with engine.connect() as db:
            try:await require_wan30_input_schema(db)
            except RuntimeError:pass
            else:raise AssertionError('Changed owner guard acquired schema authority')
        try:await migrate_wan30_reference_inputs(engine)
        except RuntimeError:pass
        else:raise AssertionError('Migration silently repaired or ignored a changed owner')
    await close_db();print(json.dumps({'passed':True,'mode':mode,'network_started':False}))
async def owned_main():
    """Dispose the physical database pool on both positive and refused child paths."""
    try:await main()
    finally:await close_db()
asyncio.run(owned_main())
'''


def bound_process(database,mode,*,workspace=WORKSPACE):
    """Use only a new process with fixed authored identity and empty private configuration, so Base maps its actual workspace column."""
    names={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','LANG','LC_ALL','TZ'}
    env={key:value for key,value in os.environ.items() if key.upper() in names}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',JELLYFISH_SKIP_ENV_FILE='1',DEBUG='false',
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),DATABASE_URL='sqlite+aiosqlite:///'+database.as_posix(),
        WORKSPACE_ID=workspace,WORKSPACE_RUNTIME_LOCKED='true',MODEL_PRICE_CONFIG='',VIDEO_RUNTIME_CONFIG='',
        OWNED_DATABASE=str(database),OWNED_MODE=mode,LOCAL_STORAGE_ROOT=str(database.parent/'private-files'),
        PRODUCTION_MEDIA_ROOT=str(database.parent/'private-media'))
    result=subprocess.run([sys.executable,'-c',CHILD],env=env,capture_output=True,text=True,encoding='utf8',timeout=120)
    assert result.returncode==0,result.stderr[-5000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('mode',['fresh','existing-fixed-upgrade','missing-guard','redirected-guard'])
def test_actual_fixed_workspace_start_restart_and_owner_guard_refusal(tmp_path,mode):
    """A newly created ORM-bound schema restarts; deleting or redirecting its owner guard never becomes a legal ALTER variant."""
    assert bound_process(tmp_path/'fresh.sqlite3',mode)['passed']


@pytest.mark.asyncio
async def test_actual_offline_claim_preserves_sealed_wan_history_and_sole_project_hold(tmp_path,monkeypatch):
    """Real legacy ALTER/owner binding and a new process upgrade retain one preexisting two-frame intent and its original budget reservation."""
    async with owned_native(tmp_path,monkeypatch,reference=True) as fixture:
        await accept(fixture);await fixture.engine.dispose();database=tmp_path/'charges.db'
        columns={model.__tablename__:','.join(column.name for column in model.__table__.columns) for model in (Wan30ReferenceInputSet,Wan30ReferenceInput)}
        def snapshot():
            """Compare original input and budget fields without including the intentionally additive fixed-owner column."""
            with sqlite3.connect(database) as db:
                return {**{name:db.execute('SELECT '+fields+' FROM '+name+' ORDER BY job_id'+(',position' if name.endswith('_inputs') else '')).fetchall() for name,fields in columns.items()},
                    'budget':db.execute("SELECT limit_minor,reserved_minor,spent_minor FROM execution_budgets WHERE project_id='a'").fetchone()}
        before=snapshot()
        claim_legacy_workspace(database,WORKSPACE,expected_sha256=hashlib.sha256(database.read_bytes()).hexdigest())
        assert bound_process(database,'claimed')['passed']
        assert snapshot()==before and before['budget']==(100,25,0)
        assert bound_process(database,'foreign-runtime',workspace='ws_'+'b'*32)['refused']
