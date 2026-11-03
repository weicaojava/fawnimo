"""Explicit trusted-maintainer draft publication; disabled by default, with original recovery before source/credential work."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
from observe_community_pr import read_json


def main():
    """Keep publication credentials outside all stores/candidates; initialization/history do not discover or use any token."""
    parser=argparse.ArgumentParser(description='Publish one exact checked candidate draft, with durable unknown-write recovery')
    parser.add_argument('--publication-root',type=Path,required=True)
    parser.add_argument('--policy-file',type=Path)
    parser.add_argument('--credential-file',type=Path)
    parser.add_argument('--repository-id',type=int)
    parser.add_argument('--repository-name',default='weicaojava/fawnimo')
    parser.add_argument('--proposal-label',default='enhancement')
    parser.add_argument('--root',type=Path)
    parser.add_argument('--changes-root',type=Path)
    parser.add_argument('--repository',type=Path)
    actions=parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--initialize-publication',action='store_true')
    actions.add_argument('--publish-file',type=Path)
    actions.add_argument('--get-publication',metavar='PUBLICATION_ID')
    actions.add_argument('--reconcile-publication',metavar='PUBLICATION_ID')
    args=parser.parse_args()
    from app.core.contracts.community_publication import CommunityPublicationPolicy,CommunityPublicationInput
    from app.core.contracts.community_requests import CommunityIntakeConfiguration
    from app.services.community.publication_store import CommunityPublicationStore
    from app.services.community.publication_admission import publish_current_candidate
    from app.services.community.publication_runtime import reconcile_publication
    from app.services.community.publication_transport import GitHubPublicationTransport
    from app.services.community.change_store import CommunityChangeStore
    from app.services.community.code_check_store import CommunityCodeCheckStore
    from app.services.community.queue_store import CommunityQueueStore
    from app.services.community.queue_leases import CommunityPreparationStore
    from app.services.community.store import CommunityRequestStore,CommunityStoreConflict
    from app.services.community.candidate_git import CandidateGitRepository
    try:
        policy=CommunityPublicationPolicy.model_validate(read_json(args.policy_file,8192)) if args.policy_file else None
        if args.initialize_publication:
            if args.root is None or args.changes_root is None: raise CommunityStoreConflict('Explicit separate source/change roots required')
            if policy is None:
                if args.repository_id is None: raise CommunityStoreConflict('Explicit repository identity required')
                policy=CommunityPublicationPolicy(repository_id=args.repository_id,repository_name=args.repository_name)
        companions=(args.root,args.changes_root) if args.initialize_publication else ()
        publications=CommunityPublicationStore(args.publication_root,policy,initialize=args.initialize_publication,isolated_from=companions)
        if args.initialize_publication: result={'policy':publications.policy.model_dump(mode='json'),'network_started':False,'remote_write_started':False}
        elif args.get_publication: result=publications.get(args.get_publication)
        elif args.reconcile_publication:
            if args.credential_file is None: raise CommunityStoreConflict('Explicit dedicated GitHub credential file required')
            result=reconcile_publication(publications,args.reconcile_publication,GitHubPublicationTransport(publications.policy,args.credential_file))
        else:
            body=CommunityPublicationInput.model_validate(read_json(args.publish_file,4096)); result=publications.replay(body)
            if result is None:
                if not publications.policy.enabled: raise CommunityStoreConflict('Remote publication is disabled')
                if args.root is None or args.changes_root is None or args.credential_file is None: raise CommunityStoreConflict('Explicit source/check roots and dedicated GitHub credential required')
                config=CommunityIntakeConfiguration(repository_id=publications.policy.repository_id,repository_name=publications.policy.repository_name,proposal_label=args.proposal_label)
                intake=CommunityRequestStore(args.root,config,initialize=False); changes=CommunityChangeStore(args.changes_root,config)
                preparation=CommunityPreparationStore(CommunityQueueStore(intake)); checks=CommunityCodeCheckStore(changes)
                result=asyncio.run(publish_current_candidate(preparation,checks,publications,body,
                    remote_factory=lambda trusted:GitHubPublicationTransport(trusted,args.credential_file),
                    repository=CandidateGitRepository(repository=args.repository) if args.repository else None))
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError,sqlite3.Error,RuntimeError,RecursionError):
        parser.exit(1,'Publication unavailable. Recover the original record; use read-only reconcile for unknown writes. Verify separate roots, exact checked source and explicit immutable policy. No credentials or provider response are printed.\n')


if __name__=='__main__': main()
