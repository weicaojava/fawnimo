"""Operator-only legacy staging/claim/restore CLI; no command starts a model or HTTP service."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re


async def upgrade_copy():
    """Migrate only a pre-bound copy under explicit isolated environment before any app import."""
    if not re.fullmatch(r'ws_[a-f0-9]{32}',os.environ.get('WORKSPACE_ID','')) or os.environ.get('WORKSPACE_RUNTIME_LOCKED')!='true':
        raise ValueError('Private offline upgrade context missing')
    url = os.environ.get('DATABASE_URL','')
    if not url.startswith('sqlite+aiosqlite:///') or not Path(url[20:]).is_absolute():
        raise ValueError('Explicit absolute SQLite copy required')
    import app.api.v1  # noqa: F401
    from app.core.db import init_db, close_db
    try:
        await init_db()
    finally:
        await close_db()


def main():
    """Require concrete source, new artifact paths and an explicit existing owner for claim."""
    os.environ['JELLYFISH_SKIP_ENV_FILE']='1'
    parser=argparse.ArgumentParser(description='Stage, explicitly claim or restore legacy Fawnimo data offline')
    commands=parser.add_subparsers(dest='command',required=True)
    stage=commands.add_parser('stage')
    stage.add_argument('--database',type=Path,required=True)
    stage.add_argument('--media',type=Path,required=True)
    stage.add_argument('--files',type=Path,required=True)
    stage.add_argument('--bundle',type=Path,required=True)
    claim=commands.add_parser('claim')
    claim.add_argument('--bundle',type=Path,required=True)
    claim.add_argument('--root',type=Path,required=True)
    claim.add_argument('--account',required=True)
    restore=commands.add_parser('restore')
    restore.add_argument('--bundle',type=Path,required=True)
    restore.add_argument('--destination',type=Path,required=True)
    verify=commands.add_parser('verify')
    verify.add_argument('--bundle',type=Path,required=True)
    commands.add_parser('_upgrade',help=argparse.SUPPRESS)
    args=parser.parse_args()
    from app.controlplane.migration import stage_legacy, claim_bundle, restore_bundle, verify_bundle
    if args.command=='stage':
        result=stage_legacy(args.database,args.media,args.files,args.bundle)
    elif args.command=='claim':
        result=claim_bundle(args.bundle,args.root,args.account)
    elif args.command=='restore':
        result=restore_bundle(args.bundle,args.destination)
    elif args.command=='verify':
        manifest,_=verify_bundle(args.bundle)
        result={'import_id':manifest['import_id'],'verified':True}
    else:
        asyncio.run(upgrade_copy());result={'schema_upgraded':True,'services_started':False}
    print(json.dumps(result))


if __name__=='__main__':
    main()
