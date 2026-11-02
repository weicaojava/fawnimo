"""Inspect duplicate advice and record explicit local-maintainer evaluations; never post to GitHub or execute issue instructions."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Require an existing dedicated intake and exact public repository; new decisions always verify the live source."""
    parser=argparse.ArgumentParser(description='Fawnimo local public-requirement review')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--initialize-review',action='store_true')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--suggest',metavar='VERSION_ID')
    action.add_argument('--history',metavar='VERSION_ID')
    action.add_argument('--evaluate-file',type=Path)
    args=parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_evaluations import CommunityEvaluationInput
    from app.services.community.store import CommunityRequestStore, CommunityStoreConflict
    from app.services.community.evaluations import CommunityEvaluationStore, evaluate_current_public_source
    from app.services.community.public_source import parse_public_json
    try:
        config=CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        intake=CommunityRequestStore(args.root,config,initialize=False)
        store=CommunityEvaluationStore(intake,initialize=args.initialize_review)
        if args.suggest:result=store.suggestions(args.suggest)
        elif args.history:result=store.history(args.history)
        else:
            with args.evaluate_file.open('rb') as stream:raw=stream.read(32769)
            if len(raw)>32768:raise CommunityStoreConflict('Evaluation input exceeds32KiB')
            body=CommunityEvaluationInput.model_validate(parse_public_json(raw))
            result=asyncio.run(evaluate_current_public_source(store,body))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError,sqlite3.Error,asyncio.TimeoutError):
        parser.exit(1,'Review not saved: check the exact source version, public availability, review input and original request key. Existing history is retained.\n')


if __name__=='__main__':main()
