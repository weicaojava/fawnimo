"""Stopped owned-root readiness: pending work/fees remain fenced and private financial/security state is represented only by hashes."""
from contextlib import closing,ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from app.controlplane.maintenance import ProcessLease
from app.services.community.local_release_files import plain,database_receipt,connection_receipt,inventory,sqlite_file

TERMINAL=frozenset({'succeeded','failed','cancelled','ready'})
ACTIVITY={
    'agent_execution_runs':'status','asset_image_generation_jobs':'status','edit_export_jobs':'state',
    'execution_jobs':'state','generation_tasks':'status','local_speech_jobs':'state','sound_export_jobs':'state',
    'transition_export_jobs':'state','video_understanding_jobs':'state'}
SLOTS=frozenset({'edit_export_slot','execution_slots','local_speech_slot','sound_export_slot','transition_export_slot'})
PASSIVE=frozenset({'generation_task_links','model_job_charges','native_video_job_charges','shot_plan_job_links','model_call_charges'})


class RuntimeSafetyError(ValueError):
    """Return fixed maintenance reasons without exposing rows, paths, credentials, provider evidence or user identities."""


def digest(value):
    """Hash deterministic private facts without placing their row values in an operation receipt."""
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')).hexdigest()


def identifier(value):
    """Only bounded known SQLite identifiers can enter generated read queries; values always remain bound parameters."""
    if not isinstance(value,str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',value) is None:
        raise RuntimeSafetyError('Unsupported stopped database identifier')
    return '"'+value+'"'


def checked_file(path):
    """Reject links, redirection and shared physical files before a private owned-root read."""
    value=plain(path);entry=value.stat()
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1:
        raise RuntimeSafetyError('A fixed private regular database is required')
    return value


def checked_lease(path):
    """Reject redirected parents or shared lock files before supported native leases can create or open anything."""
    value=plain(path,exists=False)
    if value.exists():checked_file(value)
    return value


def read_connection(path,deadline):
    """Open committed WAL content read-only and in one real snapshot; never use immutable mode or initialize/migrate a source."""
    db=sqlite3.connect(checked_file(path).as_uri()+'?mode=ro',uri=True,timeout=1.5)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,8388608)
        db.set_progress_handler(lambda:1 if time.monotonic()>deadline else 0,1000)
    except BaseException:
        db.close();raise
    return db


def inspect_activity(path,deadline):
    """Refuse nonterminal work, retained leases, unclassified activity and unresolved per-project reservations without changing any row."""
    with closing(read_connection(path,deadline)) as db:
        names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in names:
            table=identifier(name)
            if name in ACTIVITY:
                column=identifier(ACTIVITY[name]);columns={row[1] for row in db.execute('PRAGMA table_info('+table+')')}
                if ACTIVITY[name] not in columns:raise RuntimeSafetyError('Unsupported task state schema')
                if db.execute('SELECT 1 FROM '+table+' WHERE '+column+' IS NULL OR '+column+' NOT IN (?,?,?,?) LIMIT 1',tuple(sorted(TERMINAL))).fetchone():
                    raise RuntimeSafetyError('Pending work requires completion or explicit maintenance before a version change')
                for lease in ('lease_token','lease_expires_at','lease_until'):
                    if lease in columns and db.execute('SELECT 1 FROM '+table+' WHERE '+identifier(lease)+' IS NOT NULL AND '+identifier(lease)+' != 0 LIMIT 1').fetchone():
                        raise RuntimeSafetyError('Retained task ownership requires maintenance before a version change')
            elif name in SLOTS:
                columns={row[1] for row in db.execute('PRAGMA table_info('+table+')')}
                if not {'job_id','token','lease_until'}<=columns:raise RuntimeSafetyError('Unsupported execution slot schema')
                if db.execute('SELECT 1 FROM '+table+' WHERE job_id IS NOT NULL OR token IS NOT NULL OR (lease_until IS NOT NULL AND lease_until != 0) LIMIT 1').fetchone():
                    raise RuntimeSafetyError('Retained execution slot requires maintenance before a version change')
            elif name not in PASSIVE and re.search(r'(^|_)(jobs?|tasks?|runs?|calls?|slots?)(_|$)',name):
                if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                    raise RuntimeSafetyError('Unknown retained activity requires explicit version support')
        if 'execution_budgets' in names and db.execute('SELECT 1 FROM execution_budgets WHERE reserved_minor != 0 LIMIT 1').fetchone():
            raise RuntimeSafetyError('Original project funds remain reserved; version change refused')
        if 'execution_budget_entries' in names and db.execute("SELECT 1 FROM execution_budget_entries WHERE state NOT IN ('settled','released') LIMIT 1").fetchone():
            raise RuntimeSafetyError('Original project charge outcome remains unresolved')
        if 'model_call_charges' in names:
            required={'execution_budget_entries','model_billing_receipts'}
            if not required<=names:raise RuntimeSafetyError('Incomplete model settlement schema')
            unresolved=db.execute("SELECT 1 FROM model_call_charges c LEFT JOIN execution_budget_entries e ON e.job_id=c.id AND e.project_id=c.project_id "
                "LEFT JOIN model_billing_receipts r ON r.charge_id=c.id AND r.project_id=c.project_id "
                "WHERE COALESCE(((c.dispatch_state IN ('planned','not_sent') AND e.state='released') OR (e.state='settled' AND r.id IS NOT NULL)),0)=0 LIMIT 1").fetchone()
            if unresolved:raise RuntimeSafetyError('A model call has no proven original settlement or unsent release')
        inspect_billing(db,names)
        return connection_receipt(db,deadline)


