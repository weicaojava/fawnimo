"""Actual private identity roots/native ownership and strict operator inputs for the local runtime controller; no engine or model calls."""
import copy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import pytest
from pydantic import ValidationError
from app.core.contracts.local_runtime_release import LocalRuntimePolicy,LocalRuntimeActivation,LocalRuntimeRollback,LocalRuntimeStop
from app.controlplane.store import IdentityStore
from app.controlplane.maintenance import ProcessLease
from app.services.community.runtime_release_safety import inspect_idle_gateway,RuntimeSafetyError
from app.services.community.local_release_files import database_receipt,copy_snapshot,restore_snapshot


def enabled_policy():
    """Author explicit immutable image identities, never discover tags, credentials, engine contexts or other services."""
    return {'enabled':True,'operator_allowed_images':[{'image_id':'sha256:'+'a'*64,'source_commit':'b'*40},
        {'image_id':'sha256:'+'c'*64,'source_commit':'d'*40}],'helper_image_id':'sha256:'+'a'*64,'bootstrap_sha256':'e'*64}


def owned_root(tmp_path):
    """Create two entirely new private accounts and personal spaces without existing users, business data or model configuration."""
    root=tmp_path/'new-owned-gateway';root.mkdir(mode=0o700)
    store=IdentityStore(root)
    first=store.provision('runtime-new-a','synthetic-new-runtime-password-a')
    second=store.provision('runtime-new-b','synthetic-new-runtime-password-b')
    return root,store,first,second


@pytest.mark.parametrize('change',[{'enabled':'true'},{'external_fee_limit_fen':1},{'network':'host'},
    {'stable_port':18000},{'preview_port':8000},{'stable_port':17888},{'preview_port':18080},
    {'bootstrap_sha256':None},{'helper_image_id':'sha256:'+'f'*64},{'max_operations':17}])
def test_strict_operator_policy_cannot_widen_or_borrow_original_resources(change):
    """The default profile cannot acquire ambient credentials, public promotion, arbitrary resource knobs or original application ports."""
    body=enabled_policy();body.update(change)
    with pytest.raises(ValidationError):LocalRuntimePolicy.model_validate(body)


def test_disabled_policy_and_host_only_origins_are_separate():
    """Initialization alone grants no action; separate loopback hosts keep stable and preview browser cookies apart."""
    default=LocalRuntimePolicy();assert default.enabled is False and default.automatic_public_promotion is False
    policy=LocalRuntimePolicy.model_validate(enabled_policy())
    assert policy.origin('stable')=='http://127.0.0.1:18080' and policy.origin('preview')=='http://127.0.0.2:18081'
    assert policy.image('sha256:'+'a'*64).source_commit=='b'*40
    with pytest.raises(ValueError):policy.image('some-mutable-image:latest')
    body=enabled_policy();body['operator_allowed_images'].append(copy.deepcopy(body['operator_allowed_images'][0]))
    with pytest.raises(ValidationError):LocalRuntimePolicy.model_validate(body)


@pytest.mark.parametrize('change',[{'expected_generation':True},{'expected_generation':9},{'root':'other-data'},
    {'command':['python','arbitrary.py']},{'image_id':'mutable:latest'},{'slot':'production'}])
def test_activation_has_only_original_exact_slot_intent(change):
    """No request can choose paths, executable arguments, mutable tags or another deployment target."""
    body={'request_key':'runtime-intent-001','slot':'preview','expected_generation':0,'reason':'own new local verification','image_id':'sha256:'+'a'*64}
    body.update(change)
    with pytest.raises(ValidationError):LocalRuntimeActivation.model_validate(body)


def test_rollback_and_stop_require_exact_existing_generation_and_acknowledgment():
    """An old snapshot cannot silently become an empty-slot deployment or hide retained later edits."""
    body={'request_key':'runtime-rollback-001','slot':'stable','expected_generation':1,'reason':'own rollback verification','snapshot_id':'a'*64}
    with pytest.raises(ValidationError):LocalRuntimeRollback.model_validate(body)
    body['acknowledge_retained_later_edits']=True;assert LocalRuntimeRollback.model_validate(body).expected_generation==1
    body['expected_generation']=0
    with pytest.raises(ValidationError):LocalRuntimeRollback.model_validate(body)
    with pytest.raises(ValidationError):LocalRuntimeStop(request_key='runtime-stop-001',slot='stable',expected_generation=0,reason='own stop')


