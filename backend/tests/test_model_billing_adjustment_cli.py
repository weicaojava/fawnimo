"""Run actual maintenance decisions in separate processes using only new authored identity/workspace data."""
import asyncio
from contextlib import closing
import json
import sqlite3

from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from app.controlplane.maintenance import ProcessLease
from tests.test_model_billing import admitted
from tests.test_model_billing_cli import command,counters,import_arguments,prepared_root


def saved_facts(directory):
    """Read explicit local receipt/line IDs without printing private authored invoice bytes or remote identifiers."""
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        return db.execute('SELECT id FROM model_billing_receipts').fetchone()[0],db.execute(
            'SELECT id FROM model_billing_lines ORDER BY rowid DESC LIMIT 1').fetchone()[0]


def test_actual_correction_cli_import_credit_replay_changed_intent_and_statement_recovery(tmp_path):
    """Actual process retries do not re-credit, change old evidence or raise an unrelated project's ceiling."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    initial=command(root,*import_arguments(original,normalized))
    assert initial.returncode==0,initial.stderr
    normalized.write_bytes(b'row_reference,provider_request_id,status,currency,amount_minor\ncredit,fixture-request-1,charged,CNY,5\n')
    imported=command(root,*import_arguments(original,normalized,key='explicit-corrected-invoice'))
    assert imported.returncode==0 and json.loads(imported.stdout)['conflict']==1
    receipt,line=saved_facts(directory)
    arguments=['--correct-line',line,'--reference-receipt',receipt,'--expected-correction-revision','0','--reason','Authored confirmed credit evidence']
    first=command(root,*arguments)
    assert first.returncode==0,first.stderr
    result=json.loads(first.stdout)
    assert result['status']=='correction_applied' and not result['replayed'] and result['external_model_calls']==0
    assert not result['provider_automatically_authenticated']
    again=command(root,*arguments)
    assert again.returncode==0 and json.loads(again.stdout)['correction_id']==result['correction_id'] and json.loads(again.stdout)['replayed']
    recovered=command(root,'--statement-id',json.loads(imported.stdout)['statement_id'])
    assert recovered.returncode==0 and json.loads(recovered.stdout)['correction_applied']==1
    changed=command(root,*arguments[:-1],'Changed credit evidence must conflict')
    assert changed.returncode==2
    assert counters(directory)==([('a',0,5,100),('b',0,0,100)],1)
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        assert db.execute('SELECT count(*) FROM model_billing_corrections').fetchone()[0]==1
        assert db.execute('SELECT actual_minor FROM model_billing_receipts').fetchone()[0]==12
    assert 'fixture-request-1' not in first.stdout+first.stderr and 'PRIVATE_AUTHORED' not in first.stdout+first.stderr


def test_actual_ambiguous_assignment_cli_keeps_the_other_project_hold(tmp_path):
    """A real separate process settles the explicitly selected project once, retaining the other original unknown call."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    async def add_second():
        """Add a second authored matching call through the actual existing ledger service."""
        engine=create_async_engine('sqlite+aiosqlite:///'+(directory/'studio.db').as_posix())
        try:
            return await admitted(async_sessionmaker(engine,expire_on_commit=False),project='b',scope='second-ambiguous')
        finally:
            await engine.dispose()
    chosen=asyncio.run(add_second())
    imported=command(root,*import_arguments(original,normalized))
    assert imported.returncode==0 and json.loads(imported.stdout)['ambiguous']==1
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        line=db.execute('SELECT id FROM model_billing_lines').fetchone()[0]
    arguments=['--assign-line',line,'--charge-id',chosen,'--reason','Authored explicit matching call evidence']
    first=command(root,*arguments)
    assert first.returncode==0,first.stderr
    result=json.loads(first.stdout)
    assert result['status']=='applied' and result['project_id']=='b' and result['external_model_calls']==0
    again=command(root,*arguments)
    assert again.returncode==0 and json.loads(again.stdout)['assignment_id']==result['assignment_id'] and json.loads(again.stdout)['replayed']
    assert counters(directory)==([('a',25,0,100),('b',0,12,100)],1)
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        assert db.execute('SELECT count(*) FROM model_billing_assignments').fetchone()[0]==1


def test_both_new_cli_decisions_are_fenced_before_a_live_gateway_or_worker(tmp_path):
    """Real maintenance leases deny both new modes before any financial operation or evidence write."""
    root,store,owner,directory,original,normalized=prepared_root(tmp_path)
    before=counters(directory)
    cases=[(root/'.maintenance.lock',['--correct-line','missing-line','--reference-receipt','missing-receipt','--expected-correction-revision','0']),
        (root/'workspaces'/'.leases'/(owner['workspace_id']+'.lock'),['--assign-line','missing-line','--charge-id','missing-charge'])]
    for lease,arguments in cases:
        with ProcessLease(lease):
            result=command(root,*arguments,'--reason','Authored operation must stay fenced')
        assert result.returncode==2 and counters(directory)==before
        assert not (directory/'files').exists()
    with closing(sqlite3.connect(directory/'studio.db')) as db:
        assert db.execute('SELECT count(*) FROM model_billing_corrections').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM model_billing_assignments').fetchone()[0]==0
