"""Explicitly publish one saved-model checked draft or observe its review through existing separate policies; no inference, merge or deployment."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sqlite3
from observe_community_pr import read_json


def main():
    """Derive exact inputs from original saved records and recover before discovering source/Git/credentials; initialization remains in existing policy CLIs."""
    parser=argparse.ArgumentParser(description='Fawnimo original local-model draft/review coordination')
    for name in ('model-root','changes-root','publication-root','request-file'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--check-key',required=True)
    parser.add_argument('--image-id',required=True)
    parser.add_argument('--publication-key',required=True)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--repository',type=Path)
    parser.add_argument('--credential-file',type=Path)
    parser.add_argument('--gates-root',type=Path)
    parser.add_argument('--observation-key')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--publish-draft',action='store_true')
    action.add_argument('--observe-review',action='store_true')
    args=parser.parse_args()
    from app.core.contracts.community_model_code import CommunityCodeModelRequest
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.advice_store import require_separate_roots
    from app.services.community.development_model_store import CommunityCodeModelStore
    from app.services.community.development_model_delivery import publication_input,review_input,publish_model_candidate,observe_model_review
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.code_check_store import CommunityCodeCheckStore
    from app.services.community.publication_store import CommunityPublicationStore
    from app.services.community.review_gate_store import CommunityReviewGateStore
    from app.services.community.candidate_git import CandidateGitRepository
    from app.services.community.store import CommunityRequestStore
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    from app.services.community.publication_transport import GitHubPublicationTransport
    try:
        if re.fullmatch(r'sha256:[a-f0-9]{64}',args.image_id) is None:raise ValueError('Explicit immutable check image required')
        body=CommunityCodeModelRequest.model_validate(read_json(args.request_file,8192))
        roots=[value for value in (args.model_root,args.changes_root,args.publication_root,args.gates_root,args.root) if value is not None]
        for index,left in enumerate(roots):
            for right in roots[index+1:]:require_separate_roots(left,right)
        config=CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        journal=CommunityCodeModelStore(args.model_root,args.repository_id,args.repository_name)
        changes=CommunityChangeStore(args.changes_root,config,initialize=False);checks=CommunityCodeCheckStore(changes)
        publications=CommunityPublicationStore(args.publication_root)
        common=(changes,journal,body,checks,publications)
        gates=None
        if args.publish_draft:
            artifact,intent=publication_input(*common,args.check_key,args.image_id,args.publication_key)
            old=publications.replay(intent);needs_fresh=old is None
            if needs_fresh and not publications.policy.enabled:raise ValueError('Remote publication is disabled')
            if needs_fresh and args.credential_file is None:raise ValueError('Explicit dedicated publication credential required')
        else:
            if args.gates_root is None or args.observation_key is None:raise ValueError('Explicit original gate root and observation key required')
            gates=CommunityReviewGateStore(args.gates_root)
            artifact,intent=review_input(*common,gates,args.check_key,args.image_id,args.publication_key,args.observation_key,allow_history=True)
            old=gates.replay(intent);needs_fresh=old is None and gates.policy.enabled
            if old is None:review_input(*common,gates,args.check_key,args.image_id,args.publication_key,args.observation_key)
        preparation=None;repository=None
        if needs_fresh:
            if args.root is None or args.repository is None:raise ValueError('Fresh source/preparation and explicit public clone required')
            intake=CommunityRequestStore(args.root,config,initialize=False)
            preparation=CommunityPreparationStore(CommunityQueueStore(intake));repository=CandidateGitRepository(repository=args.repository)
        if args.publish_draft:
            result=asyncio.run(publish_model_candidate(preparation,*common,args.check_key,args.image_id,args.publication_key,repository=repository,
                remote_factory=lambda policy:GitHubPublicationTransport(policy,args.credential_file)))
        else:
            result=asyncio.run(observe_model_review(preparation,*common,gates,args.check_key,args.image_id,args.publication_key,args.observation_key,repository=repository))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,TypeError,OSError,sqlite3.Error,RuntimeError,RecursionError):
        parser.exit(1,'Original delivery not completed. Inspect the exact saved model/bundle/check/publication records and separate enabled policies. Reconcile unknown writes read-only; no model, merge or deployment is started. Credentials and remote prose are not printed.\n')


if __name__=='__main__':main()
