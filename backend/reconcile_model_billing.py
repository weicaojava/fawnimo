"""Offline operator import/reconciliation for an explicitly selected owned workspace.

No model, payment or supplier endpoint is called. Stop only the selected identity
gateway before mutation; original legacy studio services need not be stopped.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path


def read_bounded(path,limit):
    """Read an explicit private evidence file with a real bound, independent of its reported size."""
    target=path.resolve(strict=True)
    if not target.is_file():
        raise ValueError('Evidence must be a file')
    with target.open('rb') as stream:
        content=stream.read(limit+1)
    if not content or len(content)>limit:
        raise ValueError('Evidence size is invalid')
    return content


def archive_evidence(directory,original,normalized):
    """Preserve exact private source bytes outside FileItem/public downloads, with no replacement.

    The fixed files root is already part of supported offline backups. These
    unregistered billing objects are never added to the user's media library.
    """
    root=directory/'files'
    if root.resolve()!=root:
        raise ValueError('Evidence storage cannot redirect')
    root.mkdir(exist_ok=True)
    source_hash=hashlib.sha256(original).hexdigest()
    folder=root
    for part in ('billing-evidence',source_hash):
        candidate=folder/part
        if candidate.resolve()!=candidate or not candidate.is_relative_to(root):
            raise ValueError('Evidence storage cannot redirect')
        candidate.mkdir(exist_ok=True)
        if not candidate.is_dir():
            raise ValueError('Evidence storage is not a directory')
        folder=candidate
    for name,content in [('original.bin',original),('normalized-'+hashlib.sha256(normalized).hexdigest()+'.csv',normalized)]:
        target=folder/name
        if target.resolve()!=target:
            raise ValueError('Evidence path cannot redirect')
        try:
            with target.open('xb') as stream:
                stream.write(content);stream.flush();os.fsync(stream.fileno())
        except FileExistsError:
            if read_bounded(target,10*1024*1024)!=content:
                raise ValueError('Existing private evidence differs') from None


async def apply(args,directory,workspace_id,actor_id):
    """Open only the server-resolved marked database, never a request-supplied data path."""
    os.environ.update(JELLYFISH_SKIP_ENV_FILE='1',DEBUG='false',WORKSPACE_ID=workspace_id,
        DATABASE_URL='sqlite+aiosqlite:///'+(directory/'studio.db').as_posix())
    from app.core.db import async_session_maker,close_db
    from app.core.contracts.model_accounting import BillingStatementContext
    from app.services.worker.model_billing import import_statement,parse_normalized_csv,reconcile_statement,resolve_duplicate
    try:
        if args.resolve_line:
            identifier=await resolve_duplicate(async_session_maker,args.resolve_line,args.reference_receipt,
                reason=args.reason,recorded_by=actor_id)
            return {'resolution_id':identifier,'action':'duplicate_evidence','external_model_calls':0,
                'provider_automatically_authenticated':False}
        if args.statement_id:
            identifier=args.statement_id
        else:
            original=read_bounded(args.original,10*1024*1024)
            normalized=read_bounded(args.normalized,2*1024*1024)
            parse_normalized_csv(normalized)
            context=BillingStatementContext(provider_id=args.provider_id,provider_connection_version=args.connection_version,
                account_version=args.account_version,statement_key=args.statement_key)
            archive_evidence(directory,original,normalized)
            identifier=await import_statement(async_session_maker,context=context,normalized_csv=normalized,original_content=original,imported_by=actor_id)
        result=await reconcile_statement(async_session_maker,identifier,recorded_by=actor_id)
        counts={status:sum(item['status']==status for item in result) for status in ('applied','unmatched','ambiguous','conflict','disputed','duplicate_resolved')}
        return {'statement_id':identifier,'rows':len(result),**counts,'external_model_calls':0,'provider_automatically_authenticated':False}
    finally:
        await close_db()


def main():
    """Require an existing active owner, fixed-path checks and both supported maintenance leases."""
    parser=argparse.ArgumentParser(description='Offline normalized model billing import; never calls a model or changes a project ceiling')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--account',required=True)
    parser.add_argument('--statement-id')
    parser.add_argument('--resolve-line')
    parser.add_argument('--reference-receipt')
    parser.add_argument('--reason')
    parser.add_argument('--original',type=Path)
    parser.add_argument('--normalized',type=Path)
    parser.add_argument('--provider-id')
    parser.add_argument('--connection-version',type=int)
    parser.add_argument('--account-version')
    parser.add_argument('--statement-key')
    args=parser.parse_args()
    import_values=[args.original,args.normalized,args.provider_id,args.connection_version,args.account_version,args.statement_key]
    resolution_values=[args.resolve_line,args.reference_receipt,args.reason]
    if any(value is not None for value in resolution_values):
        if any(value is None for value in resolution_values) or args.statement_id or any(value is not None for value in import_values):
            parser.error('Duplicate resolution requires only --resolve-line, --reference-receipt and --reason')
    elif (args.statement_id and any(value is not None for value in import_values)) or (not args.statement_id and any(value is None for value in import_values)):
        parser.error('Choose either an existing --statement-id, or all explicit source/account import fields')
    os.environ['JELLYFISH_SKIP_ENV_FILE']='1'
    from app.controlplane.maintenance import MaintenanceBusy,ProcessLease
    from app.core.workspace_boundary import assert_workspace_boundary,validate_workspace_id
    from app.controlplane.store import IdentityStore
    root=args.root.resolve()
    if not (root/'control.db').is_file():
        parser.error('The selected existing identity root was not found')
    try:
        with ProcessLease(root/'.maintenance.lock'):
            store=IdentityStore(root)
            with store.connect() as db:
                rows=db.execute("SELECT w.id,u.id AS user_id FROM users u JOIN workspaces w ON w.owner_id=u.id JOIN memberships m ON m.user_id=u.id AND m.workspace_id=w.id WHERE u.account=? AND u.disabled=0 AND m.role='owner'",
                    (args.account.strip().lower(),)).fetchall()
            if len(rows)!=1:
                raise ValueError('A unique active owner is required')
            workspace_id=validate_workspace_id(rows[0]['id'])
            directory=root/'workspaces'/workspace_id
            if directory.resolve()!=directory or not (directory/'studio.db').is_file():
                raise ValueError('Fixed existing workspace is required')
            with ProcessLease(root/'workspaces'/'.leases'/(workspace_id+'.lock')):
                from app.controlplane.migration import migration_pending
                if migration_pending(root/'workspaces',workspace_id):
                    parser.exit(2,'当前空间迁移尚未完成；请先恢复并完成原认领，再导入账单。账目和私有证据均未写入。\n')
                assert_workspace_boundary(directory/'studio.db',workspace_id)
                result=asyncio.run(apply(args,directory,workspace_id,rows[0]['user_id']))
                assert_workspace_boundary(directory/'studio.db',workspace_id)
        print(json.dumps(result,ensure_ascii=False))
    except MaintenanceBusy:
        parser.exit(2,'此身份平台或空间仍在运行；请先停止对应网关后再导入账单。原入口不需要切换。\n')
    except Exception:
        parser.exit(2,'账单核对未完成；请检查有效所有者、固定空间、输入格式及已升级的账目结构。原预留和历史不会被静默清空。\n')


if __name__=='__main__':
    main()
