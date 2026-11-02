"""Explicit local-maintainer Linux code-check commands, without issue-selected executors or GitHub writes."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Enable the separate profile deliberately; recover original keys before source/Git/Docker, and keep cancellation durable when disconnected."""
    parser = argparse.ArgumentParser(description='Fawnimo bounded local Linux candidate checks (no GitHub write/review/deploy authority)')
    parser.add_argument('--changes-root',type=Path,required=True)
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--root',type=Path)
    parser.add_argument('--image-id')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--initialize-checks',action='store_true')
    action.add_argument('--check-file',type=Path)
    action.add_argument('--get-check',metavar='ATTEMPT_ID')
    action.add_argument('--cancel-check',metavar='ATTEMPT_ID')
    action.add_argument('--reconcile-check',metavar='ATTEMPT_ID')
    args = parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_code_checks import CommunityCodeCheckInput
    from app.services.community.events import unique_fields,invalid_number,finite_float
    from app.services.community.store import CommunityRequestStore,CommunityStoreConflict
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.code_check_store import CommunityCodeCheckStore
    from app.services.community.code_check_runtime import run_current_check,reconcile_locally
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    try:
        config = CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        changes = CommunityChangeStore(args.changes_root,config)
        checks = CommunityCodeCheckStore(changes,initialize=args.initialize_checks)
        if args.initialize_checks: result = {'profile':'linux_pytest_check_v1','policy':checks.policy.model_dump(),'execution_started':False}
        elif args.get_check: result = checks.get(args.get_check)
        elif args.cancel_check: result = reconcile_locally(checks,args.cancel_check,cancel=True)
        elif args.reconcile_check: result = reconcile_locally(checks,args.reconcile_check)
        else:
            if args.image_id is None: raise CommunityStoreConflict('Maintainer-selected immutable local image ID is required')
            with args.check_file.open('rb') as stream: raw = stream.read(4097)
            if len(raw) > 4096: raise CommunityStoreConflict('Strict check request exceeds4KiB')
            body = CommunityCodeCheckInput.model_validate(json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float))
            result = checks.replay(body,args.image_id)
            if result is None:
                if args.root is None: raise CommunityStoreConflict('New isolated checks require their exact existing source root')
                left,right = args.root.absolute(),changes.root
                if left.resolve() != left or left.is_relative_to(right) or right.is_relative_to(left): raise CommunityStoreConflict('Source/change roots overlap or redirect')
                preparation = CommunityPreparationStore(CommunityQueueStore(CommunityRequestStore(args.root,config)))
                result = asyncio.run(run_current_check(preparation,checks,body,args.image_id))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError,sqlite3.Error,asyncio.TimeoutError,RecursionError):
        parser.exit(1,'Check not completed: verify the exact original source, separate explicitly enabled check root, immutable local image and local Linux Docker. Existing reservations are retained; reconcile before retrying.\n')


if __name__ == '__main__': main()
