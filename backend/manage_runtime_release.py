"""Explicit operator CLI for new private local stable/preview resources; no model, cloud, GitHub or public deployment authority."""
import argparse
import json
import os
from pathlib import Path


def main():
    """Keep activation disabled until an explicit private policy is supplied; original-key replay never implicitly repeats work."""
    os.environ['JELLYFISH_SKIP_ENV_FILE'] = '1'
    from app.services.community.runtime_release_store import RuntimeReleaseStore, RuntimeReleaseError
    from app.services.community.runtime_releases import LocalRuntimeManager
    from app.services.community.local_release_files import read_json
    from app.controlplane.maintenance import MaintenanceBusy
    parser = argparse.ArgumentParser(description='Manage new owned local Fawnimo stable/preview slots')
    parser.add_argument('--root', required=True, type=Path, help='New private operator journal, outside source and existing application data')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('status')
    claims = commands.add_parser('credentials')
    claims.add_argument('--slot', choices=('stable', 'preview'), required=True)
    claims.add_argument('--expected-generation', type=int, required=True)
    claims.add_argument('--output', type=Path, required=True, help='New file in an existing private directory outside source/Git/journal')
    policy = commands.add_parser('policy')
    policy.add_argument('--input', required=True, type=Path)
    policy.add_argument('--expected-revision', required=True, type=int)
    policy.add_argument('--request-key', required=True)
    for name in ('activate', 'rollback', 'stop', 'quiesce'):
        child = commands.add_parser(name)
        child.add_argument('--input', required=True, type=Path, help='Strict original intent JSON; no credential or data-root fields')
    for name in ('show', 'reconcile'):
        child = commands.add_parser(name)
        child.add_argument('--id', required=True)
    args = parser.parse_args()
    try:
        store = RuntimeReleaseStore(args.root, initialize=args.command == 'init')
        manager = LocalRuntimeManager(store)
        if args.command == 'init': result = {'initialized': True, 'activation_enabled': False, 'daemon_contacted': False}
        elif args.command == 'status':
            revision, current, _ = store.policy()
            result = {'policy_revision': revision, 'enabled': current.enabled,
                      'slots': [store.slot(name) for name in ('stable', 'preview')], 'daemon_contacted': False}
        elif args.command == 'policy':
            result = store.set_policy(read_json(args.input), expected_revision=args.expected_revision, request_key=args.request_key)
        elif args.command == 'show': result = store.get(args.id)
        elif args.command == 'credentials': result = manager.export_initial_claims(args.slot, args.expected_generation, args.output)
        elif args.command == 'reconcile': result = manager.reconcile(args.id)
        else: result = getattr(manager, args.command)(read_json(args.input))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except (ValueError, OSError, RuntimeReleaseError, MaintenanceBusy):
        # Neither pydantic validation nor daemon exceptions may echo input
        # values, private configuration, credentials or source data.
        parser.exit(1, 'Local runtime operation refused or interrupted; preserve its private journal and use show/reconcile.\n')


if __name__ == '__main__': main()