def inspect_billing(db,names):
    """Compile the existing correction/duplicate-aware billing rule into the same read-only SQLite snapshot; no client or async task is created."""
    if 'model_billing_reviews' not in names:return
    required={'model_billing_receipts','model_billing_corrections','model_billing_resolutions','model_call_charges','execution_budget_entries'}
    if not required<=names:raise RuntimeSafetyError('Unsupported billing review schema')
    from sqlalchemy import select,func
    from sqlalchemy.dialects import sqlite
    from app.models.model_billing import ModelBillingReview
    from app.services.worker.model_billing import resolved_review
    expressions=[select(func.count()).select_from(ModelBillingReview).where(
        ModelBillingReview.status.in_({'unmatched','ambiguous','disputed','conflict','price_violation'}),~resolved_review()),
        select(func.count()).select_from(ModelBillingReview).where(ModelBillingReview.status=='price_violation')]
    # Every literal is a fixed installed rule, never a request value. Reusing
    # this expression preserves U's exact correction/duplicate resolution rules.
    if any(db.execute(str(expression.compile(dialect=sqlite.dialect(),compile_kwargs={'literal_binds':True}))).fetchone()[0] for expression in expressions):
        raise RuntimeSafetyError('Unresolved original billing or breached price history requires maintenance')


def financial_tables(tables):
    """Bind monetary, model-account and execution-lock history while allowing explicit rollback of ordinary picture/editor data."""
    return {name:receipt for name,receipt in tables.items() if re.search(r'model|provider|budget|bill|connection|credential|setting|workspace_boundary|workspace_identity|workspace_trial_quota',name,re.I)}


