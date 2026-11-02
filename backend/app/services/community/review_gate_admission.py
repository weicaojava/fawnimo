"""Observe independent review/CI only after current exact local check and source selection, preserving no-action historical recovery."""
import asyncio
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
from app.services.community.review_gate_decisions import LocalPRGateFacts,validate_local_check
from app.services.community.review_gate_source import verify_public_pr
from app.services.community.store import CommunityStoreConflict


async def owned_public_sources(*coroutines):
    """Join every owned source helper after success, first failure or repeated cancellation; a persistent event loop must not retain detached reads."""
    tasks=[asyncio.create_task(coroutine) for coroutine in coroutines]
    try: return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        async def drain():
            """Existing process helpers shield their kernel cleanup; wait for every coroutine to finish before returning the first failure."""
            return await asyncio.gather(*tasks,return_exceptions=True)
        cleaning=asyncio.create_task(drain()); cancelled=None
        while not cleaning.done():
            try: await asyncio.shield(cleaning)
            except asyncio.CancelledError as error: cancelled=error
        cleaning.result()
        if cancelled is not None: raise cancelled


async def observe_current_pr_gate(preparation,checks,gates,body,*,repository=None):
    """Recover original keys before all source/Git/network work; explicitly enabled new observations recheck source/prepared scope before a separate audit append."""
    original=gates.replay(body)
    if original is not None: return original
    if not gates.policy.enabled: return gates.record(body)
    if preparation is None or checks is None: raise CommunityStoreConflict('Enabled gates require exact original source and owned-check roots')
    intake=preparation.intake; changes=checks.changes; roots=(intake.root,changes.root,gates.root); policy=gates.policy
    if (intake.config.repository_id!=policy.repository_id or changes.config.repository_id!=policy.repository_id
            or intake.config.repository_name.casefold()!=policy.repository_name or changes.config.repository_name.casefold()!=policy.repository_name
            or any(left.is_relative_to(right) or right.is_relative_to(left) for index,left in enumerate(roots) for right in roots[index+1:])):
        raise CommunityStoreConflict('Gate/source/change roots differ from their dedicated repository scope')
    artifact=changes.get(body.bundle_id); payload=artifact['bundle']; scope=payload['scope']; candidate_repository=repository or CandidateGitRepository()
    candidate=candidate_repository.recover(artifact)
    if candidate is None: raise CommunityStoreConflict('Exact local candidate must already exist')
    facts=LocalPRGateFacts(candidate,checks.get(body.check_attempt_id),hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
        candidate_repository.workflow_blobs(scope['base_tree'],candidate.tree))
    validate_local_check(body,artifact,facts,policy)
    with closing(intake.connect()) as db:
        prepared=current_prepared_work(preparation,db,scope['work_id']); source=source_row(db,prepared['work']['entry']['version_id'])
    # Independent read-only children run concurrently so even a slow observer
    # does not normally age the other source proofs beyond their60-second cap.
    issue,code,pr=await owned_public_sources(verify_public_issue(intake.config,source['issue_number']),
        verify_public_code(intake.config,prepared['work']['entry']['feature_ids'],[item['path'] for item in payload['files']]),
        verify_public_pr(intake.config,body.pr_number))
    registry=json.loads(code.snapshot.files['capabilities/features.json'].decode('utf8'))
    if digest(registry)!=digest(preparation.queue.catalog()): raise CommunityStoreConflict('Installed/public feature register differs')
    with closing(intake.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            now=time.time_ns()//1000; current=current_prepared_work(preparation,db,scope['work_id'])
            validate_proof(intake.config,source_row(db,current['work']['entry']['version_id']),issue,now)
            if (not isinstance(code,PublicCodeObservation) or code.protocol!=CODE_PROTOCOL or type(code.checked_at_us) is not int
                    or not 0<=now-code.checked_at_us<=60000000 or code.snapshot.repository_id!=scope['repository_id']
                    or code.snapshot.repository_name.casefold()!=scope['repository_name'] or code.snapshot.commit!=scope['base_commit']
                    or code.snapshot.tree!=scope['base_tree'] or verify_public_snapshot(code.snapshot)!=payload['base_code_manifest']
                    or build_change_bundle(CommunityChangeInput.model_validate(payload['input']),current,code.snapshot,registry,
                        observations=payload['source_observations'])!=artifact):
                raise CommunityStoreConflict('Original public/prepared candidate is stale or changed')
            result=gates.record(body,artifact,facts,pr)
            db.commit(); return result
        except BaseException:
            db.rollback(); raise
