"""Observe exact public source before saving inert proposals, retaining original-key recovery without granting code execution."""
from contextlib import closing
import json
import time

from app.services.community.change_bundles import build_change_bundle
from app.services.community.evaluations import digest, source_row, validate_proof
from app.services.community.public_source import verify_public_issue
from app.services.community.public_code import verify_public_code
from app.services.community.store import CommunityStoreConflict


def current_prepared_work(preparation, db, work_id):
    """Only the exact currently selected prepared provenance may produce a new bundle; prior artifacts stay separately recoverable."""
    value = preparation._view(db, work_id)
    if (value['current']['state'] != 'prepared' or not value['clock_valid']
            or not preparation._scope_current(db, value['work'])):
        raise CommunityStoreConflict('Original prepared scope is no longer current')
    return value


async def assemble_current_change(preparation, changes, body):
    """Observe GitHub outside writers, then recheck original source/plan under its writer before appending to the separate inert store."""
    original = changes.replay(body)
    if original is not None:
        return original
    intake = preparation.intake
    if changes.config.repository_id != intake.config.repository_id or changes.config.repository_name.casefold() != intake.config.repository_name.casefold():
        raise CommunityStoreConflict('Public change and intake roots belong to different sources')
    if changes.root.is_relative_to(intake.root) or intake.root.is_relative_to(changes.root):
        raise CommunityStoreConflict('Public change and intake roots must be separate dedicated directories')
    with closing(intake.connect()) as db:
        prepared = current_prepared_work(preparation, db, body.work_id)
        source = source_row(db, prepared['work']['entry']['version_id'])
    issue_observation = await verify_public_issue(intake.config, source['issue_number'])
    code_observation = await verify_public_code(intake.config, prepared['work']['entry']['feature_ids'], [change.path for change in body.changes])
    registry = json.loads(code_observation.snapshot.files['capabilities/features.json'].decode('utf8'))
    if digest(registry) != digest(preparation.queue.catalog()):
        raise CommunityStoreConflict('Public main and installed controller use different feature-register versions')
    with closing(intake.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            prepared = current_prepared_work(preparation, db, body.work_id)
            # This source transaction appends no source data. It holds the
            # checked original selection stable while the separate inert store
            # appends its recoverable receipt in a fixed lock order.
            now = time.time_ns() // 1000
            validate_proof(intake.config, source_row(db, prepared['work']['entry']['version_id']), issue_observation, now)
            if (type(code_observation.checked_at_us) is not int or not 0 <= now - code_observation.checked_at_us <= 60000000
                    or code_observation.snapshot.repository_id != intake.config.repository_id
                    or code_observation.snapshot.repository_name.casefold() != intake.config.repository_name.casefold()):
                raise CommunityStoreConflict('Public code observation expired or belongs to another source')
            artifact = build_change_bundle(body, prepared, code_observation.snapshot, registry,
                observations={'issue': issue_observation.receipt(), 'code': code_observation.receipt()})
            result = changes.record(body, artifact)
            db.commit()
            return result
        except BaseException:
            db.rollback(); raise


async def commit_current_change(preparation, changes, identifier, *, repository=None):
    """Create only a local candidate ref after fresh source checks; original Git-object recovery precedes all new-source gates."""
    from app.services.community.candidate_git import CandidateGitRepository
    artifact = changes.get(identifier)
    candidate = repository or CandidateGitRepository()
    original = candidate.recover(artifact)
    if original is not None:
        return {'candidate': original.receipt(), 'replayed': True}
    if preparation is None:
        raise CommunityStoreConflict('New local candidates require current prepared provenance')
    intake = preparation.intake
    if (changes.config.repository_id != intake.config.repository_id or changes.config.repository_name.casefold() != intake.config.repository_name.casefold()
            or changes.root.is_relative_to(intake.root) or intake.root.is_relative_to(changes.root)):
        raise CommunityStoreConflict('Original source/change roots do not match their dedicated scope')
    work_id = artifact['bundle']['input']['work_id']
    with closing(intake.connect()) as db:
        prepared = current_prepared_work(preparation, db, work_id)
        source = source_row(db, prepared['work']['entry']['version_id'])
    issue_observation = await verify_public_issue(intake.config, source['issue_number'])
    code_observation = await verify_public_code(intake.config, prepared['work']['entry']['feature_ids'],
        [item['path'] for item in artifact['bundle']['files']])
    with closing(intake.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            prepared = current_prepared_work(preparation, db, work_id)
            validate_proof(intake.config, source_row(db, prepared['work']['entry']['version_id']), issue_observation, time.time_ns() // 1000)
            result = candidate.create(artifact, prepared, code_observation)
            db.commit()
            return {'candidate': result.receipt(), 'replayed': False}
        except BaseException:
            db.rollback(); raise
