"""Fresh source/preparation/check admission before explicit trusted remote publication; original history is always recovered first."""
from contextlib import closing
import hashlib
import json
import time
from app.core.contracts.community_changes import CommunityChangeInput
from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.change_bundles import build_change_bundle,verify_public_snapshot
from app.services.community.change_preparation import current_prepared_work
from app.services.community.code_check_runtime import RUNNER
from app.services.community.evaluations import digest,source_row,validate_proof
from app.services.community.public_code import verify_public_code,PublicCodeObservation,CODE_PROTOCOL
from app.services.community.public_source import verify_public_issue
from app.services.community.publication_objects import publication_objects
from app.services.community.publication_runtime import publish_candidate
from app.services.community.review_gate_admission import owned_public_sources
from app.services.community.review_gate_decisions import LocalPRGateFacts,validate_local_check
from app.services.community.store import CommunityStoreConflict


async def publish_current_candidate(preparation,checks,publications,body,*,remote_factory,repository=None):
    """No new credentials/Git/network work for original recovery or disabled publication; source reads and all GitHub writes run outside database writers."""
    old=publications.replay(body)
    if old is not None: return old
    if not publications.policy.enabled: raise CommunityStoreConflict('Remote publication is disabled')
    if preparation is None or checks is None: raise CommunityStoreConflict('Original source/preparation and cleaned checks required')
    intake=preparation.intake; changes=checks.changes; policy=publications.policy; roots=(intake.root,changes.root,publications.root)
    if (intake.config.repository_id!=policy.repository_id or changes.config.repository_id!=policy.repository_id
            or intake.config.repository_name.casefold()!=policy.repository_name or changes.config.repository_name.casefold()!=policy.repository_name
            or any(left.is_relative_to(right) or right.is_relative_to(left) for index,left in enumerate(roots) for right in roots[index+1:])):
        raise CommunityStoreConflict('Publication roots differ from the dedicated original repository scope')
    artifact=changes.get(body.bundle_id); bundle=artifact['bundle']; scope=bundle['scope']; repo=repository or CandidateGitRepository()
    candidate=repo.recover(artifact)
    if candidate is None: raise CommunityStoreConflict('Exact original candidate required')
    facts=LocalPRGateFacts(candidate,checks.get(body.check_attempt_id),hashlib.sha256(RUNNER.read_bytes()).hexdigest(),repo.workflow_blobs(scope['base_tree'],candidate.tree))
    validate_local_check(body,artifact,facts,policy)

    async def revalidate():
        """Before each active remote step recheck current selected provenance, public source/main/catalog and the immutable cleaned exact check."""
        with closing(intake.connect()) as db:
            prepared=current_prepared_work(preparation,db,scope['work_id']); source=source_row(db,prepared['work']['entry']['version_id'])
        issue,code=await owned_public_sources(verify_public_issue(intake.config,source['issue_number']),
            verify_public_code(intake.config,prepared['work']['entry']['feature_ids'],[item['path'] for item in bundle['files']]))
        registry=json.loads(code.snapshot.files['capabilities/features.json'].decode('utf8'))
        with closing(intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                current=current_prepared_work(preparation,db,scope['work_id']); now=time.time_ns()//1000
                validate_proof(intake.config,source_row(db,current['work']['entry']['version_id']),issue,now)
                if (not isinstance(code,PublicCodeObservation) or code.protocol!=CODE_PROTOCOL or type(code.checked_at_us) is not int or not 0<=now-code.checked_at_us<=60000000
                        or digest(registry)!=digest(preparation.queue.catalog()) or code.snapshot.repository_id!=scope['repository_id']
                        or code.snapshot.repository_name.casefold()!=scope['repository_name'] or code.snapshot.commit!=candidate.parent or code.snapshot.tree!=scope['base_tree']
                        or verify_public_snapshot(code.snapshot)!=bundle['base_code_manifest']
                        or build_change_bundle(CommunityChangeInput.model_validate(bundle['input']),current,code.snapshot,registry,observations=bundle['source_observations'])!=artifact):
                    raise CommunityStoreConflict('Original publication source/selection/main/catalog no longer current')
                db.commit()
            except BaseException: db.rollback(); raise
        validate_local_check(body,artifact,LocalPRGateFacts(candidate,checks.get(body.check_attempt_id),hashlib.sha256(RUNNER.read_bytes()).hexdigest(),repo.workflow_blobs(scope['base_tree'],candidate.tree)),policy)
    await revalidate()
    objects=publication_objects(repo,artifact)
    # Credentials remain in the trusted host transport. No candidate process,
    # SQLite writer, model adapter or public issue receives the credential.
    remote=remote_factory(policy)
    return await publish_candidate(publications,body,artifact,facts,objects,remote,revalidate=revalidate)