def test_actual_private_root_readiness_preserves_committed_wal_and_all_identity_rows(tmp_path):
    """A real uncheckpointed WAL is read in the same logical snapshot; the receipt contains no account or password and grants no action."""
    root,store,first,second=owned_root(tmp_path)
    writer=sqlite3.connect(root/'control.db')
    try:
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE runtime_guard_evidence(value TEXT NOT NULL)')
        writer.execute('INSERT INTO runtime_guard_evidence VALUES (?)',('authored committed WAL only',));writer.commit()
        assert Path(str(root/'control.db')+'-wal').stat().st_size>0
        before=database_receipt(root/'control.db',time.monotonic()+30)
        receipt=inspect_idle_gateway(root)
        assert receipt['idle'] and receipt['workspace_count']==2 and receipt['initialized_workspaces']==0
        assert receipt['historical_only'] and receipt['fresh_action_authorized'] is False and receipt['security_carry_forward_required']
        assert database_receipt(root/'control.db',time.monotonic()+30)==before
        encoded=json.dumps(receipt)
        assert 'runtime-new-a' not in encoded and first['user_id'] not in encoded and 'synthetic-new-runtime-password' not in encoded
        assert writer.execute('SELECT value FROM runtime_guard_evidence').fetchall()==[('authored committed WAL only',)]
    finally:writer.close()


def test_actual_native_gateway_lease_blocks_readiness(tmp_path):
    """A supported live owner cannot be presented as stopped even when it has no business database yet."""
    root,*_=owned_root(tmp_path)
    with ProcessLease(root/'.maintenance.lock'):
        with pytest.raises(RuntimeSafetyError):inspect_idle_gateway(root)
    assert inspect_idle_gateway(root)['idle']


@pytest.mark.parametrize('kind',['redirected_lease_parent','hardlinked_gateway_lease'])
def test_foreign_lock_targets_are_rejected_before_access(tmp_path,kind):
    """A leftover redirected lock cannot touch a different synthetic directory or shared physical file."""
    root,store,first,second=owned_root(tmp_path);other=tmp_path/'unrelated-owned-fixture';other.mkdir()
    if kind=='redirected_lease_parent':
        spaces=root/'workspaces';spaces.mkdir()
        try:(spaces/'.leases').symlink_to(other,target_is_directory=True)
        except OSError:pytest.skip('This platform does not grant directory symlink creation')
        before=list(other.iterdir())
    else:
        target=other/'do-not-lock';target.write_bytes(b'authored unrelated file')
        os.link(target,root/'.maintenance.lock');before=hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(RuntimeSafetyError):inspect_idle_gateway(root)
    if kind=='redirected_lease_parent':assert list(other.iterdir())==before
    else:assert hashlib.sha256(target.read_bytes()).hexdigest()==before


@pytest.mark.asyncio
async def test_actual_fixed_worker_must_exit_before_full_schema_is_idle(tmp_path,request):
    """The real private worker retains its native lock; after joined exit all current task/fee tables and a fresh restore are checked."""
    from app.controlplane.workers import WorkspaceWorkers
    root,store,first,second=owned_root(tmp_path)
    probe=os.environ.get('FAWNIMO_TEST_FFMPEG') or os.environ.get('TEST_MEDIA_PROBE')
    pool=WorkspaceWorkers(root/'workspaces',probe=Path(probe) if probe else None,capacity=2)
    try:
        worker=await pool.get(first['workspace_id']);assert worker.process.returncode is None
        with pytest.raises(RuntimeSafetyError):inspect_idle_gateway(root)
    finally:await pool.close()
    assert not pool.workers
    try:before=inspect_idle_gateway(root)
    except RuntimeSafetyError as error:
        pytest.fail('Stopped guard diagnostic: '+str(getattr(error,'cause_type','guard_refusal'))+'/'+str(getattr(error,'sqlite_code',None)))
    assert before['initialized_workspaces']==1
    request.node.user_properties.append(('storage_rechecks',str(before['storage_rechecks'])))
    archive=tmp_path/'private-snapshot';manifest=copy_snapshot(root,archive,time.monotonic()+120)
    restored=tmp_path/'fresh-restored';restore_snapshot(archive,manifest,restored,time.monotonic()+120)
    after=inspect_idle_gateway(restored)
    assert before['financial_security_sha256']==after['financial_security_sha256']
    assert before['topology_sha256']==after['topology_sha256'] and before['control_schema_sha256']==after['control_schema_sha256']


@pytest.mark.parametrize('code',[sqlite3.SQLITE_IOERR_TRUNCATE,sqlite3.SQLITE_CORRUPT])
def test_only_observed_windows_storage_error_can_recheck_read_only_state(tmp_path,monkeypatch,code):
    """A known cold-storage refusal may recheck with fresh fences; unrelated corruption remains a first-attempt refusal."""
    from app.services.community import runtime_release_safety as guard
    root,*_=owned_root(tmp_path);original=guard._inspect_idle_gateway_once;calls=[]
    def once(source,deadline):
        """Inject one fixed result code before the real unchanged read-only inspection."""
        calls.append(1)
        if len(calls)==1:
            failure=RuntimeSafetyError('authored storage refusal');failure.sqlite_code=code
            raise failure
        return original(source,deadline)
    monkeypatch.setattr(guard,'_inspect_idle_gateway_once',once)
    before=database_receipt(root/'control.db',time.monotonic()+30)
    if os.name=='nt' and code==sqlite3.SQLITE_IOERR_TRUNCATE:
        receipt=guard.inspect_idle_gateway(root);assert receipt['idle'] and receipt['storage_rechecks']==1 and len(calls)==2
    else:
        with pytest.raises(RuntimeSafetyError):guard.inspect_idle_gateway(root)
        assert len(calls)==1
    assert database_receipt(root/'control.db',time.monotonic()+30)==before


