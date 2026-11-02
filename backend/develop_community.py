"""Save/read inert public-source proposals and local candidate commits; there is no code executor, Git push, PR or deployment switch."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Keep the proposal store separate, recover original keys before reopening source roots, and verify public sources for new proposals."""
    parser = argparse.ArgumentParser(description='Fawnimo inert public-code proposals (no code execution or GitHub writes)')
    parser.add_argument('--changes-root', type=Path, required=True)
    parser.add_argument('--repository-id', type=int, required=True)
    parser.add_argument('--repository-name', default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label', default='enhancement')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--initialize-changes', action='store_true')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare-file', type=Path)
    action.add_argument('--get-bundle')
    action.add_argument('--history', metavar='WORK_ID')
    action.add_argument('--create-local-commit', metavar='BUNDLE_ID')
    args = parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_changes import CommunityChangeInput
    from app.services.community.events import unique_fields, invalid_number, finite_float
    from app.services.community.store import CommunityRequestStore, CommunityStoreConflict
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.change_preparation import assemble_current_change, commit_current_change
    try:
        config = CommunityIntakeConfiguration(repository_id=args.repository_id, repository_name=args.repository_name, proposal_label=args.proposal_label)
        # Reject overlapping explicit roots before creating the new store; an
        # intake directory must never be modified into an unsupported mixture.
        if args.root:
            left, right = args.root.absolute(), args.changes_root.absolute()
            if left.resolve() != left or right.resolve() != right or left.is_relative_to(right) or right.is_relative_to(left):
                raise CommunityStoreConflict('Dedicated source/change roots overlap or redirect')
        changes = CommunityChangeStore(args.changes_root, config, initialize=args.initialize_changes)
        if args.get_bundle:
            result = changes.get(args.get_bundle)
        elif args.history:
            result = changes.history(args.history)
        elif args.create_local_commit:
            # Recovery intentionally precedes opening the old intake root.
            # New creation then checks its exact source and prepared scope.
            from app.services.community.candidate_git import CandidateGitRepository
            candidate = CandidateGitRepository()
            artifact = changes.get(args.create_local_commit)
            original = candidate.recover(artifact)
            if original is not None:
                result = {'candidate': original.receipt(), 'replayed': True}
            else:
                if args.root is None:
                    raise CommunityStoreConflict('New local candidates require the exact source root')
                intake = CommunityRequestStore(args.root, config, initialize=False)
                preparation = CommunityPreparationStore(CommunityQueueStore(intake))
                result = asyncio.run(commit_current_change(preparation, changes, args.create_local_commit, repository=candidate))
        else:
            with args.prepare_file.open('rb') as stream:
                raw = stream.read(2097153)
            if len(raw) > 2097152:
                raise CommunityStoreConflict('Original change input exceeds2MiB')
            value = json.loads(raw.decode('utf8'), object_pairs_hook=unique_fields, parse_constant=invalid_number, parse_float=finite_float)
            body = CommunityChangeInput.model_validate(value)
            result = changes.replay(body)
            if result is None:
                if args.root is None:
                    raise CommunityStoreConflict('New proposals require their exact existing source root')
                intake = CommunityRequestStore(args.root, config, initialize=False)
                preparation = CommunityPreparationStore(CommunityQueueStore(intake))
                result = asyncio.run(assemble_current_change(preparation, changes, body))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, sqlite3.Error, asyncio.TimeoutError, RecursionError):
        parser.exit(1, 'Change not saved: check exact original source/preparation, public code hashes, separate roots and bounded input. Existing proposals are retained.\n')


if __name__ == '__main__':
    main()
