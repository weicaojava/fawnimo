"""Advance one exact saved local-model bundle to a local candidate or separately enabled isolated check; no inference or GitHub writes."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sqlite3


def main():
    """Recover original candidate/check records before source/engine gates and require explicit repository/image choices for new local work."""
    parser=argparse.ArgumentParser(description='Fawnimo local model/candidate/check coordination')
    parser.add_argument('--model-root',type=Path,required=True)
    parser.add_argument('--changes-root',type=Path,required=True)
    parser.add_argument('--request-file',type=Path,required=True)
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--root',type=Path)
    parser.add_argument('--repository',type=Path)
    parser.add_argument('--image-id')
    parser.add_argument('--check-key')
    parser.add_argument('--initialize-checks',action='store_true')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare-candidate',action='store_true')
    action.add_argument('--run-check',action='store_true')
    args=parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_model_code import CommunityCodeModelRequest
    from app.core.contracts.community_code_checks import CommunityCodeCheckInput
    from app.services.community.advice_child import strict_json
    from app.services.community.advice_store import require_separate_roots
    from app.services.community.store import CommunityRequestStore
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.code_check_store import CommunityCodeCheckStore
    from app.services.community.candidate_git import CandidateGitRepository
    from app.services.community.development_model_store import CommunityCodeModelStore
    from app.services.community.development_model_flow import linked_artifact,prepare_candidate,check_candidate
    try:
        with args.request_file.open('rb') as stream:raw=stream.read(8193)
        if len(raw)>8192:raise ValueError('Original model request exceeds the bounded profile')
        body=CommunityCodeModelRequest.model_validate(strict_json(raw))
        selected=[value for value in (args.root,args.changes_root,args.model_root) if value is not None]
        for index,left in enumerate(selected):
            for right in selected[index+1:]:require_separate_roots(left,right)
        config=CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        journal=CommunityCodeModelStore(args.model_root,args.repository_id,args.repository_name)
        changes=CommunityChangeStore(args.changes_root,config,initialize=False)
        _original,artifact=linked_artifact(changes,journal,body)
        checks=None;check_body=None;old_check=None
        if args.run_check:
            if args.check_key is None or args.image_id is None:raise ValueError('Explicit original check key and immutable image required')
            check_body=CommunityCodeCheckInput(request_key=args.check_key,bundle_id=artifact['bundle_id'])
            if re.fullmatch(r'sha256:[a-f0-9]{64}',args.image_id) is None:raise ValueError('Immutable local image ID required')
            checks=CommunityCodeCheckStore(changes,initialize=args.initialize_checks)
            old_check=checks.replay(check_body,args.image_id)
        elif args.initialize_checks:raise ValueError('Check initialization belongs to the explicit isolated-check action')
        if old_check is not None:
            result=asyncio.run(check_candidate(None,changes,journal,body,checks,args.check_key,args.image_id))
        else:
            if args.repository is None:raise ValueError('Explicit original public Git clone required')
            repository=CandidateGitRepository(repository=args.repository)
            existing=repository.recover(artifact)
            preparation=None
            if existing is None or args.run_check:
                if args.root is None:raise ValueError('Fresh original preparation required for new local work')
                intake=CommunityRequestStore(args.root,config,initialize=False)
                preparation=CommunityPreparationStore(CommunityQueueStore(intake))
            if args.run_check:
                result=asyncio.run(check_candidate(preparation,changes,journal,body,checks,args.check_key,args.image_id,repository=repository))
            else:
                result=asyncio.run(prepare_candidate(preparation,changes,journal,body,repository=repository))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,TypeError,OSError,sqlite3.Error,asyncio.TimeoutError,RecursionError):
        parser.exit(1,'Local advancement not completed: inspect the original model/bundle/candidate/check records, exact source and explicit local repository/image. Existing history and reservations remain; no model request or GitHub write is started.\n')


if __name__=='__main__':main()
