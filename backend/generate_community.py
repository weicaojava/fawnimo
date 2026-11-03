"""Generate/recover inert local-model Python proposals for exact prepared public work; no candidate execution, Git push, PR or deployment action."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Use three dedicated roots and explicit local-model configuration; history or original ambiguous/bundled requests recover before source setup."""
    parser=argparse.ArgumentParser(description='Fawnimo explicit local-model code proposals')
    parser.add_argument('--model-root',type=Path,required=True)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--changes-root',type=Path)
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--request-file',type=Path,required=True)
    parser.add_argument('--configuration-file',type=Path)
    parser.add_argument('--initialize-model',action='store_true')
    parser.add_argument('--initialize-changes',action='store_true')
    parser.add_argument('--history-only',action='store_true')
    args=parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_model_code import CommunityCodeModelRequest
    from app.services.community.advice_child import strict_json
    from app.services.community.advice_store import require_separate_roots
    from app.services.community.store import CommunityRequestStore
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.development_model_store import CommunityCodeModelStore
    from app.services.community.development_model import generate_original,recover_original_bundle
    try:
        raw=args.request_file.read_bytes()
        if len(raw)>8192:raise ValueError('Original local code-model request exceeds its bounded profile')
        body=CommunityCodeModelRequest.model_validate(strict_json(raw))
        config=CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        selected=[path for path in (args.root,args.changes_root,args.model_root) if path is not None]
        for index,left in enumerate(selected):
            for right in selected[index+1:]:require_separate_roots(left,right)
        journal=CommunityCodeModelStore(args.model_root,args.repository_id,args.repository_name,initialize=args.initialize_model)
        original=journal.replay(body)
        if args.history_only:
            if original is None:raise ValueError('Original local code-model request not found')
            result=original
        elif original is not None and (original['bundle'] is not None or original['model_result'] is None or original['model_result']['state']!='generated'):
            result=original
        else:
            if args.changes_root is None:raise ValueError('Original separate changes root required')
            changes=CommunityChangeStore(args.changes_root,config,initialize=args.initialize_changes)
            recovered=recover_original_bundle(changes,journal,body) if original is not None else None
            if recovered is not None:result=recovered
            else:
                if args.root is None:raise ValueError('New proposal materialization requires its original prepared source root')
                intake=CommunityRequestStore(args.root,config,initialize=False)
                preparation=CommunityPreparationStore(CommunityQueueStore(intake))
                result=asyncio.run(generate_original(preparation,changes,journal,body,args.configuration_file))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,TypeError,OSError,sqlite3.Error,asyncio.TimeoutError,RecursionError):
        parser.exit(1,'Local code proposal unavailable: original model results are retained; inspect the same request with --history-only before a new action. Check current preparation/public code and separate roots. No ambiguous model request is automatically resent.\n')


if __name__=='__main__':main()