def _inspect_idle_gateway_once(root,deadline):
    """Acquire fresh fences and exact read snapshots once; a failure never retains a successful prior observation as authority."""
    source=plain(root)
    try:
        with ExitStack() as stack:
            stack.enter_context(ProcessLease(checked_lease(source/'.maintenance.lock')))
            control_path=checked_file(source/'control.db')
            with closing(read_connection(control_path,deadline)) as db:
                workspaces=db.execute('SELECT id,owner_id FROM workspaces ORDER BY id').fetchall()
                users=[row[0] for row in db.execute('SELECT id FROM users ORDER BY id')]
                control=connection_receipt(db,deadline)
            if len(workspaces)>1024 or any(not isinstance(row[0],str) or re.fullmatch(r'ws_[a-f0-9]{32}',row[0]) is None for row in workspaces):
                raise RuntimeSafetyError('Unsupported owned workspace inventory')
            spaces=source/'workspaces';ids={row[0] for row in workspaces}
            if spaces.exists():
                plain(spaces)
                if any(item.name!='.leases' and item.name not in ids for item in spaces.iterdir()):
                    raise RuntimeSafetyError('Unclaimed workspace data requires maintenance')
            for workspace,_owner in workspaces:stack.enter_context(ProcessLease(checked_lease(spaces/'.leases'/(workspace+'.lock'))))
            before=inventory(source,deadline);databases={};finance={}
            for workspace,_owner in workspaces:
                folder=spaces/workspace;path=folder/'studio.db'
                if not path.exists():
                    if folder.exists() and any(folder.iterdir()):raise RuntimeSafetyError('Incomplete workspace startup requires maintenance')
                    continue
                from app.core.workspace_boundary import assert_workspace_boundary
                assert_workspace_boundary(checked_file(path),workspace)
                receipt=inspect_activity(path,deadline);databases[workspace]=receipt;finance[workspace]=financial_tables(receipt['tables'])
            after=inventory(source,deadline)
            database_paths={'control.db'}|{'workspaces/'+workspace+'/studio.db' for workspace in databases}
            if set(before)!=set(after) or any(before[name]!=after[name] for name in before if name not in database_paths):
                raise RuntimeSafetyError('Owned data changed during stopped readiness inspection')
            if database_receipt(control_path,deadline)!=control or any(database_receipt(spaces/workspace/'studio.db',deadline)!=receipt for workspace,receipt in databases.items()):
                raise RuntimeSafetyError('Owned database changed during stopped readiness inspection')
            # Only registered media/file subtrees are ordinary user content.
            # Other non-DB files can carry model, execution or credential policy.
            sensitive={name:entry for name,entry in after.items() if name not in database_paths and name!='.reference-delivery.key'
                       and not (len(Path(name).parts)>=4 and Path(name).parts[0]=='workspaces' and Path(name).parts[2] in {'files','media'})}
            for name in sensitive:
                if sqlite_file(source/name):sensitive[name]={'logical_database':database_receipt(source/name,deadline)}
            return {'profile':'local_runtime_idle_v1','idle':True,'workspace_count':len(workspaces),'initialized_workspaces':len(databases),
                    'topology_sha256':digest(workspaces),'user_ids_sha256':digest(users),'control_schema_sha256':control['schema_sha256'],
                    'current_control_sha256':digest(control),'financial_security_sha256':digest({'databases':finance,'private_config_files':sensitive}),
                    'source_state_sha256':digest({'control':control,'databases':databases,'files':after}),
                    'security_carry_forward_required':True,'historical_only':True,'fresh_action_authorized':False,'external_model_calls':0,'paid_authorization':0}
    except RuntimeSafetyError:raise
    except Exception as error:
        failure=RuntimeSafetyError('Stopped owned-root readiness could not be verified')
        # Private maintainer diagnostics identify only installed exception/code
        # classes; neither the public message nor these attributes reflect rows.
        failure.cause_type=type(error).__name__
        failure.sqlite_code=getattr(error,'sqlite_errorcode',None)
        raise failure from None


def inspect_idle_gateway(root,*,seconds=60):
    """Reacquire all fences for bounded read-only Windows WAL stabilization; never retry a task, charge, write or unknown error."""
    if type(seconds) is not int or not 1<=seconds<=120:raise RuntimeSafetyError('A bounded readiness deadline is required')
    deadline=time.monotonic()+seconds;storage_until=min(deadline,time.monotonic()+1);rechecks=0
    while True:
        try:
            result=_inspect_idle_gateway_once(root,deadline)
            result['storage_rechecks']=rechecks
            return result
        except RuntimeSafetyError as error:
            if (os.name!='nt' or getattr(error,'sqlite_code',None)!=sqlite3.SQLITE_IOERR_TRUNCATE
                    or time.monotonic()>=storage_until or rechecks>=100):
                raise
            rechecks+=1;time.sleep(min(.01,max(0,storage_until-time.monotonic())))
