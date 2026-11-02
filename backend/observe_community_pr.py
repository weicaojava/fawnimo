"""Explicit local-maintainer observation of exact public PR review/CI; no approval, comment, merge, paid call or deployment command exists."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def read_json(path,limit):
    """Bound local policy/request JSON and reject duplicate/nonfinite fields without reflecting sensitive input on failure."""
    from app.services.community.events import unique_fields,invalid_number,finite_float
    with path.open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('Bounded policy/request required')
    return json.loads(raw.decode('utf8'),object_pairs_hook=unique_fields,parse_constant=invalid_number,parse_float=finite_float)


def main():
    """Create a separate immutable policy root deliberately; original observations recover without current source/Git/reviewer configuration."""
    parser=argparse.ArgumentParser(description='Fawnimo read-only exact PR/review/CI observations; no merge/deploy authority')
    parser.add_argument('--gates-root',type=Path,required=True)
    parser.add_argument('--policy-file',type=Path)
    parser.add_argument('--repository-id',type=int)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--root',type=Path)
    parser.add_argument('--changes-root',type=Path)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--initialize-gates',action='store_true')
    action.add_argument('--evaluate-file',type=Path)
    action.add_argument('--get-observation',metavar='OBSERVATION_ID')
    action.add_argument('--history',metavar='BUNDLE_ID')
    args=parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_review_gates import CommunityReviewGatePolicy,CommunityPRGateInput
    from app.services.community.review_gate_store import CommunityReviewGateStore
    from app.services.community.review_gate_admission import observe_current_pr_gate
    from app.services.community.store import CommunityRequestStore,CommunityStoreConflict
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.code_check_store import CommunityCodeCheckStore
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    try:
        policy=CommunityReviewGatePolicy.model_validate(read_json(args.policy_file,8192)) if args.policy_file else None
        if args.initialize_gates:
            if args.root is None or args.changes_root is None: raise CommunityStoreConflict('Explicit separate source/change roots required for gate initialization')
            if policy is None:
                if args.repository_id is None: raise CommunityStoreConflict('Repository identity required for default disabled gate policy')
                policy=CommunityReviewGatePolicy(repository_id=args.repository_id,repository_name=args.repository_name)
        companions=(args.root,args.changes_root) if args.initialize_gates else ()
        gates=CommunityReviewGateStore(args.gates_root,policy,initialize=args.initialize_gates,isolated_from=companions)
        if args.initialize_gates: result={'profile':gates.policy.profile,'policy':gates.policy.model_dump(),'network_observation_started':False,'action_authorized':False}
        elif args.get_observation: result=gates.get(args.get_observation)
        elif args.history: result=gates.history(args.history)
        else:
            body=CommunityPRGateInput.model_validate(read_json(args.evaluate_file,4096)); result=gates.replay(body)
            if result is None:
                if not gates.policy.enabled: result=gates.record(body)
                else:
                    if args.root is None or args.changes_root is None: raise CommunityStoreConflict('New enabled observations require original source/change/check roots')
                    config=CommunityIntakeConfiguration(repository_id=gates.policy.repository_id,repository_name=gates.policy.repository_name,proposal_label=args.proposal_label)
                    intake=CommunityRequestStore(args.root,config,initialize=False)
                    changes=CommunityChangeStore(args.changes_root,config)
                    checks=CommunityCodeCheckStore(changes)
                    preparation=CommunityPreparationStore(CommunityQueueStore(intake))
                    result=asyncio.run(observe_current_pr_gate(preparation,checks,gates,body))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError,sqlite3.Error,asyncio.TimeoutError,RecursionError,RuntimeError):
        parser.exit(1,'Observation not saved: verify exact original source/check/PR, separate immutable policy root and fresh public review/CI. Existing observations remain historical and grant no action authority.\n')


if __name__=='__main__': main()
