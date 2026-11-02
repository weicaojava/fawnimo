"""Explicitly install supplemental immutable-row guards on one known dedicated community database, without rewriting data."""
import argparse
import json
from pathlib import Path
import sqlite3


def main():
    """Accept an exact existing public source/root only; normal service construction never silently accepts legacy guards."""
    parser=argparse.ArgumentParser(description='Upgrade dedicated community receipt guards')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--repository-id',type=int,required=True)
    parser.add_argument('--proposal-label',default='enhancement')
    args=parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.store import CommunityRequestStore
    try:
        config=CommunityIntakeConfiguration(repository_id=args.repository_id,repository_name=args.repository_name,proposal_label=args.proposal_label)
        store=CommunityRequestStore(args.root,config,initialize=False,allow_legacy_rowid_guards=True)
        print(json.dumps(store.upgrade_rowid_guards(),sort_keys=True))
    except (ValueError,OSError,sqlite3.Error):
        parser.exit(1,'Upgrade stopped: verify the exact dedicated source configuration, known schema and consistent backup. Existing rows were not rewritten.\n')


if __name__=='__main__':main()
