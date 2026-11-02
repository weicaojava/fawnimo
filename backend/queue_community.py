"""Local versioned roadmap and preparation reservations; never run code, call a model or post public feedback."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3


def main():
    """Use existing dedicated public-request data and strict bounded JSON; new claim/success verifies the fixed public source."""
    parser = argparse.ArgumentParser(description='Fawnimo local roadmap and preparation leases (no development executor)')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repository-name', default='weicaojava/fawnimo')
    parser.add_argument('--repository-id', type=int, required=True)
    parser.add_argument('--proposal-label', default='enhancement')
    parser.add_argument('--initialize-queue', action='store_true')
    parser.add_argument('--initialize-preparation', action='store_true')
    action = parser.add_mutually_exclusive_group(required=True)
    for name in ('plan', 'selection', 'materialize', 'claim', 'heartbeat', 'finish', 'cancel', 'recover'):
        action.add_argument('--' + name + '-file', type=Path)
    action.add_argument('--get-plan', metavar='PLAN_ID')
    action.add_argument('--list-work', metavar='PLAN_ID')
    action.add_argument('--get-work', metavar='WORK_ID')
    action.add_argument('--history', metavar='WORK_ID')
    args = parser.parse_args()
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.core.contracts.community_queue import (CommunityQueuePlanInput, QueueSelectionInput, QueueMaterializeInput, QueueClaimInput,
        QueueLeaseInput, QueueFinishInput, QueueCancelInput, QueueWorkInput)
    from app.services.community.public_source import parse_public_json
    from app.services.community.store import CommunityRequestStore, CommunityStoreConflict
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore, claim_current_public_source, finish_current_public_source

    def read(path, contract):
        """Reject ambiguous JSON, extra fields and oversized intents; no client field supplies source proof or commands."""
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise CommunityStoreConflict('Queue input exceeds64KiB')
        return contract.model_validate(parse_public_json(raw))

    try:
        config = CommunityIntakeConfiguration(repository_id=args.repository_id, repository_name=args.repository_name, proposal_label=args.proposal_label)
        intake = CommunityRequestStore(args.root, config, initialize=False)
        queue = CommunityQueueStore(intake, initialize=args.initialize_queue)
        if args.plan_file:
            result = queue.create_plan(read(args.plan_file, CommunityQueuePlanInput))
        elif args.selection_file:
            body = read(args.selection_file, QueueSelectionInput)
            result = queue.activate(body.plan_id, body.request_key, body.expected_revision)
        elif args.get_plan:
            result = queue.get_plan(args.get_plan)
        else:
            store = CommunityPreparationStore(queue, initialize=args.initialize_preparation)
            if args.materialize_file:
                result = store.materialize(read(args.materialize_file, QueueMaterializeInput))
            elif args.claim_file:
                result = asyncio.run(claim_current_public_source(store, read(args.claim_file, QueueClaimInput)))
            elif args.heartbeat_file:
                result = store.heartbeat(read(args.heartbeat_file, QueueLeaseInput))
            elif args.finish_file:
                result = asyncio.run(finish_current_public_source(store, read(args.finish_file, QueueFinishInput)))
            elif args.cancel_file:
                result = store.cancel(read(args.cancel_file, QueueCancelInput))
            elif args.recover_file:
                result = store.recover(read(args.recover_file, QueueWorkInput))
            elif args.list_work:
                result = store.list_work(args.list_work)
            elif args.get_work:
                result = store.get_work(args.get_work)
            else:
                result = store.history(args.history)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, sqlite3.Error, asyncio.TimeoutError):
        parser.exit(1, 'Queue operation not committed: check original keys, exact plan/selection/source, current lease and finite limits. Saved history is retained.\n')


if __name__ == '__main__':
    main()
