"""Request or recover advisory assessment from an explicitly configured local maintainer model, with no paid or publication actions."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Keep intake and advice roots separate; existing original requests replay without a current model configuration."""
    parser = argparse.ArgumentParser(description='Fawnimo local-maintainer model advice')
    parser.add_argument('--intake-root',type=Path,required=True)
    parser.add_argument('--advice-root',type=Path,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--initialize-advice',action='store_true')
    parser.add_argument('--request-file',type=Path,required=True)
    parser.add_argument('--configuration-file',type=Path)
    parser.add_argument('--history-only',action='store_true')
    args = parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_advice import CommunityAdviceRequest
    from app.services.community.store import CommunityRequestStore
    from app.services.community.advice_store import CommunityAdviceStore,require_separate_roots
    from app.services.community.advice_child import strict_json
    from app.services.community.advice import create_advice,current_source
    try:
        raw = args.request_file.read_bytes()
        if len(raw) > 8192:
            raise ValueError('Advice request is too large')
        request = CommunityAdviceRequest.model_validate(strict_json(raw))
        require_separate_roots(args.intake_root,args.advice_root)
        config = CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        intake = CommunityRequestStore(args.intake_root,config,initialize=False)
        journal = CommunityAdviceStore(args.advice_root,args.repository_id,args.repository_name,initialize=args.initialize_advice)
        if args.history_only:
            result = journal.replay(request)
            if result is None:
                raise ValueError('Original advice request not found')
            result = {**result,'source_current':current_source(intake,request)}
        else:
            result = asyncio.run(create_advice(intake,journal,request,args.configuration_file))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,TypeError,OSError,sqlite3.Error,asyncio.TimeoutError):
        parser.exit(1,'Local model advice unavailable: check the explicit configuration, original public source and dedicated journal. Original requests are retained and are never automatically resent.\n')


if __name__ == '__main__':
    main()