@pytest.mark.skipif(os.name!='nt',reason='The observed Windows-only storage stabilization path is not enabled on this platform')
def test_persistent_storage_error_exhausts_a_finite_read_budget(tmp_path,monkeypatch):
    """A permanent I/O failure cannot become an unbounded retry or an accepted readiness result."""
    from types import SimpleNamespace
    from app.services.community import runtime_release_safety as guard
    root,*_=owned_root(tmp_path);clock=[0.0];calls=[]
    def once(*_args):
        """Retain the same fixed storage refusal on every attempted state read."""
        calls.append(1);failure=RuntimeSafetyError('authored persistent storage refusal');failure.sqlite_code=sqlite3.SQLITE_IOERR_TRUNCATE
        raise failure
    def advance(seconds):
        """Advance only this module's private test clock; no global time function is changed."""
        clock[0]+=seconds
    monkeypatch.setattr(guard,'_inspect_idle_gateway_once',once)
    monkeypatch.setattr(guard,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=advance))
    with pytest.raises(RuntimeSafetyError):guard.inspect_idle_gateway(root)
    assert 1<len(calls)<=101 and clock[0]<=1.001


def test_real_unknown_project_charge_blocks_and_settlement_remains_read_only(tmp_path):
    """A real authored reserved/accepted ledger stays blocked until its actual original invoice is reconciled; no money is moved by inspection."""
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from tests.test_model_billing_cli import prepared_root
    from tests.test_model_billing import imported,invoice
    from app.services.worker.model_billing import reconcile_statement
    root,store,owner,data,*_=prepared_root(tmp_path)
    before=database_receipt(data/'studio.db',time.monotonic()+30)
    with pytest.raises(RuntimeSafetyError):inspect_idle_gateway(root)
    assert database_receipt(data/'studio.db',time.monotonic()+30)==before
    async def settle():
        """Apply only the existing operator reconciliation to this new synthetic bill, never a provider call or refund."""
        engine=create_async_engine('sqlite+aiosqlite:///'+(data/'studio.db').as_posix());factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            statement=await imported(factory,invoice(),key='owned-runtime-settlement')
            assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
        finally:await engine.dispose()
    asyncio.run(settle())
    settled=database_receipt(data/'studio.db',time.monotonic()+30);receipt=inspect_idle_gateway(root)
    assert receipt['idle'] and database_receipt(data/'studio.db',time.monotonic()+30)==settled


def test_actual_budget_revision_changes_financial_restore_identity(tmp_path):
    """An old data snapshot cannot invisibly restore an earlier project authorization ceiling, even with no unresolved funds."""
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from tests.test_model_billing_cli import prepared_root
    from tests.test_model_billing import imported,invoice
    from app.services.worker.model_billing import reconcile_statement
    from app.services.worker.workspace_video_runtime import read_project_budget,save_project_budget
    from app.core.contracts.video_runtime_setup import ProjectBudgetWrite
    root,store,owner,data,*_=prepared_root(tmp_path)
    async def change_budget(change=False):
        """Use the real CAS budget service and immutable edit receipt on the existing original project."""
        engine=create_async_engine('sqlite+aiosqlite:///'+(data/'studio.db').as_posix());factory=async_sessionmaker(engine,expire_on_commit=False)
        try:
            if not change:
                statement=await imported(factory,invoice(),key='owned-runtime-baseline')
                assert (await reconcile_statement(factory,statement))[0]['status']=='applied'
            else:
                async with factory() as db:current=await read_project_budget(db,'a')
                await save_project_budget(factory,'a',ProjectBudgetWrite(expected_revision=current.revision,limit_minor=200),actor_id=owner['user_id'])
        finally:await engine.dispose()
    asyncio.run(change_budget())
    before=inspect_idle_gateway(root)
    asyncio.run(change_budget(True))
    after=inspect_idle_gateway(root)
    assert before['financial_security_sha256']!=after['financial_security_sha256'] and after['idle']


@pytest.mark.parametrize('acknowledgment',[False,1,'true'])
def test_explicit_quiescence_does_not_coerce_permission_values(acknowledgment):
    """Stopping an interrupted environment requires an actual true boolean and a fresh exact observation, not a coerced truthy value."""
    from app.core.contracts.local_runtime_release import LocalRuntimeQuiescence
    with pytest.raises(ValidationError):
        LocalRuntimeQuiescence(request_key='strict-quiesce-001',operation_id='a'*64,expected_observation_sha256='b'*64,
            acknowledge_stopped_data_retained=acknowledgment,reason='authored invalid acknowledgment')
